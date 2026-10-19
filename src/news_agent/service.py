from __future__ import annotations

import hashlib
import html
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
from filelock import Timeout

from .agent import generate, markdown
from .collector import collect
from .models import utcnow
from .settings import Settings
from .storage import JsonStore

logger = logging.getLogger(__name__)


class NewsService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = JsonStore(settings.data_dir)
        self.store.initialize()
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="newsbrief")

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def run_path(self, run_id: str) -> str:
        if not run_id or any(c not in "0123456789abcdef-" for c in run_id):
            raise ValueError("无效任务 ID")
        return f"runs/{run_id[:10]}/{run_id}.json"

    def new_run(self, kind: str, *, daily: bool = False) -> dict:
        identity = f"{utcnow().date().isoformat()}-{uuid4().hex}"
        record = {
            "schema_version": 1,
            "id": identity,
            "kind": kind,
            "daily": daily,
            "pid": os.getpid(),
            "status": "queued",
            "created_at": utcnow().isoformat(),
        }
        self.store.write(self.run_path(identity), record)
        return record

    def launch(self, kind: str, **options) -> dict:
        run = self.new_run(kind, daily=options.get("daily", False))
        self.executor.submit(self.execute, run, **options)
        return run

    def run(self, kind: str, **options) -> dict:
        return self.execute(self.new_run(kind, daily=options.get("daily", False)), **options)

    def execute(self, run: dict, **options) -> dict:
        path = self.run_path(run["id"])
        lock_name = "job:news" if run["kind"] in {"collect", "cleanup"} else "job:generate"
        try:
            with self.store.lock(lock_name, timeout=0):
                run.update(status="running", started_at=utcnow().isoformat())
                self.store.write(path, run)
                if run["kind"] == "collect":
                    result = collect(self.store, self.settings, **options)
                elif run["kind"] in {"generate", "demo"}:
                    send = options.pop("send", False)
                    briefing = generate(
                        self.store, self.settings, run["id"], demo=run["kind"] == "demo", **options
                    )
                    if send and run["kind"] != "demo":
                        briefing = self.send_briefing(briefing)
                    result = {
                        "briefing_id": briefing["id"],
                        "items": len(briefing["items"]),
                        "delivery": briefing["delivery"],
                    }
                elif run["kind"] == "cleanup":
                    result = self.cleanup()
                else:
                    raise ValueError("未知任务类型")
                run.update(status="completed", result=result)
        except Timeout:
            run.update(status="skipped", error="同类任务正在执行，请稍后重试")
        except Exception as exc:
            message = str(exc)
            for secret in [self.settings.api_key, self.settings.resend_key, self.settings.proxy]:
                if secret:
                    message = message.replace(secret, "[redacted]")
            run.update(status="failed", error=message[:1500])
            logger.error("任务 %s 失败：%s", run["id"], run["error"])
        finally:
            run["finished_at"] = utcnow().isoformat()
            self.store.write(path, run)
        return run

    def briefing(self, identity: str) -> dict | None:
        if len(identity) > 80 or any(c not in "0123456789abcdef-daily" for c in identity):
            raise ValueError("无效简报 ID")
        return self.store.read(f"briefings/{identity[:10]}/{identity}/briefing.json")

    def save_briefing(self, briefing: dict) -> None:
        identity = briefing["id"]
        path = f"briefings/{identity[:10]}/{identity}"
        self.store.write(f"{path}/briefing.json", briefing)
        self.store.write_text(f"{path}/briefing.md", markdown(briefing))

    def send_briefing(self, briefing: dict) -> dict:
        identity = briefing["id"]
        with self.store.lock(f"delivery:{identity}"):
            briefing = self.briefing(identity) or briefing
            delivery = briefing.get("delivery", {})
            # Retry uses the original recipient and payload, preserving provider idempotency.
            email = delivery.get("recipient") or briefing["preferences_snapshot"]["user"]["email"]
            if delivery.get("status") == "accepted":
                return briefing
            if briefing.get("mode") == "demo":
                raise ValueError("演示简报不发送邮件")
            if not email or not self.settings.resend_key or not self.settings.mail_from:
                briefing["delivery"] = {
                    "status": "failed",
                    "error": "缺少收件邮箱、RESEND_API_KEY 或 MAIL_FROM",
                }
                self.save_briefing(briefing)
                return briefing
            delivery = {"status": "sending", "recipient": email, "attempts": delivery.get("attempts", 0) + 1}
            briefing["delivery"] = delivery
            self.save_briefing(briefing)
            try:
                token = hashlib.sha256(f"{identity}:{email}".encode()).hexdigest()
                with httpx.Client(timeout=25, trust_env=False) as client:
                    response = client.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {self.settings.resend_key}",
                            "Idempotency-Key": token,
                        },
                        json={
                            "from": self.settings.mail_from,
                            "to": [email],
                            "subject": briefing["title"],
                            "text": markdown(briefing),
                            "html": f"<pre style='white-space:pre-wrap;font:15px/1.8 sans-serif'>{html.escape(markdown(briefing))}</pre>",
                        },
                    )
                    response.raise_for_status()
                    delivery.update(
                        status="accepted",
                        provider_id=response.json().get("id"),
                        accepted_at=utcnow().isoformat(),
                    )
            except Exception:
                delivery.update(
                    status="failed", error="邮件服务请求失败，请检查网络、发件域名和服务配置后重试"
                )
            self.save_briefing(briefing)
            return briefing

    def cleanup(self) -> dict:
        schedule = self.store.schedule()
        cutoff = utcnow() - timedelta(days=schedule.retention_days)
        removed = 0
        for article in self.store.articles():
            expires_from = (
                min(article.published_at, article.first_seen_at)
                if article.published_at
                else article.first_seen_at
            )
            if expires_from < cutoff:
                with self.store.lock(f"article:{article.id}"):
                    self.store.path(self.store.article_path(article.id)).unlink(missing_ok=True)
                    removed += 1
        old_runs = utcnow() - timedelta(days=schedule.run_retention_days)
        workspaces = 0
        for run in self.store.records("runs"):
            if run.get("status") in {"queued", "running"}:
                continue
            created = datetime.fromisoformat(run["created_at"])
            if created < old_runs:
                self.store.path(self.run_path(run["id"])).unlink(missing_ok=True)
                workspace = self.store.path(f"workspaces/{run['id']}")
                if workspace.exists():
                    shutil.rmtree(workspace)
                    workspaces += 1
        return {"removed_articles": removed, "removed_workspaces": workspaces}

    def status(self) -> dict:
        articles = list(self.store.articles())
        return {
            "article_count": len(articles),
            "extracted_count": sum(a.content.status == "extracted" for a in articles),
            "dated_count": sum(a.published_at is not None for a in articles),
            "briefing_count": len(self.store.records("briefings")),
            "model_configured": bool(self.settings.api_key),
            "mail_configured": bool(self.settings.resend_key and self.settings.mail_from),
            "collector": self.store.read("collector/state.json", {}),
        }
