from __future__ import annotations

import json
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from .storage import JsonStore


def build_tools(workspace: Path, audit: list[dict]):
    storage = JsonStore(workspace)

    def record(name: str, details: dict) -> None:
        if len(audit) >= 40:
            raise ValueError("本次任务已达到 40 次工具调用上限")
        audit.append({"tool": name, **details})

    @tool
    def list_dir(path: str = ".") -> str:
        """列出任务工作目录中的文件；path 是相对路径，例如 candidates。"""
        directory = storage.path(path)
        if not directory.is_dir():
            raise ValueError("目录不存在")
        rows = [
            {"name": p.name, "type": "directory" if p.is_dir() else "file"}
            for p in sorted(directory.iterdir())[:220]
        ]
        record("list_dir", {"path": path, "count": len(rows)})
        return json.dumps(rows, ensure_ascii=False)

    @tool
    def read_file(path: str) -> str:
        """读取 UTF-8 文件，例如 preferences.json 或 candidates/新闻ID.json；最多返回 36000 字符。"""
        file = storage.path(path)
        if not file.is_file() or file.stat().st_size > 1_000_000:
            raise ValueError("文件不存在或超过大小限制")
        content = file.read_text(encoding="utf-8")
        record("read_file", {"path": path})
        return content[:36000]

    @tool
    def search_content(keyword: str, dir: str = "candidates") -> str:
        """在目录下按关键词搜索 JSON 新闻的标题、主题、摘要和正文，返回最多 30 个文件及片段。"""
        if not keyword.strip() or len(keyword) > 100:
            raise ValueError("关键词长度必须为 1 到 100")
        directory = storage.path(dir)
        if not directory.is_dir():
            raise ValueError("目录不存在")
        result = []
        for file in sorted(directory.rglob("*.json"))[:220]:
            safe_file = storage.path(file.relative_to(workspace))
            if safe_file.stat().st_size > 1_000_000:
                continue
            raw = json.loads(safe_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "content" in raw:
                content = raw.get("content", {})
                text = "\n".join(
                    [
                        raw.get("title", ""),
                        " ".join(raw.get("topics", [])),
                        content.get("snippet", ""),
                        content.get("text", ""),
                    ]
                )
            else:
                text = json.dumps(raw, ensure_ascii=False)
            index = text.casefold().find(keyword.casefold())
            if index >= 0:
                result.append(
                    {
                        "path": str(file.relative_to(workspace)),
                        "snippet": text[max(0, index - 60) : index + 400],
                    }
                )
            if len(result) >= 30:
                break
        record("search_content", {"keyword": keyword, "count": len(result)})
        return json.dumps(result, ensure_ascii=False)

    @tool
    def write_file(path: str, content: str) -> str:
        """写入草稿 draft.json 或 notes/ 下的分析笔记。不能修改候选新闻或偏好。"""
        target = storage.path(path)
        relative = target.relative_to(workspace).as_posix()
        if relative != "draft.json" and not relative.startswith("notes/"):
            raise ValueError("只允许写入 draft.json 或 notes/ 目录")
        if len(content.encode()) > 300_000:
            raise ValueError("写入内容过大")
        if relative == "draft.json":
            from .models import Draft

            Draft.model_validate_json(content)
        storage.write_text(relative, content)
        record("write_file", {"path": relative, "characters": len(content)})
        return "写入成功"

    @tool
    def bash(command: str) -> str:
        """通过 bash 执行受控 shell 命令。允许：pwd、ls、ls -la、wc -c draft.json。"""
        if command not in {"pwd", "ls", "ls -la", "wc -c draft.json"}:
            raise ValueError("命令不在允许列表中")
        result = subprocess.run(
            command,
            shell=True,
            executable="/bin/bash",
            cwd=workspace,
            env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        record("bash", {"command": command, "returncode": result.returncode})
        return (result.stdout + result.stderr)[:5000]

    return [list_dir, read_file, search_content, write_file, bash]
