from datetime import timedelta
from email.utils import format_datetime

from ddgs.exceptions import DDGSException
from fastapi.testclient import TestClient

from news_agent import ddgs_search
from news_agent.api import create_app
from news_agent.collector import collect
from news_agent.models import Source, Sources, utcnow
from news_agent.news import candidate_articles
from news_agent.settings import Settings


def row(**changes):
    return {
        "title": "New electric vehicle battery announced",
        "url": "https://example.org/news?utm_source=ddgs",
        "date": utcnow().isoformat(),
        "source": "Example News",
        "body": "A new electric vehicle battery has been announced with details of its expected production. "
        * 2,
        **changes,
    }


def install_fake(monkeypatch, handler):
    calls = []

    class FakeDDGS:
        def __init__(self, **kwargs):
            self.options = kwargs

        def news(self, term, **kwargs):
            calls.append({"term": term, **self.options, **kwargs})
            return handler(term, **kwargs)

    monkeypatch.setattr(ddgs_search, "DDGS", FakeDDGS)
    monkeypatch.setattr(ddgs_search.time, "sleep", lambda _: None)
    return calls


def test_defaults_enable_google_and_ddgs_only():
    sources = Sources().sources
    assert [s.kind for s in sources if s.enabled] == ["google_rss", "ddgs"]


def test_sources_api_round_trip_supports_ddgs_without_resetting_user_settings(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "api"), enable_scheduler=False)
    with TestClient(app) as client:
        sources = client.get("/api/config").json()["sources"]
        assert [s["kind"] for s in sources["sources"] if s["enabled"]] == ["google_rss", "ddgs"]
        ddgs = next(s for s in sources["sources"] if s["kind"] == "ddgs")
        ddgs.update(enabled=False, interval_minutes=240)
        assert client.put("/api/sources", json=sources).status_code == 200
        saved = client.get("/api/config").json()["sources"]
        assert next(s for s in saved["sources"] if s["kind"] == "ddgs") == ddgs


def test_fallback_uses_english_mapping_and_records_actual_backend(monkeypatch):
    def search(term, **kwargs):
        if kwargs["backend"] == "duckduckgo":
            raise DDGSException("request timed out")
        return [row()]

    calls = install_fake(monkeypatch, search)
    result = ddgs_search.search_news(
        Source(id="ddgs", kind="ddgs", name="DDGS"),
        {"id": "q", "term": "新能源汽车", "gdelt_term": "electric vehicle"},
        proxy="http://proxy.example:7890",
        hours=48,
        languages=["zh", "en"],
        now=utcnow(),
    )
    assert [call["backend"] for call in calls] == ["duckduckgo", "yahoo"]
    assert all(call["term"] == "electric vehicle" and call["region"] == "us-en" for call in calls)
    assert calls[0]["proxy"] == "http://proxy.example:7890"
    assert calls[0]["timelimit"] == "w"
    article = result.articles[0]
    assert article.canonical_url == "https://example.org/news"
    assert article.discoveries[0].backend == "yahoo"
    assert article.discoveries[0].query_id == "q"
    assert article.published_at_source == "ddgs:yahoo"
    assert result.requests == 2 and result.warnings


def test_chinese_only_subscription_never_silently_accepts_english(monkeypatch):
    def search(term, **kwargs):
        return [row()] if kwargs["backend"] == "duckduckgo" else [row(title="新能源汽车电池发布")]

    calls = install_fake(monkeypatch, search)
    result = ddgs_search.search_news(
        Source(id="ddgs", kind="ddgs", name="DDGS"),
        {"id": "q", "term": "新能源汽车", "gdelt_term": "electric vehicle"},
        proxy=None,
        hours=6,
        languages=["zh"],
        now=utcnow(),
    )
    assert all(call["term"] == "新能源汽车" and call["region"] == "cn-zh" for call in calls)
    assert result.articles[0].language == "zh"
    assert result.requests == 2


