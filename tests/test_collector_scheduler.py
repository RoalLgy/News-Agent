from datetime import datetime, timezone

import httpx

from news_agent.collector import collect
from news_agent.models import Source, Sources, utcnow
from news_agent.scheduler import Scheduler


def test_collection_is_idempotent_and_partial_failure_isolated(service, monkeypatch):
    import news_agent.collector as module

    prefs = service.store.preferences()
    prefs.subscription.topics = ["体育"]
    prefs.subscription.keywords = []
    service.store.save_preferences(prefs)
    sources = Sources(
        sources=[Source(id="a", kind="bing_rss", name="A"), Source(id="b", kind="google_rss", name="B")]
    )
    service.store.write("config/sources.json", sources.model_dump(mode="json"))
    schedule = service.store.schedule()
    schedule.max_extract_per_run = 0
    service.store.write("config/scheduler.json", schedule.model_dump(mode="json"))
    from email.utils import format_datetime

    payload = f"""<rss version="2.0"><channel><title>News</title><item><title>体育新闻</title>
    <link>https://example.org/sports</link><pubDate>{format_datetime(utcnow())}</pubDate>
    <description>{"体育新闻摘要，足够生成一则简报。" * 8}</description></item></channel></rss>""".encode()

    def fetch(client, url):
        if "google" in url:
            raise httpx.ConnectError("unavailable")
        return payload, url

    monkeypatch.setattr(module, "fetch_bytes", fetch)
    first = collect(service.store, service.settings)
    second = collect(service.store, service.settings)
    assert first["new"] == 1 and second["new"] == 0
    assert len(list(service.store.articles())) == 1
    assert len(service.store.get_article(next(service.store.articles()).id).discoveries) == 1
    assert first["sources"][1]["errors"]
    assert service.store.read("collector/state.json")["preferences_updated_at"]


def test_scheduler_creates_one_daily_and_compensates_restart(service, monkeypatch):
    import news_agent.agent as agent_module
    import news_agent.scheduler as module
    import news_agent.service as service_module

    fixed = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)  # 09:00 Shanghai
    monkeypatch.setattr(module, "utcnow", lambda: fixed)
    monkeypatch.setattr(service_module, "utcnow", lambda: fixed)
    monkeypatch.setattr(agent_module, "utcnow", lambda: fixed)
    monkeypatch.setattr(service_module, "collect", lambda *args, **kwargs: {"new": 0})
    prefs = service.store.preferences()
    prefs.delivery.daily_enabled = True
    prefs.delivery.email_enabled = False
    service.store.save_preferences(prefs)
    scheduler = Scheduler(service)
    scheduler.tick()
    assert service.briefing("2026-10-19-daily")
    Scheduler(service).tick()
    assert len(service.store.records("briefings")) == 1


def test_model_error_message_redacts_credentials(service, monkeypatch):
    import news_agent.service as module
    from news_agent.settings import Settings

    service.settings = Settings(data_dir=service.settings.data_dir, api_key="secret-token")

    def fail(*args, **kwargs):
        raise ValueError("provider error secret-token")

    monkeypatch.setattr(module, "generate", fail)
    result = service.run("generate")
    assert "secret-token" not in result["error"]
    assert "[redacted]" in result["error"]


def test_rate_limit_stops_queries_and_honors_retry_after(service, monkeypatch):
    import news_agent.collector as module
    from news_agent.network import SourceRateLimited

    sources = Sources(sources=[Source(id="bing", kind="bing_rss", name="Bing")])
    service.store.write("config/sources.json", sources.model_dump(mode="json"))
    calls = []

    def throttled(*args, **kwargs):
        calls.append(1)
        raise SourceRateLimited("7200")

    monkeypatch.setattr(module, "fetch_bytes", throttled)
    result = service.run("collect")
    assert result["status"] == "failed"
    assert len(calls) == 1
    state = service.store.read("collector/state.json")["sources"]["bing"]
    assert (datetime.fromisoformat(state["next_allowed_at"]) - utcnow()).total_seconds() > 7100
    # Even a manual collection must not ignore the provider's retry window.
    service.run("collect")
    assert len(calls) == 1
