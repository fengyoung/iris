"""taskpanel.catalog：命令目录与表单 → argv 翻译。"""

from __future__ import annotations

import pytest

from iris.app._cli_main import COMMANDS, build_parser
from iris.taskpanel import catalog as cat
from iris.taskpanel.catalog import CatalogError, build_argv, build_catalog, format_cli


def test_catalog_covers_every_cli_command():
    catalog = build_catalog(refresh=True)
    assert list(catalog) == list(COMMANDS)
    assert set(cat._META) == set(COMMANDS)


def test_every_declared_arg_exists_in_parser():
    dests = {a.dest for a in build_parser()._actions}
    for name, meta in cat._META.items():
        missing = [d for d in meta.args if d not in dests]
        assert not missing, f"{name}: {missing}"
    for name, actions in cat._ACTIONS.items():
        for value, _label, args, _subj in actions:
            assert set(args) <= set(cat._META[name].args), (name, value)


def test_widgets_follow_argparse_types():
    spec = build_catalog()["search"]
    assert spec.arg("top_k").widget == "number"
    assert spec.arg("mode").widget == "select"
    assert spec.arg("query").widget == "text"
    assert build_catalog()["wiki-lint"].arg("fix").widget == "checkbox"
    # 全局参数 --workspace 注入到所有非委托命令
    assert spec.arg("workspace") is not None


def test_sensitive_internal_args_never_exposed():
    for spec in build_catalog().values():
        dests = {a.dest for a in spec.args}
        assert not dests & {
            "project_root",
            "call_source",
            "interactive",
            "pretty",
            "command",
        }


def test_interactive_and_daemon_commands_blocked():
    catalog = build_catalog()
    for name in (
        "feed-setup",
        "asr-corrector",
        "meeting-live-assistant",
        "watch",
        "undercover-game-web",
        "task-panel",
        "secrets-set",
    ):
        assert catalog[name].blocked
        with pytest.raises(CatalogError):
            build_argv(name, {})


def test_build_argv_basic_and_defaults_omitted():
    argv = build_argv("search", {"query": "拍照 3.0", "top_k": "8", "mode": "local"})
    assert argv == ["search", "--query=拍照 3.0", "--top-k=8"]
    assert build_argv("search", {"query": "x", "top_k": 5}) == ["search", "--query=x"]
    assert build_argv("wiki-lint", {"fix": True}) == ["wiki-lint", "--fix"]
    assert build_argv("wiki-lint", {"fix": False}) == ["wiki-lint"]
    assert build_argv("status", {}) == ["status"]


def test_build_argv_rejects_bad_input():
    with pytest.raises(CatalogError, match="未知命令"):
        build_argv("nope", {})
    with pytest.raises(CatalogError, match="不支持参数"):
        build_argv("search", {"project_root": "/tmp"})
    with pytest.raises(CatalogError, match="整数"):
        build_argv("search", {"top_k": "abc"})
    with pytest.raises(CatalogError, match="取值无效"):
        build_argv("search", {"mode": "rm -rf"})
    with pytest.raises(CatalogError, match="非法字符"):
        build_argv("search", {"query": "a\nb"})


def test_value_starting_with_dash_is_safe():
    # --flag=value 形式：值以 - 开头也不会被解析为新选项
    argv = build_argv("search", {"query": "--project-root=/etc"})
    ns = build_parser().parse_args(argv)
    assert ns.query == "--project-root=/etc"
    assert ns.project_root != "/etc"


def test_actions_and_subject():
    argv = build_argv("decisions", {"action": "search", "subject": "产线", "days": 30})
    assert argv == ["decisions", "search", "产线", "--days=30"]
    ns = build_parser().parse_args(argv)
    assert (ns.workspace_action, ns.subject, ns.days) == ("search", "产线", 30)
    assert build_argv("decisions", {}) == ["decisions", "list"]
    with pytest.raises(CatalogError, match="需要目标"):
        build_argv("decisions", {"action": "show"})
    with pytest.raises(CatalogError, match="不支持参数"):
        build_argv("decisions", {"action": "show", "subject": "D1", "reason": "x"})
    with pytest.raises(CatalogError, match="'-'"):
        build_argv("decisions", {"action": "show", "subject": "-x"})
    with pytest.raises(CatalogError, match="不支持子操作"):
        build_argv("search", {"action": "list"})