def test_empty_results_are_not_a_transport_failure(monkeypatch):
    def search(*args, **kwargs):
        raise DDGSException("No results found.")

    install_fake(monkeypatch, search)
    result = ddgs_search.search_news(
        Source(id="ddgs", kind="ddgs", name="DDGS"),
        {"id": "q", "term": "test"},
        proxy=None,
        hours=24,
        languages=["en"],
        now=utcnow(),
    )
    assert not result.articles and result.requests == 2
    assert len(result.warnings) == 2


def test_bad_rows_and_unknown_dates_do_not_create_recent_news(service):
    now = utcnow()
    articles = ddgs_search.normalize_results(
        [
            row(date="2 hours ago"),
            row(url="https://example.org/day", date="2026-10-19"),
            row(url="file:///private/news"),
            row(title=""),
            row(date=None, url="https://example.org/no-date"),
        ],
        Source(id="ddgs", kind="ddgs", name="DDGS"),
        {"id": "q"},
        "duckduckgo",
        "test",
        now,
        ["en"],
    )
    assert len(articles) == 3
    assert all(article.published_at is None for article in articles)
    assert articles[0].discoveries[0].published_at_raw == "2 hours ago"
    assert not candidate_articles(articles, service.store.preferences(), 20)


def prepare_collection(service):
    prefs = service.store.preferences()
    prefs.subscription.keywords = []
    prefs.subscription.topics = ["新能源汽车"]
    service.store.save_preferences(prefs)
    sources = Sources(
        sources=[
            Source(id="google", kind="google_rss", name="Google"),
            Source(id="ddgs", kind="ddgs", name="DDGS", interval_minutes=120),
        ]
    )
    service.store.write("config/sources.json", sources.model_dump(mode="json"))
    schedule = service.store.schedule()
    schedule.max_extract_per_run = 0
    service.store.write("config/scheduler.json", schedule.model_dump(mode="json"))


def test_google_and_ddgs_merge_same_url_and_repeat_collection(service, monkeypatch):
    import news_agent.collector as collector

    prepare_collection(service)
    install_fake(monkeypatch, lambda *args, **kwargs: [row()])
    payload = f"""<rss version="2.0"><channel><title>News</title><item>
    <title>新能源汽车新闻</title><link>https://example.org/news?utm_source=google</link>
    <pubDate>{format_datetime(utcnow())}</pubDate><description>新闻摘要</description>
    </item></channel></rss>""".encode()
    monkeypatch.setattr(collector, "fetch_bytes", lambda client, url: (payload, url))
    first = collect(service.store, service.settings)
    second = collect(service.store, service.settings)
    assert first["new"] == 1 and first["updated"] == 1 and second["new"] == 0
    articles = list(service.store.articles())
    assert len(articles) == 1
    assert {d.provider for d in articles[0].discoveries} == {"google", "ddgs"}


def test_ddgs_failure_does_not_lose_google_results_and_enters_backoff(service, monkeypatch):
    import news_agent.collector as collector

    prepare_collection(service)

    def search(*args, **kwargs):
        raise DDGSException("connection timed out")

    calls = install_fake(monkeypatch, search)
    payload = f"""<rss version="2.0"><channel><title>News</title><item>
    <title>新能源汽车新闻</title><link>https://example.org/google</link>
    <pubDate>{format_datetime(utcnow())}</pubDate>
    </item></channel></rss>""".encode()
    monkeypatch.setattr(collector, "fetch_bytes", lambda client, url: (payload, url))
    result = service.run("collect")
    assert result["status"] == "completed" and result["result"]["new"] == 1
    state = service.store.read("collector/state.json")["sources"]["ddgs"]
    assert state["requests"] == 2 and state["errors"]
    assert ddgs_search.datetime.fromisoformat(state["next_allowed_at"]) > utcnow() + timedelta(minutes=100)
    service.run("collect")
    assert len(calls) == 2
