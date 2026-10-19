from concurrent.futures import ThreadPoolExecutor

import pytest

from news_agent.models import Discovery
from news_agent.storage import JsonStore


def test_atomic_merge_preserves_discoveries(service, article):
    def write(index):
        other = article.model_copy(deep=True)
        other.discoveries = [Discovery(provider=f"source-{index}", query_id="q", result_url=article.url)]
        JsonStore(service.store.root).upsert_article(other)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(16)))
    result = service.store.get_article(article.id)
    assert len(result.discoveries) == 17
    assert result.first_seen_at == article.first_seen_at
    assert len(list(service.store.articles())) == 1


def test_corrupt_article_does_not_block_valid_data(service, article):
    service.store.write_text("news/ff/broken.json", "{broken")
    assert [a.id for a in service.store.articles()] == [article.id]


def test_storage_rejects_path_traversal_and_symlinks(service, tmp_path):
    with pytest.raises(ValueError):
        service.store.write("../outside.json", {})
    outside = tmp_path / "outside"
    outside.mkdir()
    service.store.path("escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        service.store.write("escape/secret.json", {})


def test_repeated_initialization_preserves_preferences(service):
    prefs = service.store.preferences()
    prefs.user.name = "我的名字"
    service.store.save_preferences(prefs)
    service.store.initialize()
    assert service.store.preferences().user.name == "我的名字"
