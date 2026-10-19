from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock
from pydantic import ValidationError

from .models import Article, Preferences, Schedule, Sources, utcnow

logger = logging.getLogger(__name__)


class JsonStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, relative: str | Path) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("路径超出数据目录")
        return path

    @contextmanager
    def lock(self, name: str, timeout: float = 15) -> Iterator[None]:
        # Fixed article lock buckets avoid accumulating one permanent lock file per expired article.
        if name.startswith("article:"):
            name = name[:10]
        token = hashlib.sha256(name.encode()).hexdigest()
        directory = self.path(".locks")
        directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(directory / f"{token}.lock"), timeout=timeout):
            yield

    def read(self, relative: str | Path, default: Any = None) -> Any:
        path = self.path(relative)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def write(self, relative: str | Path, data: Any) -> None:
        self.write_text(relative, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def write_text(self, relative: str | Path, text: str) -> None:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def initialize(self) -> None:
        for directory in ["config", "collector", "news", "briefings", "runs", "workspaces"]:
            self.path(directory).mkdir(parents=True, exist_ok=True)
        with self.lock("initialize"):
            for name, model in [
                ("preferences", Preferences()),
                ("sources", Sources()),
                ("scheduler", Schedule()),
            ]:
                if not self.path(f"config/{name}.json").exists():
                    self.write(f"config/{name}.json", model.model_dump(mode="json"))

    def preferences(self) -> Preferences:
        return Preferences.model_validate(self.read("config/preferences.json"))

    def save_preferences(self, preferences: Preferences) -> Preferences:
        with self.lock("preferences"):
            preferences = preferences.model_copy(update={"updated_at": utcnow()})
            self.write("config/preferences.json", preferences.model_dump(mode="json"))
        return preferences

    def sources(self) -> Sources:
        return Sources.model_validate(self.read("config/sources.json"))

    def schedule(self) -> Schedule:
        return Schedule.model_validate(self.read("config/scheduler.json"))

    def article_path(self, article_id: str) -> str:
        if len(article_id) != 64 or any(c not in "0123456789abcdef" for c in article_id):
            raise ValueError("无效新闻 ID")
        return f"news/{article_id[:2]}/{article_id}.json"

    def get_article(self, article_id: str) -> Article | None:
        raw = self.read(self.article_path(article_id))
        return Article.model_validate(raw) if raw else None

    def upsert_article(self, article: Article) -> bool:
        with self.lock(f"article:{article.id}"):
            existing = self.get_article(article.id)
            if existing:
                existing.last_seen_at = article.last_seen_at
                if article.published_at and not existing.published_at:
                    existing.published_at = article.published_at
                    existing.published_at_source = article.published_at_source
                if len(article.content.snippet) > len(existing.content.snippet):
                    existing.content.snippet = article.content.snippet
                if article.content.status == "extracted":
                    existing.content = article.content
                if article.publisher.name:
                    existing.publisher = article.publisher
                known = {(d.provider, d.query_id): d for d in existing.discoveries}
                for discovery in article.discoveries:
                    key = (discovery.provider, discovery.query_id)
                    if key in known:
                        known[key].last_seen_at = discovery.last_seen_at
                        if discovery.backend:
                            known[key].backend = discovery.backend
                            known[key].search_term = discovery.search_term
                            known[key].published_at_raw = discovery.published_at_raw
                    else:
                        known[key] = discovery
                existing.discoveries = list(known.values())
                existing.topics = sorted(set(existing.topics + article.topics))
                article = existing
            self.write(self.article_path(article.id), article.model_dump(mode="json"))
            return existing is None

    def articles(self) -> Iterator[Article]:
        for path in sorted(self.path("news").glob("*/*.json")):
            try:
                raw = self.read(path.relative_to(self.root))
                if raw:
                    yield Article.model_validate(raw)
            except (ValueError, ValidationError, OSError):
                logger.warning("跳过损坏新闻文件 %s", path.name)

    def records(self, directory: str) -> list[dict]:
        result = []
        for path in self.path(directory).rglob("*.json"):
            try:
                value = self.read(path.relative_to(self.root))
                if isinstance(value, dict):
                    result.append(value)
            except (ValueError, OSError):
                logger.warning("跳过损坏记录 %s", path.name)
        return result
