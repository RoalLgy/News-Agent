from datetime import timedelta

import pytest

from news_agent.models import Article, Content, Discovery, Publisher, utcnow
from news_agent.news import article_id, canonical_url, query_plan
from news_agent.service import NewsService
from news_agent.settings import Settings


@pytest.fixture
def service(tmp_path):
    value = NewsService(Settings(data_dir=tmp_path / "data"))
    yield value
    value.close()


@pytest.fixture
def article(service):
    url = canonical_url("https://example.org/ai-model?utm_source=rss")
    now = utcnow()
    query = query_plan(service.store.preferences(), 6)[0]
    value = Article(
        id=article_id(url),
        title="AI Agent 发布新的开源模型",
        url=url,
        canonical_url=url,
        publisher=Publisher(name="Example", domain="example.org"),
        language="zh",
        published_at=now - timedelta(hours=1),
        published_at_source="rss",
        content=Content(
            snippet="AI Agent 的一个开源模型更新，增加了工具调用能力。" * 8, status="snippet_only"
        ),
        discoveries=[Discovery(provider="bing", query_id=query["id"], result_url=url)],
    )
    service.store.upsert_article(value)
    return value
