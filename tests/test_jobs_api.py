from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from news_agent.api import create_app
from news_agent.models import utcnow
from news_agent.network import validate_public_url
from news_agent.settings import Settings


def test_cleanup_removes_expired_news_but_preserves_history(service, article):
    run = service.run("demo")
    article.published_at = utcnow() - timedelta(days=10)
    service.store.write(service.store.article_path(article.id), article.model_dump(mode="json"))
    result = service.run("cleanup")
    assert result["result"]["removed_articles"] == 1
    assert service.briefing(run["result"]["briefing_id"])


def test_job_lock_prevents_overlap(service):
    # A separate FileLock instance represents a concurrent process.
    with service.store.lock("job:news"):
        result = service.run("cleanup")
    assert result["status"] == "skipped"


def test_api_preferences_demo_history_and_origin(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "api"), enable_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        prefs = client.get("/api/preferences").json()
        prefs["user"]["name"] = "测试用户"
        prefs["subscription"]["topics"] = ["体育"]
        assert client.put("/api/preferences", json=prefs).status_code == 200
        assert client.get("/api/preferences").json()["user"]["name"] == "测试用户"
        assert client.post("/api/demo", headers={"origin": "https://evil.example"}).status_code == 403
        prefs["subscription"]["max_items"] = 500
        assert client.put("/api/preferences", json=prefs).status_code == 422
        run = client.post("/api/demo").json()
        app.state.service.executor.shutdown(wait=True)
        state = client.get(f"/api/runs/{run['id']}").json()
        assert state["status"] == "completed"
        briefings = client.get("/api/briefings").json()
        assert briefings[0]["mode"] == "demo"
        assert "演示" in client.get(f"/api/briefings/{briefings[0]['id']}/markdown").text


def test_private_network_urls_are_rejected():
    for url in [
        "http://127.0.0.1/a",
        "http://[::1]/",
        "file:///etc/passwd",
        "http://user:pass@example.org",
        "http://example.org:8080",
    ]:
        with pytest.raises((ValueError, OSError)):
            validate_public_url(url)


def test_explicit_proxy_uses_remote_dns_but_still_rejects_private_literals(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local DNS called")),
    )
    validate_public_url("https://example.org/news", resolve_dns=False)
    with pytest.raises(ValueError):
        validate_public_url("http://127.0.0.1/", resolve_dns=False)


def test_packaged_frontend_can_be_selected_with_explicit_path(tmp_path):
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html>News Brief UI</html>")
    app = create_app(Settings(data_dir=tmp_path / "data", frontend_dir=frontend), enable_scheduler=False)
    with TestClient(app) as client:
        assert "News Brief UI" in client.get("/").text


def test_send_retry_keeps_briefing_and_provider_idempotency(service, monkeypatch):
    from news_agent.models import Preferences

    run = service.run("generate")  # Honest empty bulletin, no LLM needed.
    briefing = service.briefing(run["result"]["briefing_id"])
    briefing["preferences_snapshot"]["user"]["email"] = "reader@example.org"
    service.save_briefing(briefing)
    service.settings = Settings(
        data_dir=service.settings.data_dir, resend_key="test-key", mail_from="Daily <news@example.org>"
    )
    calls = []

    def post(self, url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json={"id": "mail-1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", post)
    assert service.send_briefing(briefing)["delivery"]["status"] == "failed"
    prefs = Preferences()
    prefs.user.email = "different@example.org"
    service.store.save_preferences(prefs)
    assert service.send_briefing(briefing)["delivery"]["status"] == "accepted"
    assert len(calls) == 2
    assert calls[0]["headers"]["Idempotency-Key"] == calls[1]["headers"]["Idempotency-Key"]
    assert calls[1]["json"]["to"] == ["reader@example.org"]
    service.send_briefing(briefing)
    assert len(calls) == 2
