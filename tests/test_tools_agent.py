import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from news_agent.agent import generate, validate_draft
from news_agent.agent_tools import build_tools
from news_agent.models import Draft
from news_agent.storage import JsonStore


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def tool_message(name, args, index):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": f"call_{index}", "type": "tool_call"}]
    )


def test_real_langchain_loop_uses_all_five_tools_and_saves_snapshot(service, article):
    draft = {
        "title": "今日 AI 简报",
        "overview": "今天关注开源模型与工具调用进展。",
        "items": [
            {
                "title": "开源模型更新",
                "summary": "该开源模型更新了工具调用能力，提供新的 Agent 使用方式。",
                "why_it_matters": "与用户订阅的 AI Agent 相关。",
                "article_ids": [article.id],
            }
        ],
    }
    model = ToolCallingModel(
        responses=[
            tool_message("list_dir", {"path": "."}, 1),
            tool_message("read_file", {"path": "preferences.json"}, 2),
            tool_message("read_file", {"path": "manifest.json"}, 3),
            tool_message("search_content", {"keyword": "AI", "dir": "candidates"}, 4),
            tool_message("read_file", {"path": f"candidates/{article.id}.json"}, 5),
            tool_message("bash", {"command": "pwd"}, 6),
            tool_message(
                "write_file", {"path": "draft.json", "content": json.dumps(draft, ensure_ascii=False)}, 7
            ),
            AIMessage(content="已完成"),
        ]
    )
    result = generate(service.store, service.settings, "test-run", model=model)
    assert result["agent_used"]
    assert result["tool_call_count"] == 7
    assert result["items"][0]["sources"][0]["url"] == article.canonical_url
    service.store.path(service.store.article_path(article.id)).unlink()
    assert service.briefing(result["id"])["items"][0]["sources"][0]["title"] == article.title


def test_tools_reject_escapes_commands_and_input_mutation(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    store = JsonStore(workspace)
    store.write("preferences.json", {"name": "user"})
    tools = {t.name: t for t in build_tools(workspace, [])}
    with pytest.raises(ValueError):
        tools["read_file"].invoke({"path": "../secret"})
    with pytest.raises(ValueError):
        tools["bash"].invoke({"command": "pwd; cat /etc/passwd"})
    with pytest.raises(ValueError):
        tools["write_file"].invoke({"path": "preferences.json", "content": "{}"})
    with pytest.raises(ValueError):
        tools["write_file"].invoke({"path": "draft.json", "content": "{}"})


def test_invalid_source_and_duplicate_reference_rejected(service, article):
    prefs = service.store.preferences()
    draft = Draft(
        title="简报",
        overview="概览",
        items=[{"title": "新闻", "summary": "这是一段足够长度的测试新闻摘要。", "article_ids": ["made-up"]}],
    )
    with pytest.raises(ValueError, match="候选目录外"):
        validate_draft(draft, [article], prefs)
    draft.items[0].article_ids = [article.id, article.id]
    with pytest.raises(ValueError, match="引用重复"):
        validate_draft(draft, [article], prefs)


def test_missing_model_does_not_fall_back_to_fake_news(service, article):
    result = service.run("generate")
    assert result["status"] == "failed"
    assert "OPENAI_API_KEY" in result["error"]
    assert not service.store.records("briefings")


def test_demo_never_enters_news_archive(service):
    run = service.run("demo")
    result = service.briefing(run["result"]["briefing_id"])
    assert result["mode"] == "demo"
    assert not result["agent_used"]
    assert not list(service.store.articles())
