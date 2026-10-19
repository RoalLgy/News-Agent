from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .models import Article, Content, Discovery, Publisher, Source
from .news import article_id, canonical_url


@dataclass
class SearchOutcome:
    articles: list[Article] = field(default_factory=list)
    requests: int = 0
    warnings: list[str] = field(default_factory=list)


class DDGSSearchError(ValueError):
    def __init__(self, outcome: SearchOutcome):
        self.requests = outcome.requests
        super().__init__("DDGS 后端均请求失败：" + "; ".join(outcome.warnings))


def normalize_results(
    rows: list[dict],
    source: Source,
    query: dict,
    backend: str,
    term: str,
    now: datetime,
    languages: list[str],
) -> list[Article]:
    articles = []
    for row in rows[:20]:
        try:
            title = re.sub(r"\s+", " ", row.get("title", "")).strip()
            if not title:
                continue
            language = "zh" if re.search(r"[\u4e00-\u9fff]", title) else "en"
            if language not in languages:
                continue
            url = canonical_url(row["url"])
            raw_date = str(row.get("date") or "")
            # Keep the provider's value; never substitute the collection time for publication.
            try:
                published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                published = published.astimezone(timezone.utc) if published.tzinfo else None
            except ValueError:
                published = None
            articles.append(
                Article(
                    id=article_id(url),
                    title=title,
                    url=url,
                    canonical_url=url,
                    publisher=Publisher(
                        name=str(row.get("source") or ""), domain=urlsplit(url).hostname or ""
                    ),
                    language=language,
                    published_at=published,
                    published_at_source=f"ddgs:{backend}" if published else "",
                    first_seen_at=now,
                    last_seen_at=now,
                    content=Content(snippet=str(row.get("body") or "")[:6000]),
                    discoveries=[
                        Discovery(
                            provider=source.id,
                            query_id=query["id"],
                            result_url=row["url"],
                            backend=backend,
                            search_term=term,
                            published_at_raw=raw_date,
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                    ],
                )
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return articles


def search_news(
    source: Source,
    query: dict,
    *,
    proxy: str | None,
    hours: int,
    languages: list[str],
    now: datetime,
    timeout: float = 20,
) -> SearchOutcome:
    # Google covers Chinese; DDGS can use the existing English mappings for supplemental coverage.
    english = query.get("gdelt_term") if "en" in languages else ""
    term = english or query["term"]
    region = "us-en" if english or "zh" not in languages else "cn-zh"
    timelimit = "d" if hours <= 24 else "w"
    outcome = SearchOutcome()
    had_empty_response = False
    for index, backend in enumerate(("duckduckgo", "yahoo")):
        if index:
            time.sleep(source.request_interval_seconds)
        outcome.requests += 1
        try:
            rows = DDGS(proxy=proxy, timeout=timeout).news(
                term,
                region=region,
                timelimit=timelimit,
                max_results=20,
                backend=backend,
            )
            outcome.articles = normalize_results(rows, source, query, backend, term, now, languages)
            if outcome.articles:
                return outcome
            had_empty_response = True
            outcome.warnings.append(f"{backend} 未返回符合语言和格式要求的新闻")
        except Exception as exc:
            message = str(exc).replace(proxy, "[redacted-proxy]") if proxy else str(exc)
            if isinstance(exc, DDGSException) and "No results found" in message:
                had_empty_response = True
            outcome.warnings.append(f"{backend}: {message[:250]}")
    if not had_empty_response:
        raise DDGSSearchError(outcome)
    return outcome
