"""CLI 埋点保持原 handler 的输出、返回码与异常。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iris.app._cli_main import build_parser
from iris.app.cli._handlers import _content, _data, _feed, _wiki
from iris.taskpanel.store import read_current_all, read_history


CASES = [
    (
        "feed-collect",
        _feed.handle_feed_collect,
        "iris.app.cli._handlers._feed.FeedPipeline",
    ),
    (
        "feishu-doc-convert",
        _content.handle_feishu_doc_convert,
        "iris.feishu.doc_convert.FeishuDocConverter",
    ),
    (
        "chat-digest",
        _content.handle_chat_digest,
        "iris.feishu.chat_digest.ChatDigester",
    ),
    ("build-graph", _data.handle_build_graph, "iris.wiki.WikiGraph"),
    ("deep-eval", _wiki.handle_deep_eval, "iris.evaluation.DeepEvaluator"),
]


@pytest.mark.parametrize("name,handler,service_path", CASES)
@pytest.mark.parametrize("outcome", ["success", "nonzero", "exception"])
def test_handler_observability_preserves_cli(
    name, handler, service_path, outcome, tmp_path, monkeypatch, capsys
):
    bundle = SimpleNamespace(
        root=tmp_path, wiki={"wiki_root": "wiki"}, default_source_path=None
    )
    args = build_parser().parse_args([name])
    service = Mock()
    monkeypatch.setattr(service_path, Mock(return_value=service))
    monkeypatch.setattr(_feed, "LLMService", Mock())
    if name == "feed-collect":
        service.run.return_value = SimpleNamespace(empty_reason="empty")
        if outcome == "nonzero":
            args.since = "invalid"
    elif name == "feishu-doc-convert":
        args.url = "https://example.test/doc"
        service.convert_batch.return_value = [
            {"status": "error" if outcome == "nonzero" else "success"}
        ]
    elif name == "chat-digest":
        args.group = "group"
        service.digest.return_value = {
            "status": "error" if outcome == "nonzero" else "success"
        }
    elif name == "build-graph":
        service.refresh.return_value = (
            {"llm_error": "failed"} if outcome == "nonzero" else {}
        )
    else:
        result = SimpleNamespace(
            overall_accuracy_rate=None,
            total_references=0,
            consistent_count=0,
            inconsistent_count=0,
            unverifiable_count=0,
            source_missing_count=0,
            total_gaps=0,
            pages_with_gaps=0,
        )
        service.evaluate.return_value = result
        monkeypatch.setattr(
            "iris.evaluation.deep_eval_result_to_json", lambda result: {"ok": True}
        )
        if outcome == "nonzero":
            bundle.wiki = {}
    if outcome == "exception":
        monkeypatch.setattr(
            service_path, Mock(side_effect=RuntimeError("service failed"))
        )

    # 未包装实现作为 CLI 兼容基线，不写任务记录。
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="service failed"):
            handler.__wrapped__(args, bundle, Mock())
        expected_code = None
    else:
        expected_code = handler.__wrapped__(args, bundle, Mock())
    expected_output = capsys.readouterr()
    assert not read_history(tmp_path / "data")

    if outcome == "exception":
        with pytest.raises(RuntimeError, match="service failed"):
            handler(args, bundle, Mock())
    else:
        assert (
            handler(args, bundle, Mock())
            == expected_code
            == (1 if outcome == "nonzero" else 0)
        )
    assert capsys.readouterr() == expected_output
    assert read_current_all(tmp_path / "data") == []
    history = read_history(tmp_path / "data")
    assert len(history) == 1
    assert history[0].name == name
    assert history[0].phase
    assert history[0].status == ("success" if outcome == "success" else "failed")
    if outcome == "nonzero":
        assert history[0].error == "CLI returned 1"
