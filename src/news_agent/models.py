from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class User(Model):
    name: str = Field(default="我的订阅", min_length=1, max_length=80)
    email: str = Field(default="", max_length=254)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("请输入有效邮箱，或留空")
        return value


class Subscription(Model):
    topics: list[str] = Field(default_factory=lambda: ["人工智能"], max_length=12)
    keywords: list[str] = Field(default_factory=lambda: ["AI Agent", "开源模型"], max_length=20)
    exclude_keywords: list[str] = Field(default_factory=lambda: ["招聘"], max_length=20)
    gdelt_keywords: list[str] = Field(default_factory=list, max_length=12)
    languages: list[Literal["zh", "en"]] = Field(default_factory=lambda: ["zh", "en"], min_length=1)
    max_items: int = Field(default=8, ge=1, le=20)
    lookback_hours: int = Field(default=48, ge=1, le=168)

    @field_validator("topics", "keywords", "exclude_keywords", "gdelt_keywords")
    @classmethod
    def clean_terms(cls, value: list[str]) -> list[str]:
        result = []
        for term in value:
            term = term.strip()
            if len(term) > 100:
                raise ValueError("单个关键词不得超过 100 字符")
            if term and term.casefold() not in {t.casefold() for t in result}:
                result.append(term)
        return result


class Delivery(Model):
    daily_enabled: bool = False
    email_enabled: bool = False
    time: str = "08:00"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("时间必须为 HH:MM")
        return value


class Preferences(Model):
    schema_version: Literal[1] = 1
    user: User = Field(default_factory=User)
    subscription: Subscription = Field(default_factory=Subscription)
    delivery: Delivery = Field(default_factory=Delivery)
    updated_at: datetime = Field(default_factory=utcnow)


class Source(Model):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    kind: Literal["bing_rss", "google_rss", "gdelt", "rss", "ddgs"]
    name: str = Field(max_length=100)
    enabled: bool = True
    interval_minutes: int = Field(default=60, ge=15, le=1440)
    url: str = ""
    request_interval_seconds: float = Field(default=5, ge=1, le=120)


class Sources(Model):
    schema_version: Literal[1] = 1
    sources: list[Source] = Field(
        default_factory=lambda: [
            Source(id="google", kind="google_rss", name="Google News RSS"),
            Source(id="ddgs", kind="ddgs", name="DDGS 补充搜索", interval_minutes=120),
            Source(id="bing", kind="bing_rss", name="Bing News RSS", enabled=False),
            Source(id="gdelt", kind="gdelt", name="GDELT", interval_minutes=120, enabled=False),
        ],
        max_length=20,
    )

    @field_validator("sources")
    @classmethod
    def unique_ids(cls, sources: list[Source]) -> list[Source]:
        if len({s.id for s in sources}) != len(sources):
            raise ValueError("来源 ID 不能重复")
        for source in sources:
            if source.kind == "rss" and not source.url.startswith(("https://", "http://")):
                raise ValueError("RSS 来源需要 http(s) URL")
        return sources


class Schedule(Model):
    schema_version: Literal[1] = 1
    retention_days: int = Field(default=7, ge=1, le=90)
    run_retention_days: int = Field(default=30, ge=1, le=365)
    cleanup_time: str = "03:00"
    max_queries: int = Field(default=6, ge=1, le=20)
    max_extract_per_run: int = Field(default=30, ge=0, le=100)
    max_candidates: int = Field(default=60, ge=10, le=200)

    @field_validator("cleanup_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        return Delivery.valid_time(value)


class Publisher(Model):
    name: str = ""
    domain: str = ""


class Content(Model):
    snippet: str = ""
    text: str = ""
    status: Literal["pending", "extracted", "snippet_only", "failed"] = "pending"
    method: str = ""
    hash: str = ""
    attempts: int = 0
    error: str = ""


class Discovery(Model):
    provider: str
    query_id: str
    result_url: str
    backend: str = ""
    search_term: str = ""
    published_at_raw: str = ""
    provider_seen_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class Article(Model):
    schema_version: Literal[1] = 1
    id: str
    title: str
    url: str
    canonical_url: str
    publisher: Publisher = Field(default_factory=Publisher)
    language: str = ""
    topics: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    published_at_source: str = ""
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    content: Content = Field(default_factory=Content)
    discoveries: list[Discovery] = Field(default_factory=list)

    @field_validator("published_at", "first_seen_at", "last_seen_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("日期必须包含时区")
        return value


class DraftItem(Model):
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=10, max_length=1400)
    why_it_matters: str = Field(default="", max_length=600)
    article_ids: list[str] = Field(min_length=1, max_length=5)


class Draft(Model):
    title: str = Field(min_length=1, max_length=180)
    overview: str = Field(min_length=1, max_length=2000)
    items: list[DraftItem] = Field(default_factory=list, max_length=20)
