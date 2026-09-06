"""LLM 通道/模型 连接速度(TTFT) + 吞吐 基准引擎。

供两条消费路径复用：
  - CLI `iris llm-bench`（见 app/cli/_handlers/_bench.py）
  - 独立脚本 scripts/llm_bench.py（人类可读表格输出）

测量口径（保证跨通道/模型可比、不依赖中继 usage）：
  - TTFT(连接速度)：流式短问（“请只回复两个字：收到”）测「请求 → 首个可见正文
    字符」秒数。max_tokens 给足(默认 900)让思考型模型思考完能落到正文；给太小
    (如 16)会被思考截断而误判“无正文”。
  - 吞吐：让模型写中文散文（同一内容任务，思考型与非思考型都稳定产出），统计
    「首个正文之后」的稳定生成窗：
        字符/分 chars/min = 生成窗正文字符数 / 生成窗秒数 * 60
        估 TPM(tok/min)   = 字符/分 ÷ CHARS_PER_TOKEN(中文≈1.5 字符/token)
    以实测字符为准，不依赖服务端 usage —— 实测 tokenhub 中继对 Qwen 系列
    usage.completion_tokens 虚高且不随 max_tokens 变化，svc_tok 仅作参照。
  思考型模型在正文前烧的思考 token 计入 TTFT/总耗时，不计入正文生成窗速率。

异常：单模型失败不中断，逐条记入 rows。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

# 中文正文的估算 token 密度（qwen/deepseek 系中文 tokenizer 约 1.5 字符/token）
CHARS_PER_TOKEN = 1.5
PING_PROMPT = "请只回复两个字：收到。"
# 吞吐用中文散文 prompt：思考型与非思考型都能稳定产出正文（数数任务会诱发
# DeepSeek 思考模型长时间思考甚至拒出正文，故不用）。
PROSE_PROMPT = (
    "请围绕“高效的团队协作”直接写一段不少于 500 字的中文正文，"
    "不要标题、不要前言、不要解释，只写正文，同一自然段写完。"
)


@dataclass
class ModelSpec:
    role: str
    model_id: str
    channel: str
    provider: str
    model: str
    api_base: str
    api_key: str
    multimodal: bool
    cfg_max_tokens: int

    @property
    def dedupe_key(self) -> Tuple[str, str]:
        return (self.api_base, self.model)


@dataclass
class BenchResult:
    spec: ModelSpec
    ok: bool = False
    error: str = ""
    # Phase1 连接
    http_ms: float = 0.0
    ttft_s: Optional[float] = None      # 首可见正文 token 延迟（真实连接速度）
    ttft_ok: bool = False
    # Phase2 吞吐（以实测字符为准，usage 仅参照）
    p2_total_s: float = 0.0             # 请求→结束 全窗（含思考/首token）
    p2_gen_s: float = 0.0               # 首个正文→结束 稳定生成窗
    content_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0          # 服务端 usage（tokenhub Qwen 虚高，勿当真值）
    usage_source: str = ""
    chars_per_min: float = 0.0
    tpm_est: float = 0.0                # = chars_per_min / CHARS_PER_TOKEN
    tpm_eff: float = 0.0                # 端到端（含 TTFT），同样按字符折算


# ── 底层 HTTP / SSE ───────────────────────────────────────


def _sse_events(resp: requests.Response):
    """把 OpenAI 兼容 SSE 流逐事件 yield。"""
    buf = ""
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        if raw.startswith("data:"):
            buf += raw[5:].strip()
            yield buf
            buf = ""
        elif raw == "" and buf:
            yield buf
            buf = ""
    if buf:
        yield buf


def _openai_chat_stream(
    api_base: str, api_key: str, model: str, prompt: str,
    max_tokens: int, temperature: float = 0.1, timeout_read: int = 300,
) -> Dict[str, Any]:
    """流式调用并记录时间/字符/usage。时间基于 time.monotonic。"""
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    t0 = time.monotonic()
    out: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0,
                           "stream_usage": False, "content_len": 0,
                           "ttft_s": None, "http_ms": 0.0, "total_s": 0.0}
    usage: Optional[Dict[str, int]] = None
    got_content = False
    resp: Optional[requests.Response] = None
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
            stream=True, timeout=(8, timeout_read),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        out["http_ms"] = (time.monotonic() - t0) * 1000
        for data in _sse_events(resp):
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
                continue
            for choice in chunk.get("choices", []) or []:
                delta = choice.get("delta", {}) or {}
                text = delta.get("content")
                if text:
                    out["content_len"] += len(text)
                    if not got_content:
                        got_content = True
                        out["ttft_s"] = time.monotonic() - t0
    finally:
        if resp is not None:
            resp.close()
    out["total_s"] = time.monotonic() - t0
    if usage:
        out["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
        out["completion_tokens"] = int(usage.get("completion_tokens") or 0)
        out["stream_usage"] = True
    return out


def _openai_chat_nonstream(api_base: str, api_key: str, model: str, prompt: str,
                           max_tokens: int, timeout_read: int = 300) -> Tuple[int, int]:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "temperature": 0.1, "stream": False}
    resp = requests.post(url, json=payload,
                         headers={"Authorization": f"Bearer {api_key}"},
                         timeout=(8, timeout_read))
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    usage = resp.json().get("usage", {})
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


# ── 模型盘点 ──────────────────────────────────────────────


def collect_specs(bundle) -> List[ModelSpec]:
    """从已加载的 ConfigBundle 枚举全部 (channel×model) 组合（含 role）。"""
    specs: List[ModelSpec] = []
    for role, rc in bundle.llm["models"].items():
        for mid, mc in (rc.get("models") or {}).items():
            specs.append(ModelSpec(
                role=role, model_id=mid,
                channel=str(mc.get("channel", "")),
                provider=str(mc.get("provider", "")).lower(),
                model=str(mc.get("model", "")),
                api_base=str(mc.get("api_base_url", "")),
                api_key=str(mc.get("api_key", "")),
                multimodal=bool(mc.get("multimodal")),
                cfg_max_tokens=int(mc.get("max_tokens") or 8192),
            ))
    return specs


def filter_specs(specs: List[ModelSpec], *, channel: str = "",
                 model_ids: Optional[List[str]] = None) -> List[ModelSpec]:
    """按通道 / model_id 过滤。model_ids 为空则不限制。"""
    out = [s for s in specs if not channel or s.channel == channel]
    if model_ids:
        out = [s for s in out if s.model_id in model_ids]
    return out


# ── 单模型测量 ────────────────────────────────────────────


def _fill_metrics(r: BenchResult) -> None:
    """由实测字符/时间窗计算字符速率与估 TPM（纯函数，便于单测）。"""
    if r.content_chars <= 0:
        return
    gen_s = max(r.p2_gen_s, 0.01)
    r.chars_per_min = r.content_chars / gen_s * 60
    r.tpm_est = r.chars_per_min / CHARS_PER_TOKEN
    r.tpm_eff = (r.content_chars / max(r.p2_total_s, 0.01) * 60) / CHARS_PER_TOKEN


def run_ttft(spec: ModelSpec, max_tokens: int = 900) -> BenchResult:
    """Phase1 连接速度：短答 prompt，测首可见正文延迟。"""
    r = BenchResult(spec=spec)
    try:
        s = _openai_chat_stream(spec.api_base, spec.api_key, spec.model,
                                PING_PROMPT, max_tokens, timeout_read=60)
        r.http_ms = s["http_ms"]
        r.ttft_s = s.get("ttft_s")
        r.ttft_ok = s.get("ttft_s") is not None
        if not r.ttft_ok:
            r.ok = True  # 连接成功但未见正文（极少数思考模型），交由 Phase2 佐证
            r.error = "HTTP正常但短答未见可见正文"
        else:
            r.ok = True
    except Exception as exc:  # noqa: BLE001
        r.ok = False
        r.error = f"[连接失败] {type(exc).__name__}: {str(exc)[:160]}"
    return r


def run_tpm(spec: ModelSpec, max_tokens: int = 3000, temperature: float = 0.6,
            prev_ttft: Optional[float] = None) -> BenchResult:
    """Phase2 吞吐：中文散文流式输出，统计正文生成窗字符速率。"""
    r = BenchResult(spec=spec)
    r.ttft_s = prev_ttft
    try:
        s = _openai_chat_stream(spec.api_base, spec.api_key, spec.model,
                                PROSE_PROMPT, max_tokens, temperature=temperature,
                                timeout_read=360)
        r.p2_total_s = s["total_s"]
        r.content_chars = s["content_len"]
        if s.get("ttft_s") is not None:
            r.ttft_s = s["ttft_s"]
        r.p2_gen_s = max(s["total_s"] - (r.ttft_s or 0.0), 0.01)
        if s.get("stream_usage"):
            r.prompt_tokens = s["prompt_tokens"]
            r.completion_tokens = s["completion_tokens"]
            r.usage_source = "stream"
        else:
            r.usage_source = "fallback"
            try:
                r.prompt_tokens, r.completion_tokens = _openai_chat_nonstream(
                    spec.api_base, spec.api_key, spec.model, PROSE_PROMPT, max_tokens)
            except Exception as exc:  # noqa: BLE001
                r.usage_source = "none"
                r.error = f"{r.error}; usage 回退失败: {str(exc)[:100]}"
        _fill_metrics(r)
        r.ok = True
    except Exception as exc:  # noqa: BLE001
        r.ok = False
        r.error = f"[吞吐失败] {type(exc).__name__}: {str(exc)[:160]}"
    return r


# ── 结果序列化 / 渲染 ─────────────────────────────────────


def row_to_dict(r: BenchResult) -> Dict[str, Any]:
    """BenchResult → 可 JSON 序列化的行（纯函数，便于单测）。"""
    return {
        "role": r.spec.role,
        "channel": r.spec.channel,
        "model_id": r.spec.model_id,
        "api_model": r.spec.model,
        "provider": r.spec.provider,
        "multimodal": r.spec.multimodal,
        "ok": r.ok,
        "error": r.error,
        "ttft_s": round(r.ttft_s, 2) if r.ttft_s is not None else None,
        "http_ms": round(r.http_ms, 1),
        "content_chars": r.content_chars,
        "chars_per_min": round(r.chars_per_min),
        "tpm_est": round(r.tpm_est),
        "tpm_eff": round(r.tpm_eff),
        "completion_tokens": r.completion_tokens,
        "total_s": round(r.p2_total_s, 1),
        "gen_s": round(r.p2_gen_s, 1),
        "usage_source": r.usage_source,
    }


def render_rows(rows: List[Dict[str, Any]]) -> str:
    """人类可读对齐表格（standalone / CLI --pretty 用）。"""
    lines = [
        "\n" + "=" * 130,
        f"{'通道':<11}{'模型ID':<34}{'多模':<3}{'TTFT/s':>7}  {'正文字符':>8}"
        f"  {'字符/分':>8}  {'估TPM':>7}  {'端到端TPM':>9}  {'svc_tok':>7}  {'svc秒':>6}  状态",
        "-" * 130,
    ]
    for r in rows:
        mm = "Y" if r["multimodal"] else "N"
        if not r["ok"]:
            lines.append(f"{r['channel']:<11}{r['model_id']:<34}{mm:<3}{'-':>7}  {'-':>8}"
                         f"  {'-':>8}  {'-':>7}  {'-':>9}  {'-':>7}  {'-':>6}  失败 {r['error']}")
            continue
        tt = f"{r['ttft_s']:.2f}" if r["ttft_s"] is not None else "-"
        lines.append(f"{r['channel']:<11}{r['model_id']:<34}{mm:<3}{tt:>7}  {r['content_chars']:>8}"
                     f"  {r['chars_per_min']:>8}  {r['tpm_est']:>7}  {r['tpm_eff']:>9}"
                     f"  {r['completion_tokens']:>7}  {r['total_s']:>6.1f}  通过"
                     + (f"  ({r['error']})" if r["error"] else ""))
    lines += ["=" * 130,
              "估TPM=正文生成窗 字符/分÷1.5(中文≈1.5字符/token) · 端到端TPM含首token延迟 · "
              "svc_tok=服务端usage.completion_tokens(tokenhub Qwen 虚高,勿当真值)"]
    return "\n".join(lines)


# ── 编排 ──────────────────────────────────────────────────


def run_benchmark(bundle, *, channel: str = "", model_ids: Optional[List[str]] = None,
                  concurrency: int = 4, max_tokens: int = 3000,
                  phase1_only: bool = False, phase2_only: bool = False,
                  ttft_cache: Optional[Dict[str, Optional[float]]] = None,
                  progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """两阶段基准。progress(text:str) 可选，用于阶段进度回显。

    返回 {"rows": [row_to_dict...], "concurrency":..., "max_tokens":...,
          "ttft_cache": {...}}。
    """
    specs = filter_specs(collect_specs(bundle), channel=channel, model_ids=model_ids)
    if not specs:
        raise ValueError("没有匹配的模型配置（检查 --channel / --bench-model）")

    results: Dict[str, BenchResult] = {}
    cache = dict(ttft_cache) if ttft_cache else {}

    # Phase1 连接速度（逐个顺序执行，无并发争抢，数据干净）
    if not phase2_only:
        for spec in specs:
            if progress:
                progress(f"[连接] {spec.channel}/{spec.model_id}")
            r = run_ttft(spec)
            results[spec.model_id] = r
            cache[spec.model_id] = r.ttft_s if r.ok else None
        if phase1_only:
            return {"rows": [row_to_dict(results[s.model_id]) for s in specs],
                    "phase": "ttft", "ttft_cache": cache}
    else:
        for spec in specs:
            r = BenchResult(spec=spec)
            r.ttft_s = cache.get(spec.model_id)
            results[spec.model_id] = r

    # Phase2 吞吐（并行）
    def _one(spec: ModelSpec) -> BenchResult:
        prev = None
        pr = results.get(spec.model_id)
        if pr is not None and (pr.ok or pr.ttft_s is not None):
            prev = pr.ttft_s
        return run_tpm(spec, max_tokens=max_tokens, prev_ttft=prev)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_one, spec): spec for spec in specs}
        for idx, fut in enumerate(as_completed(futs), 1):
            spec = futs[fut]
            results[spec.model_id] = fut.result()
            if progress:
                ok = results[spec.model_id].ok
                progress(f"[吞吐 {idx}/{len(specs)}] {spec.channel}/{spec.model_id} "
                         + ("完成" if ok else f"失败 {results[spec.model_id].error}"))

    return {"rows": [row_to_dict(results[s.model_id]) for s in specs],
            "phase": "full", "ttft_cache": cache,
            "concurrency": concurrency, "max_tokens": max_tokens}
