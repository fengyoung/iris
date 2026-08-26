"""daily-start 维护阶段的失败可观测性测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from iris.app.cli._handlers._system import _daily_wiki_maintenance


def test_graph_failure_is_returned_in_daily_result(tmp_path):
    bundle = SimpleNamespace(root=tmp_path, wiki={"wiki_root": str(tmp_path / "wiki")})
    enrich_result = SimpleNamespace(updated=0, not_found=0, ambiguous=0, no_change=0)

    with (
        patch("iris.wiki.generator.WikiGenerator") as generator_cls,
        patch("iris.wiki.person_enricher.PersonEnricher") as enricher_cls,
        patch("iris.wiki.graph.WikiGraph", side_effect=RuntimeError("graph unavailable")),
        patch("iris.wiki.WikiNavigationBuilder") as navigation_cls,
        patch("iris.wiki.append_changelog"),
    ):
        generator_cls.return_value.update_all_pages.return_value = {"status": "ok"}
        enricher_cls.return_value.enrich.return_value = enrich_result
        navigation_cls.return_value = MagicMock()

        _, _, graph_result = _daily_wiki_maintenance(bundle, [])

    assert graph_result == {"status": "error", "reason": "graph unavailable"}
