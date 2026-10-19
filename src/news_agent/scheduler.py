from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from filelock import Timeout

from .models import utcnow
from .service import NewsService

logger = logging.getLogger(__name__)


class Scheduler:
    """Single scheduler owner; durable outcomes live in JSON, not APScheduler memory."""

    def __init__(self, service: NewsService):
        self.service = service
        self.owner = None
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def start(self) -> bool:
        self.owner = self.service.store.lock("scheduler-owner", timeout=0)
        try:
            self.owner.__enter__()
        except Timeout:
            self.owner = None
            logger.warning("其他进程已经运行调度器，本进程仅提供 API")
            return False
        self.scheduler.add_job(
            self.tick,
            "interval",
            seconds=60,
            next_run_time=utcnow(),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )
        self.scheduler.start()
        return True

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        if self.owner:
            self.owner.__exit__(None, None, None)
            self.owner = None

    def tick(self) -> None:
        store = self.service.store
        try:
            now = utcnow().astimezone(ZoneInfo("Asia/Shanghai"))
            day = now.date().isoformat()
            preferences = store.preferences()
            schedule = store.schedule()
            state = store.read("collector/scheduler_state.json", {})
            collector = store.read("collector/state.json", {})
            preferences_changed = (
                collector.get("preferences_updated_at") != preferences.updated_at.isoformat()
            )
            backfill = state.get("backfill_day") != day
            due_sources = any(
                source.enabled
                and (
                    not collector.get("sources", {}).get(source.id, {}).get("last_attempt")
                    or utcnow() - datetime.fromisoformat(collector["sources"][source.id]["last_attempt"])
                    >= timedelta(minutes=source.interval_minutes)
                )
                and (
                    not collector.get("sources", {}).get(source.id, {}).get("next_allowed_at")
                    or datetime.fromisoformat(collector["sources"][source.id]["next_allowed_at"]) <= utcnow()
                )
                for source in store.sources().sources
            )
            if backfill or preferences_changed or due_sources:
                result = self.service.run(
                    "collect",
                    due_only=not (backfill or preferences_changed),
                    backfill=backfill or preferences_changed,
                )
                if result["status"] != "skipped":
                    state["backfill_day"] = day
            if now.strftime("%H:%M") >= schedule.cleanup_time and state.get("cleanup_day") != day:
                result = self.service.run("cleanup")
                if result["status"] == "completed":
                    state["cleanup_day"] = day
            store.write("collector/scheduler_state.json", state)
            hour, minute = map(int, preferences.delivery.time.split(":"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if preferences.delivery.daily_enabled and due <= now < due + timedelta(hours=6):
                briefing = self.service.briefing(f"{day}-daily")
                attempts = [
                    run
                    for run in store.records("runs")
                    if run.get("daily")
                    and datetime.fromisoformat(run["created_at"])
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    .isoformat()
                    == day
                    and run.get("status") != "skipped"
                ]
                last_attempt = max((r["created_at"] for r in attempts), default=None)
                ready = not last_attempt or utcnow() - datetime.fromisoformat(last_attempt) >= timedelta(
                    minutes=15
                )
                delivery_failed = bool(
                    briefing
                    and preferences.delivery.email_enabled
                    and briefing.get("delivery", {}).get("status") != "accepted"
                )
                if (not briefing or delivery_failed) and len(attempts) < 3 and ready:
                    self.service.run("generate", daily=True, send=preferences.delivery.email_enabled)
        except Exception:
            logger.exception("调度检查失败，下一轮继续")
