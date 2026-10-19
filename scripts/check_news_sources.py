"""Probe news endpoints without changing collection state or saving articles."""

from __future__ import annotations

import argparse
import json
import re
from time import monotonic
from urllib.parse import urlencode, urlsplit

import feedparser
import httpx

from news_agent.ddgs_search import search_news
from news_agent.models import Source, utcnow
from news_agent.network import fetch_bytes, http_client
from news_agent.news import GLOSSARY
from news_agent.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="独立测试新闻接口，不保存新闻、不修改退避状态")
    parser.add_argument("--keyword", default="AI Agent", help="RSS 查询词")
    parser.add_argument("--gdelt-keyword", help="DDGS / GDELT 英文查询词，默认映射 RSS 查询词")
    parser.add_argument(
        "--source", choices=["bing", "bing-cn", "google", "gdelt", "ddgs", "all"], default="all"
    )
    parser.add_argument("--direct", action="store_true", help="不显式使用代理；系统 TUN 仍可能影响路由")
    parser.add_argument("--timeout", type=float, default=30, help="单次网络读写超时秒数")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout 必须大于 0")

    settings = Settings.from_env()
    proxy = None if args.direct else settings.proxy
    english = (
        args.gdelt_keyword
        or GLOSSARY.get(args.keyword)
        or (args.keyword if re.search(r"[A-Za-z]", args.keyword) else "")
    )
    term = (english or args.keyword).replace('"', " ").replace("\\", " ")
    rss_query = urlencode({"q": args.keyword, "format": "rss"})
    urls = {
        "bing": "https://www.bing.com/news/search?" + rss_query,
        "bing-cn": "https://cn.bing.com/news/search?" + rss_query,
        "google": "https://news.google.com/rss/search?"
        + urlencode({"q": args.keyword + " when:1d", "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}),
        "gdelt": "https://api.gdeltproject.org/api/v2/doc/doc?"
        + urlencode(
            {
                "query": f'"{term}" (sourcelang:chinese OR sourcelang:english)',
                "mode": "ArtList",
                "maxrecords": 10,
                "timespan": "24h",
                "format": "json",
                "sort": "DateDesc",
            }
        ),
    }
    if args.source == "all":
        selected = list(urls.items())
    elif args.source == "ddgs":
        selected = []
    else:
        selected = [(args.source, urls[args.source])]
    report = {
        "mode": "explicit_proxy" if proxy else "no_explicit_proxy",
        "proxy_endpoint": None,
        "results": [],
    }
    if proxy:
        endpoint = urlsplit(proxy)
        report["proxy_endpoint"] = f"{endpoint.scheme}://{endpoint.hostname}:{endpoint.port}"

    with http_client(proxy) as client:
        client.timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 15))
        for name, url in selected:
            result = {"source": name, "url": url, "ok": False}
            payload = None

            def capture(response: httpx.Response) -> None:
                result.update(
                    status=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    final_url=str(response.url),
                )

            client.event_hooks["response"] = [capture]
            started = monotonic()
            try:
                payload, _ = fetch_bytes(client, url)
                result["bytes"] = len(payload)
                if name == "gdelt":
                    data = json.loads(payload)
                    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
                        raise ValueError("返回 JSON 不包含 articles 数组")
                    result["items"] = len(data["articles"])
                    result["sample_titles"] = [row.get("title", "") for row in data["articles"][:3]]
                else:
                    feed = feedparser.parse(payload)
                    if feed.bozo and not feed.entries:
                        raise ValueError(f"无效 RSS/Atom：{feed.get('bozo_exception', '')}")
                    if not feed.version and not feed.entries:
                        raise ValueError("响应无法识别为 RSS/Atom")
                    result["items"] = len(feed.entries)
                    result["sample_titles"] = [row.get("title", "") for row in feed.entries[:3]]
                result["ok"] = True
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
                if payload is not None:
                    result["body_preview"] = payload[:400].decode("utf-8", errors="replace")
            finally:
                result["seconds"] = round(monotonic() - started, 2)
                report["results"].append(result)

    if args.source in {"ddgs", "all"}:
        started = monotonic()
        result = {"source": "ddgs", "ok": False}
        try:
            outcome = search_news(
                Source(id="ddgs", kind="ddgs", name="DDGS"),
                {"id": "diagnostic", "term": args.keyword, "gdelt_term": english},
                proxy=proxy,
                hours=24,
                languages=["zh", "en"],
                now=utcnow(),
                timeout=args.timeout,
            )
            result.update(
                ok=True,
                items=len(outcome.articles),
                requests=outcome.requests,
                warnings=outcome.warnings,
                sample_titles=[a.title for a in outcome.articles[:3]],
                backends=sorted({d.backend for a in outcome.articles for d in a.discoveries}),
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["seconds"] = round(monotonic() - started, 2)
        report["results"].append(result)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if proxy:
        output = output.replace(proxy, "[redacted-proxy]")
    print(output)
    if not any(result["ok"] for result in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
