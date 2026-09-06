"""llm-bench 基准引擎与命令注册单测（离线，不触网）。"""

from types import SimpleNamespace

import pytest

from iris.llm.benchmark import (
    CHARS_PER_TOKEN,
    ModelSpec,
    BenchResult,
    _fill_metrics,
    collect_specs,
    filter_specs,
    render_rows,
    row_to_dict,
)


def _spec(model_id: str = "m1", channel: str = "zz_tokenhub",
          role: str = "adv_model", multimodal: bool = True) -> ModelSpec:
    return ModelSpec(role=role, model_id=model_id, channel=channel,
                     provider="zz_tokenhub", model=model_id,
                     api_base="https://x/v1", api_key="k", multimodal=multimodal,
                     cfg_max_tokens=8192)


# ── 命令注册（不触网） ──────────────────────────────────


def test_llm_bench_registered_as_cli_command():
    from iris.app.cli.handlers import COMMAND_HANDLERS
    assert "llm-bench" in COMMAND_HANDLERS


def test_llm_bench_in_commands_list():
    from iris.app._cli_main import COMMANDS
    assert "llm-bench" in COMMANDS


def test_llm_bench_parser_flags():
    from iris.app._cli_main import build_parser
    ns = build_parser().parse_args([
        "llm-bench", "--channel", "deepseek",
        "--bench-model", "deepseek-v4-flash",
        "--concurrency", "2", "--max-tokens", "500", "--phase1-only",
    ])
    assert ns.command == "llm-bench"
    assert ns.channel == "deepseek"
    assert ns.bench_model == ["deepseek-v4-flash"]
    assert ns.concurrency == 2
    assert ns.max_tokens == 500
    assert ns.phase1_only is True


# ── 模型盘点 / 过滤 ─────────────────────────────────────


def test_collect_specs_flat_enumerate():
    cfg = {
        "models": {
            "base_model": {"models": {
                "a": {"channel": "zz_tokenhub", "model": "a", "multimodal": False,
                      "max_tokens": 8192},
            }},
            "adv_model": {"models": {
                "b": {"channel": "deepseek", "model": "b", "multimodal": True,
                      "max_tokens": 8192},
                "c": {"channel": "deepseek", "model": "c", "multimodal": True,
                      "max_tokens": 8192},
            }},
        }
    }
    bundle = SimpleNamespace(llm=cfg)
    specs = collect_specs(bundle)
    ids = {(s.role, s.model_id) for s in specs}
    assert ids == {("base_model", "a"), ("adv_model", "b"), ("adv_model", "c")}
    assert specs[0].model_id == "a" or specs[0].model_id in ("a", "b", "c")


def test_filter_specs_by_channel_and_model():
    specs = [_spec("m1", "zz_tokenhub"), _spec("m2", "deepseek"), _spec("m3", "bailian")]
    assert [s.model_id for s in filter_specs(specs, channel="deepseek")] == ["m2"]
    got = filter_specs(specs, model_ids=["m1", "m3"])
    assert [s.model_id for s in got] == ["m1", "m3"]
    assert filter_specs(specs, channel="deepseek", model_ids=["m2"])[0].model_id == "m2"
    assert filter_specs(specs, channel="deepseek", model_ids=["m1"]) == []


# ── 指标计算（纯函数） ─────────────────────────────────


def test_fill_metrics_formula():
    r = BenchResult(spec=_spec(), ok=True, content_chars=900,
                    p2_gen_s=10.0, p2_total_s=12.0, ttft_s=2.0)
    _fill_metrics(r)
    assert r.chars_per_min == pytest.approx(900 / 10 * 60)       # 5400
    assert r.tpm_est == pytest.approx(5400 / CHARS_PER_TOKEN)    # 3600
    # 端到端含首 token 延迟（900/12*60/1.5 = 3000）
    assert r.tpm_eff == pytest.approx(3000)
    assert r.tpm_eff < r.tpm_est


def test_fill_metrics_zero_content_noop():
    r = BenchResult(spec=_spec(), ok=True, content_chars=0, p2_gen_s=10.0)
    _fill_metrics(r)
    assert r.chars_per_min == 0 and r.tpm_est == 0 and r.tpm_eff == 0


def test_fill_metrics_gen_window_guard():
    # 生成窗为 0 时不应除零崩溃
    r = BenchResult(spec=_spec(), ok=True, content_chars=30, p2_gen_s=0.0)
    _fill_metrics(r)
    assert r.chars_per_min == pytest.approx(30 / 0.01 * 60)


# ── 序列化 / 渲染 ───────────────────────────────────────


def test_row_to_dict_is_json_serializable():
    import json
    r = BenchResult(spec=_spec(), ok=True, content_chars=900,
                    p2_gen_s=10.0, p2_total_s=12.0, ttft_s=2.0)
    _fill_metrics(r)
    d = row_to_dict(r)
    blob = json.dumps(d, ensure_ascii=False)  # 不抛即视为可序列化
    assert '"model_id": "m1"' in blob
    assert d["tpm_est"] == pytest.approx(3600)
    assert d["ok"] is True


def test_render_rows_contains_pass_and_fail():
    ok = BenchResult(spec=_spec("ok1"), ok=True, content_chars=600,
                     p2_gen_s=8.0, p2_total_s=10.0, ttft_s=1.0)
    _fill_metrics(ok)
    fail = BenchResult(spec=_spec("bad1"), ok=False,
                       error="[连接失败] RuntimeError: HTTP 404")
    out = render_rows([row_to_dict(ok), row_to_dict(fail)])
    assert "ok1" in out and "bad1" in out
    assert "HTTP 404" in out
