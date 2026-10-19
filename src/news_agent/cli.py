from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import markdown
from .models import Preferences
from .service import NewsService
from .settings import Settings


def emit(value, json_output: bool) -> None:
    if isinstance(value, str) and not json_output:
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="新闻简报：本地 JSON 新闻简报 Agent")
    cli.add_argument("--data-dir", help="数据目录，也可通过 NEWS_BRIEF_DATA_DIR 指定")
    cli.add_argument("--json", action="store_true", help="输出 JSON，便于 shell 脚本调用")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="初始化默认 JSON 配置")
    commands.add_parser("status", help="查看配置与采集状态")
    prefs = commands.add_parser("prefs", help="查看、导入或修改偏好")
    prefs.add_argument("--file", type=Path, help="导入完整偏好 JSON")
    prefs.add_argument("--name")
    prefs.add_argument("--email")
    prefs.add_argument("--topics", nargs="*")
    prefs.add_argument("--keywords", nargs="*")
    prefs.add_argument("--exclude", nargs="*")
    prefs.add_argument("--gdelt-keywords", nargs="*")
    prefs.add_argument("--items", type=int)
    prefs.add_argument("--hours", type=int)
    prefs.add_argument("--daily", choices=["on", "off"])
    prefs.add_argument("--mail", choices=["on", "off"])
    prefs.add_argument("--time")
    commands.add_parser("collect", help="立即抓取并补充近期新闻")
    generate = commands.add_parser("generate", help="使用 LangChain 从已有新闻生成简报")
    generate.add_argument("--send", action="store_true", help="生成后发送邮件")
    commands.add_parser("demo", help="生成明确标注的虚构演示简报，不调用模型、不抓取、不发邮件")
    news = commands.add_parser("news", help="查询本地新闻")
    news.add_argument("--keyword", default="")
    news.add_argument("--limit", type=int, default=20)
    commands.add_parser("history", help="查看简报历史")
    show = commands.add_parser("show", help="查看某份简报")
    show.add_argument("id")
    send = commands.add_parser("send", help="发送或重试某份正式简报")
    send.add_argument("id")
    commands.add_parser("cleanup", help="清理超过保留期限的新闻与运行文件")
    serve = commands.add_parser("serve", help="启动前端/API及后台调度")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-scheduler", action="store_true")
    worker = commands.add_parser("worker", help="只运行后台定时任务，不启动网页")
    worker.set_defaults(command="worker")
    return cli


def main() -> None:
    args = parser().parse_args()
    settings = Settings.from_env(args.data_dir)
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(
            create_app(settings, enable_scheduler=not args.no_scheduler), host=args.host, port=args.port
        )
        return
    service = NewsService(settings)
    exit_code = 0
    try:
        if args.command == "init":
            emit({"data_dir": str(settings.data_dir), "status": "initialized"}, args.json)
        elif args.command == "status":
            emit(service.status(), args.json)
        elif args.command == "prefs":
            # Lock the full read-modify-write so independent CLI field edits don't overwrite each other.
            with service.store.lock("preferences"):
                raw = (
                    json.loads(args.file.read_text())
                    if args.file
                    else service.store.preferences().model_dump(mode="json")
                )
                for field in ["name", "email"]:
                    if getattr(args, field) is not None:
                        raw["user"][field] = getattr(args, field)
                for arg, field in [
                    ("topics", "topics"),
                    ("keywords", "keywords"),
                    ("exclude", "exclude_keywords"),
                    ("gdelt_keywords", "gdelt_keywords"),
                    ("items", "max_items"),
                    ("hours", "lookback_hours"),
                ]:
                    if getattr(args, arg) is not None:
                        raw["subscription"][field] = getattr(args, arg)
                for arg, field in [("daily", "daily_enabled"), ("mail", "email_enabled")]:
                    if getattr(args, arg) is not None:
                        raw["delivery"][field] = getattr(args, arg) == "on"
                if args.time:
                    raw["delivery"]["time"] = args.time
                # No nested FileLock: commit under the lock already held here.
                value = Preferences.model_validate(raw)
                changed = args.file or any(
                    getattr(args, f) is not None
                    for f in [
                        "name",
                        "email",
                        "topics",
                        "keywords",
                        "exclude",
                        "gdelt_keywords",
                        "items",
                        "hours",
                        "daily",
                        "mail",
                        "time",
                    ]
                )
                if changed:
                    from .models import utcnow

                    value.updated_at = utcnow()
                    service.store.write("config/preferences.json", value.model_dump(mode="json"))
                emit(value.model_dump(mode="json"), args.json)
        elif args.command in {"collect", "generate", "demo", "cleanup"}:
            options = (
                {"send": args.send}
                if args.command == "generate"
                else {"backfill": True}
                if args.command == "collect"
                else {}
            )
            result = service.run(args.command, **options)
            emit(result, args.json)
            exit_code = int(result["status"] != "completed")
        elif args.command == "news":
            articles = sorted(
                service.store.articles(), key=lambda a: a.published_at or a.first_seen_at, reverse=True
            )
            articles = [
                a
                for a in articles
                if args.keyword.casefold() in f"{a.title} {a.content.snippet} {a.content.text}".casefold()
            ]
            emit([a.model_dump(mode="json") for a in articles[: max(1, min(100, args.limit))]], args.json)
        elif args.command == "history":
            emit(
                sorted(
                    [
                        {
                            "id": b["id"],
                            "title": b["title"],
                            "mode": b["mode"],
                            "created_at": b["created_at"],
                            "items": len(b["items"]),
                        }
                        for b in service.store.records("briefings")
                    ],
                    key=lambda b: b["created_at"],
                    reverse=True,
                ),
                args.json,
            )
        elif args.command in {"show", "send"}:
            briefing = service.briefing(args.id)
            if not briefing:
                raise ValueError("简报不存在")
            if args.command == "send":
                result = service.send_briefing(briefing)["delivery"]
                emit(result, args.json)
                exit_code = int(result["status"] != "accepted")
            else:
                emit(briefing if args.json else markdown(briefing), args.json)
        elif args.command == "worker":
            import signal
            import threading

            from .scheduler import Scheduler

            scheduler = Scheduler(service)
            if not scheduler.start():
                raise ValueError("已有其他进程运行调度器")
            stopped = threading.Event()
            for sig in [signal.SIGINT, signal.SIGTERM]:
                signal.signal(sig, lambda *_: stopped.set())
            print("后台调度器已启动，Ctrl+C 停止。", file=sys.stderr)
            try:
                stopped.wait()
            finally:
                scheduler.stop()
    except (ValueError, OSError) as exc:
        emit({"error": str(exc)}, args.json)
        exit_code = 1
    finally:
        service.close()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
