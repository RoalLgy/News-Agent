from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Article, Preferences, utcnow

TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}
GLOSSARY = {
    "人工智能": "artificial intelligence",
    "大模型": "large language model",
    "开源模型": "open source AI model",
    "智能体": "AI agent",
    "新能源汽车": "electric vehicle",
    "动力电池": "EV battery",
    "比亚迪": "BYD",
    "特斯拉": "Tesla",
    "半导体": "semiconductor",
    "芯片": "semiconductor chip",
    "财经": "economy",
    "金融": "finance",
    "国际": "world news",
    "体育": "sports",
    "足球": "football",
    "篮球": "basketball",
    "科技": "technology",
    "汽车": "automotive",
    "新能源": "renewable energy",
    "气候变化": "climate change",
    "医疗": "healthcare",
}


def canonical_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("无效新闻链接")
    host = parsed.hostname.lower()
    if host.endswith("bing.com") and parsed.path.endswith("apiclick.aspx"):
        target = dict(parse_qsl(parsed.query)).get("url")
        if target:
            return canonical_url(target)
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    )
    netloc = host
    if parsed.port and parsed.port not in {80, 443}:
        netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", urlencode(query), ""))


def article_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def query_plan(preferences: Preferences, limit: int) -> list[dict]:
    sub = preferences.subscription
    # Reserve room for explicit international search terms instead of silently dropping them.
    reserved = min(len(sub.gdelt_keywords), limit)
    terms = list(dict.fromkeys(sub.keywords + sub.topics))[: limit - reserved]
    queries = []
    for term in terms:
        english = GLOSSARY.get(term) or (term if re.search(r"[A-Za-z]", term) else "")
        queries.append(
            {"id": hashlib.sha256(term.encode()).hexdigest()[:12], "term": term, "gdelt_term": english}
        )
    for term in sub.gdelt_keywords:
        if len(queries) >= limit:
            break
        if term.casefold() not in {q["gdelt_term"].casefold() for q in queries}:
            queries.append(
                {"id": hashlib.sha256(term.encode()).hexdigest()[:12], "term": term, "gdelt_term": term}
            )
    return queries


def candidate_articles(articles: list[Article], preferences: Preferences, limit: int) -> list[Article]:
    now = utcnow()
    cutoff = now - timedelta(hours=preferences.subscription.lookback_hours)
    terms = [term.casefold() for term in preferences.subscription.keywords + preferences.subscription.topics]
    exclusions = [term.casefold() for term in preferences.subscription.exclude_keywords]
    scored = []
    for article in articles:
        if not article.published_at or not cutoff <= article.published_at <= now + timedelta(minutes=10):
            continue
        if article.language and article.language not in preferences.subscription.languages:
            continue
        text = f"{article.title}\n{article.content.snippet}\n{article.content.text}".casefold()
        if any(term in text for term in exclusions):
            continue
        if len(article.content.text or article.content.snippet) < 50:
            continue
        # Discovery relevance also supports translated GDELT queries.
        query_ids = {query["id"] for query in query_plan(preferences, 32)}
        score = sum(3 if term in article.title.casefold() else 1 for term in terms if term in text)
        discovered = any(d.query_id in query_ids for d in article.discoveries)
        if terms and not score and not discovered:
            continue
        scored.append((score, article.published_at, article))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:limit]]
