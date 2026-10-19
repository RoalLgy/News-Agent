from datetime import timedelta

from news_agent.collector import gdelt_articles, rss_articles
from news_agent.models import Source, utcnow
from news_agent.news import article_id, candidate_articles, canonical_url, query_plan


def test_canonical_url_deduplicates_tracking_parameters():
    assert article_id("https://example.org/a?utm_source=x&b=2&a=1#top") == article_id(
        "https://example.org/a?a=1&b=2"
    )
    assert (
        canonical_url("https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fexample.org%2Fa")
        == "https://example.org/a"
    )


def test_candidates_enforce_time_exclusions_and_evidence(service, article):
    prefs = service.store.preferences()
    assert candidate_articles([article], prefs, 20)
    old = article.model_copy(update={"published_at": utcnow() - timedelta(days=4)})
    undated = article.model_copy(update={"published_at": None})
    future = article.model_copy(update={"published_at": utcnow() + timedelta(days=1)})
    assert not candidate_articles([old, undated, future], prefs, 20)
    prefs.subscription.exclude_keywords = ["工具调用"]
    assert not candidate_articles([article], prefs, 20)


def test_rss_stores_declared_publication_time():
    payload = b"""<rss version="2.0"><channel><title>News</title><item><title>Test news</title>
      <link>https://example.org/news?utm_source=a</link><pubDate>Mon, 19 Oct 2026 01:00:00 GMT</pubDate>
      <description>Useful article summary.</description><source>Example</source></item></channel></rss>"""
    articles = rss_articles(payload, Source(id="bing", kind="bing_rss", name="Bing"), {"id": "q"}, utcnow())
    assert len(articles) == 1
    assert articles[0].published_at.isoformat() == "2026-10-19T01:00:00+00:00"
    assert articles[0].canonical_url == "https://example.org/news"


def test_gdelt_seen_date_is_not_publication_time():
    import json

    payload = json.dumps(
        {
            "articles": [
                {
                    "url": "https://example.org/a",
                    "title": "News",
                    "language": "Chinese",
                    "domain": "example.org",
                    "seendate": "20261019T010000Z",
                }
            ]
        }
    ).encode()
    articles, truncated = gdelt_articles(
        payload, Source(id="gdelt", kind="gdelt", name="GDELT"), {"id": "q"}, utcnow()
    )
    assert articles[0].published_at is None
    assert articles[0].discoveries[0].provider_seen_at is not None
    assert not truncated


def test_query_plan_is_stable_and_maps_common_chinese(service):
    prefs = service.store.preferences()
    assert query_plan(prefs, 6) == query_plan(prefs, 6)
    assert any(q["gdelt_term"] == "artificial intelligence" for q in query_plan(prefs, 6))


def test_explicit_gdelt_queries_are_not_silently_dropped(service):
    prefs = service.store.preferences()
    prefs.subscription.keywords = [f"关键词{index}" for index in range(10)]
    prefs.subscription.gdelt_keywords = ["BYD"]
    assert any(query["gdelt_term"] == "BYD" for query in query_plan(prefs, 6))