@pytest.mark.parametrize(
    "command",
    ["trello", "sync-memory", "extract-weekly-reports", "extract-travel-invoice"],
)
def test_delegated_scripts_blocked(command):
    assert build_catalog()[command].blocked
    with pytest.raises(CatalogError):
        build_argv(command, {"raw": "--interactive --project-root=/tmp"})


def test_every_generated_argv_parses():
    parser = build_parser()
    for spec in build_catalog().values():
        if spec.blocked or spec.raw_args:
            continue
        params = {}
        for a in spec.args:
            if a.widget == "checkbox":
                params[a.dest] = True
            elif a.widget == "select":
                params[a.dest] = a.choices[-1]
            elif a.widget == "number":
                params[a.dest] = 3
            else:
                params[a.dest] = "v"
        if spec.actions:
            first = spec.actions[0]
            params = {
                k: v
                for k, v in params.items()
                if k in first["args"] or k == "workspace"
            }
            params["action"] = first["value"]
        parser.parse_args(build_argv(spec.name, params))


def test_format_cli_quotes():
    line = format_cli(["search", "--query=a b"])
    assert line == "python3 scripts/run_cli.py search '--query=a b'"


def test_catalog_payload_shape():
    payload = cat.catalog_payload()
    assert {g["id"] for g in payload["groups"]} >= {
        c["group"] for c in payload["commands"]
    }
    names = [c["name"] for c in payload["commands"]]
    assert "daily-start" in names


@pytest.mark.parametrize("value", ["false", "yes", "garbage", 0, 1, None, [], {}])
def test_checkbox_requires_json_boolean(value):
    with pytest.raises(CatalogError, match="布尔"):
        build_argv("wiki-lint", {"fix": value})


@pytest.mark.parametrize(
    "value", [True, False, 1.5, "1.5", "nan", float("nan"), float("inf"), [], {}]
)
def test_integer_never_truncates(value):
    with pytest.raises(CatalogError, match="整数"):
        build_argv("search", {"top_k": value})


@pytest.mark.parametrize(
    "value", [True, False, "nan", "inf", float("nan"), float("inf"), [], {}]
)
def test_float_requires_finite_number(value):
    with pytest.raises(CatalogError):
        build_argv("deep-eval", {"sample_rate": value})


def test_float_metadata_and_roundtrip():
    arg = build_catalog()["deep-eval"].arg("sample_rate")
    assert arg.widget == "number"
    assert arg.to_dict()["number_type"] == "float"
    assert arg.to_dict()["step"] == "any"
    assert (
        build_parser()
        .parse_args(build_argv("deep-eval", {"sample_rate": 0.25}))
        .sample_rate
        == 0.25
    )


@pytest.mark.parametrize("command", list(COMMANDS))
def test_no_command_accepts_interactive_or_raw(command):
    for params in ({"interactive": True}, {"raw": "--interactive"}):
        with pytest.raises(CatalogError):
            build_argv(command, params)


def test_every_subaction_roundtrip():
    parser = build_parser()
    for name, actions in cat._ACTIONS.items():
        for value, _label, _args, subject in actions:
            params = {"action": value}
            if subject:
                params["subject"] = "target"
            ns = parser.parse_args(build_argv(name, params))
            assert ns.workspace_action == value
            assert not ns.interactive


def test_mapping_drift_fails_closed(monkeypatch):
    monkeypatch.setitem(
        cat._META, "status", cat._Meta("daily", "status", ("missing_dest",))
    )
    with pytest.raises(CatalogError, match="映射"):
        build_catalog(refresh=True)


def test_unexpected_subject_rejected():
    with pytest.raises(CatalogError, match="不接受目标"):
        build_argv("workspace", {"action": "list", "subject": "unexpected"})
