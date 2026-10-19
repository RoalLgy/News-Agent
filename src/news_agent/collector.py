from __future__ import annotations

import calendar
import hashlib
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import feedparser
import trafilatura
from lxml import html as lxml_html

from .ddgs_search import DDGSSearchError, search_news
from .models import Article, Content, Discovery, Publisher, Source, utcnow
from .network import SourceRateLimited, fetch_bytes, http_client
from .news import article_id, canonical_url, query_plan
from .settings import Settings
from .storage import JsonStore

logger = logging.getLogger(__name__)


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def rss_articles(payload: bytes, source: Source, query: dict, now: datetime) -> list[Article]:
    feed = feedparser.parse(payload)
    if feed.bozo and not feed.entries:
        raise ValueError("来源返回无效 RSS/Atom")
    result = []
    for entry in feed.entries[:100]:
        try:
            url = canonical_url(entry.get("link", ""))
            title = plain_text(entry.get("title", ""))
            if not title:
                continue
            date_tuple = entry.get("published_parsed")
            published = (
                datetime.fromtimestamp(calendar.timegm(date_tuple), timezone.utc) if date_tuple else None
            )
            snippet = plain_text(entry.get("summary", ""))[:6000]
            result.append(
                Article(
                    id=article_id(url),
                    title=title,
                    url=url,
                    canonical_url=url,
                    publisher=Publisher(
                        name=entry.get("source", {}).get("title", ""), domain=urlsplit(url).hostname or ""
                    ),
                    language="zh" if re.search(r"[\u4e00-\u9fff]", title) else "en",
                    published_at=published,
                    published_at_source="rss" if published else "",
                    first_seen_at=now,
                    last_seen_at=now,
                    content=Content(snippet=snippet),
                    discoveries=[
                        Discovery(
                            provider=source.id,
                            query_id=query["id"],
                            result_url=entry.get("link", url),
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                    ],
                )
            )
        except (ValueError, TypeError, OverflowError):
            logger.debug("跳过无效 RSS 条目")
    return result


def gdelt_articles(payload: bytes, source: Source, query: dict, now: datetime) -> tuple[list[Article], bool]:
    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("GDELT 返回格式异常")
    rows = raw.get("articles", [])
    result = []
    for row in rows:
        try:
            url = canonical_url(row["url"])
            seen = parse_date(row.get("seendate"))
            result.append(
                Article(
                    id=article_id(url),
                    title=row["title"],
                    url=url,
                    canonical_url=url,
                    publisher=Publisher(name=row.get("domain", ""), domain=urlsplit(url).hostname or ""),
                    language={"Chinese": "zh", "English": "en"}.get(row.get("language", ""), ""),
                    first_seen_at=now,
                    last_seen_at=now,
                    # GDELT seendate is a discovery time, not a verified publication time.
                    discoveries=[
                        Discovery(
                            provider=source.id,
                            query_id=query["id"],
                            result_url=url,
                            provider_seen_at=seen,
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                    ],
                )
            )
        except (KeyError, ValueError, TypeError):
            logger.debug("跳过无效 GDELT 条目")
    return result, len(rows) >= 250


def extract_article(client, article: Article) -> Article:
    article.content.attempts += 1
    try:
        payload, final_url = fetch_bytes(client, article.canonical_url)
        if urlsplit(final_url).hostname in {"news.google.com", "www.bing.com", "cn.bing.com"}:
            raise ValueError("聚合链接未解析为原文，保留 RSS 摘要")
        extracted = trafilatura.bare_extraction(
            payload,
            url=final_url,
            with_metadata=True,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
        )
        document = extracted.as_dict() if extracted else None
        if not document or len(document.get("text", "")) < 80:
            raise ValueError("未提取到足够正文")
        article.content.text = document["text"][:30000]
        article.content.hash = hashlib.sha256(article.content.text.encode()).hexdigest()
        article.content.status = "extracted"
        article.content.method = "trafilatura"
        article.content.error = ""
        if not article.published_at:
            date = document.get("date")
            # Day-only metadata cannot establish a reliable recent publication time.
            published = parse_date(date)
            if published:
                article.published_at = published
                article.published_at_source = "article_metadata"
        # ISO publication metadata is often more precise than extractor's day-level date.
        if not article.published_at:
            tree = lxml_html.fromstring(payload)
            values = tree.xpath('//meta[@property="article:published_time"]/@content')
            published = parse_date(values[0]) if values else None
            if published:
                article.published_at = published
                article.published_at_source = "article_metadata"
    except Exception as exc:
        article.content.status = "snippet_only" if article.content.snippet else "failed"
        article.content.error = str(exc)[:250]
    return article


def collect(store: JsonStore, settings: Settings, *, due_only: bool = False, backfill: bool = False) -> dict:
    preferences = store.preferences()
    schedule = store.schedule()
    queries = query_plan(preferences, schedule.max_queries)
    store.write("collector/queries.json", {"schema_version": 1, "queries": queries})
    state = store.read("collector/state.json", {"schema_version": 1, "sources": {}})
    now = utcnow()
    stats = {"new": 0, "updated": 0, "extracted": 0, "sources": [], "warnings": []}
    if not queries:
        stats["warnings"].append("订阅关键词和话题均为空，仅采集配置的固定 RSS")
    extraction_ids: list[str] = []
    with http_client(settings.proxy) as client:
        for source in store.sources().sources:
            if not source.enabled:
                continue
            old = state["sources"].get(source.id, {})
            next_allowed = parse_date(old.get("next_allowed_at"))
            if next_allowed and next_allowed > now:
                stats["warnings"].append(f"{source.name} 退避中，将在 {next_allowed.isoformat()} 后重试")
                continue
            last_attempt = parse_date(old.get("last_attempt"))
            if due_only and last_attempt and now - last_attempt < timedelta(minutes=source.interval_minutes):
                continue
            source_stats = {"id": source.id, "requests": 0, "found": 0, "errors": [], "truncated": False}
            source_queries = [{"id": "fixed-rss", "term": ""}] if source.kind == "rss" else queries
            last_request = 0.0
            for query in source_queries:
                if source.kind == "gdelt" and not query.get("gdelt_term"):
                    stats["warnings"].append(f"GDELT 跳过未配置英文检索词的订阅：{query['term']}")
                    continue
                wait = source.request_interval_seconds - (time.monotonic() - last_request)
                if wait > 0:
                    time.sleep(wait)
                try:
                    hours = (
                        min(168, max(24, preferences.subscription.lookback_hours))
                        if backfill or not old
                        else 6
                    )
                    if source.kind == "ddgs":
                        outcome = search_news(
                            source,
                            query,
                            proxy=settings.proxy,
                            hours=hours,
                            languages=preferences.subscription.languages,
                            now=now,
                        )
                        articles = outcome.articles
                        source_stats["requests"] += outcome.requests
                        stats["warnings"].extend(f"{source.name}: {w}" for w in outcome.warnings)
                    else:
                        source_stats["requests"] += 1
                    if source.kind == "bing_rss":
                        url = "https://www.bing.com/news/search?" + urlencode(
                            {"q": query["term"], "format": "rss"}
                        )
                    elif source.kind == "google_rss":
                        chinese = "zh" in preferences.subscription.languages
                        url = "https://news.google.com/rss/search?" + urlencode(
                            {
                                "q": f"{query['term']} when:{hours}h",
                                "hl": "zh-CN" if chinese else "en-US",
                                "gl": "CN" if chinese else "US",
                                "ceid": "CN:zh-Hans" if chinese else "US:en",
                            }
                        )
                    elif source.kind == "gdelt":
                        term = re.sub(r'["\\\r\n]', " ", query["gdelt_term"])
                        language = " OR ".join(
                            "sourcelang:chinese" if v == "zh" else "sourcelang:english"
                            for v in preferences.subscription.languages
                        )
                        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(
                            {
                                "query": f'"{term}" ({language})',
                                "mode": "ArtList",
                                "maxrecords": 250,
                                "timespan": f"{hours}h",
                                "format": "json",
                                "sort": "DateDesc",
                            }
                        )
                    elif source.kind == "rss":
                        url = source.url
                    if source.kind != "ddgs":
                        payload, _ = fetch_bytes(client, url)
                        if source.kind == "gdelt":
                            articles, truncated = gdelt_articles(payload, source, query, now)
                            source_stats["truncated"] |= truncated
                        else:
                            articles = rss_articles(payload, source, query, now)
                    source_stats["found"] += len(articles)
                    for article in articles:
                        if article.published_at and article.published_at < now - timedelta(
                            days=schedule.retention_days
                        ):
                            continue
                        new = store.upsert_article(article)
                        stats["new" if new else "updated"] += 1
                        extraction_ids.append(article.id)
                except Exception as exc:
                    if isinstance(exc, DDGSSearchError):
                        source_stats["requests"] += exc.requests
                    source_stats["errors"].append(str(exc)[:300])
                    if isinstance(exc, SourceRateLimited):
                        source_stats["next_allowed_at"] = (
                            utcnow() + timedelta(seconds=exc.seconds)
                        ).isoformat()
                    # Do not hammer a throttled or unavailable provider with remaining queries.
                    break
                finally:
                    last_request = time.monotonic()
            source_stats["last_attempt"] = now.isoformat()
            if source_stats["requests"] and not source_stats["errors"]:
                source_stats["last_success"] = now.isoformat()
                source_stats["failure_count"] = 0
            elif old.get("last_success"):
                source_stats["last_success"] = old["last_success"]
            if source_stats["errors"]:
                failure_count = old.get("failure_count", 0) + 1
                source_stats["failure_count"] = failure_count
                backoff = timedelta(
                    minutes=min(1440, source.interval_minutes * (2 ** min(failure_count - 1, 4)))
                )
                retry_at = max(utcnow() + backoff, parse_date(source_stats.get("next_allowed_at")) or now)
                source_stats["next_allowed_at"] = retry_at.isoformat()
            if source_stats["truncated"]:
                stats["warnings"].append(f"{source.name} 达到返回条数上限，本轮覆盖不完整")
            state["sources"][source.id] = source_stats
            stats["sources"].append(source_stats)
        # Retry pending articles from earlier runs; cap work independently of search result count.
        pending = sorted(store.articles(), key=lambda a: a.first_seen_at, reverse=True)
        extraction_ids.extend(
            a.id for a in pending if a.content.status != "extracted" and a.content.attempts < 3
        )
        attempted = 0
        for identity in dict.fromkeys(extraction_ids):
            if attempted >= schedule.max_extract_per_run:
                break
            with store.lock(f"article:{identity}"):
                article = store.get_article(identity)
                if not article or article.content.status == "extracted" or article.content.attempts >= 3:
                    continue
                attempted += 1
                article = extract_article(client, article)
                store.write(store.article_path(identity), article.model_dump(mode="json"))
                stats["extracted"] += article.content.status == "extracted"
    state["updated_at"] = utcnow().isoformat()
    state["preferences_updated_at"] = preferences.updated_at.isoformat()
    store.write("collector/state.json", state)
    stats["extraction_attempted"] = attempted
    if stats["sources"] and all(s["errors"] for s in stats["sources"]):
        raise RuntimeError("所有新闻来源均失败：" + "; ".join(s["errors"][0] for s in stats["sources"]))
    return stats
