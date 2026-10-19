import json
import socket
import subprocess
import sys

import httpx
import pytest

from news_agent.collector import extract_article
from news_agent.models import utcnow
from news_agent.network import SourceRateLimited, fetch_bytes


def public_dns(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


def test_stream_fetch_checks_redirects_and_size(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    def handler(request):
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, content=b"x" * 100)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="私有"):
            fetch_bytes(client, "https://example.org/redirect")
        with pytest.raises(ValueError, match="大小"):
            fetch_bytes(client, "https://example.org/large", max_bytes=10)
        payload, url = fetch_bytes(client, "https://example.org/ok")
        assert len(payload) == 100 and url.endswith("/ok")


def test_http_429_preserves_retry_after(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "7200"}))
    ) as client:
        with pytest.raises(SourceRateLimited) as exc:
            fetch_bytes(client, "https://example.org/news")
        assert exc.value.seconds == 7200


def test_real_extractor_reads_body_and_publication_metadata(article, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    now = utcnow().isoformat()
    paragraphs = "".join(
        f"<p>News paragraph {i}. The company announced an open source model with tool calling support. "
        "The release includes documentation and examples for developers to evaluate the system.</p>"
        for i in range(8)
    )
    payload = f'''<!doctype html><html><head><title>New model release</title>
    <meta property="article:published_time" content="{now}" />
    </head><body><main><article><h1>New model release</h1>{paragraphs}</article></main></body></html>'''.encode()
    article.published_at = None
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    ) as client:
        result = extract_article(client, article)
    assert result.content.status == "extracted"
    assert "tool calling" in result.content.text
    assert result.published_at is not None
    assert result.published_at_source == "article_metadata"


def test_cli_persists_preferences_and_demo_round_trip(tmp_path):
    root = tmp_path / "cli-data"

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-m", "news_agent.cli", "--data-dir", str(root), "--json", *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    prefs = run("prefs", "--name", "命令行用户", "--topics", "体育", "--keywords", "足球", "--items", "3")
    assert prefs["user"]["name"] == "命令行用户"
    assert run("prefs")["subscription"]["topics"] == ["体育"]
    result = run("demo")
    briefing = run("show", result["result"]["briefing_id"])
    assert briefing["mode"] == "demo"
    assert briefing["preferences_snapshot"]["user"]["name"] == "命令行用户"
