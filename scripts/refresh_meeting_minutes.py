#!/usr/bin/env python3
"""历史语音转写纪要翻新脚本。

使用 deepseek-v4-pro 模型，以最新管线重新提取全部历史转录文件的会议纪要，
替换 SOURCE 中已有的旧版纪要（旧版备份为 .bak）。

用法:
    python3 scripts/refresh_meeting_minutes.py --dry-run     # 预览
    python3 scripts/refresh_meeting_minutes.py --limit 3     # 烟雾测试
    python3 scripts/refresh_meeting_minutes.py --resume      # 全量执行
    python3 scripts/refresh_meeting_minutes.py --verify      # 校验
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将项目根目录加入 Python 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from iris.config.loader import load_config_bundle
from iris.app.transcribe_meeting.pipeline import TranscribeMeetingPipeline

# ── 常量 ──────────────────────────────────────────────────────────
MODEL_NAME = "deepseek-v4-pro"
STATE_FILE = PROJECT_ROOT / "temp" / "refresh_meeting_state.jsonl"
DELAY_SECONDS = 2  # pro 模型限流间隔
MAX_CONSECUTIVE_FAILURES = 3


# ── 数据类 ────────────────────────────────────────────────────────
class ProcessResult:
    __slots__ = ("stem", "transcript_path", "status", "old_source_paths",
                 "backup_paths", "output_path", "route", "route_reason",
                 "model", "word_count", "duration_seconds", "error", "timestamp")
    def __init__(self, stem: str, transcript_path: str, status: str = "pending",
                 old_source_paths: list | None = None, backup_paths: list | None = None,
                 output_path: str = "", route: str = "", route_reason: str = "",
                 model: str = "", word_count: int = 0, duration_seconds: float = 0.0,
                 error: str | None = None):
        self.stem = stem
        self.transcript_path = transcript_path
        self.status = status
        self.old_source_paths = old_source_paths or []
        self.backup_paths = backup_paths or []
        self.output_path = output_path
        self.route = route
        self.route_reason = route_reason
        self.model = model
        self.word_count = word_count
        self.duration_seconds = duration_seconds
        self.error = error
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


# ── 状态管理 ──────────────────────────────────────────────────────
class StateManager:
    def __init__(self, state_path: Path):
        self._path = state_path
        self._items: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            item = json.loads(line)
                            self._items[item["stem"]] = item
                        except json.JSONDecodeError:
                            pass

    def flush(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            for item in self._items.values():
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def is_processed(self, stem: str) -> bool:
        return self._items.get(stem, {}).get("status") == "processed"

    def is_failed(self, stem: str) -> bool:
        return self._items.get(stem, {}).get("status") == "failed"

    def get_error(self, stem: str) -> str:
        return self._items.get(stem, {}).get("error", "")

    def save(self, result: ProcessResult):
        self._items[result.stem] = result.to_dict()
        # 每条处理完立即刷盘
        self.flush()

    def get_summary(self) -> dict:
        total = len(self._items)
        processed = sum(1 for v in self._items.values() if v.get("status") == "processed")
        failed = sum(1 for v in self._items.values() if v.get("status") == "failed")
        return {"total": total, "processed": processed, "failed": failed}

    def get_processed_stems(self) -> set:
        return {k for k, v in self._items.items() if v.get("status") == "processed"}


# ── 主类 ──────────────────────────────────────────────────────────
class MeetingMinutesRefresher:
    def __init__(self, project_root: Path):
        self._root = project_root
        self._bundle = load_config_bundle(project_root)
        self._pipeline = TranscribeMeetingPipeline(self._bundle)
        self._source_root = self._resolve_source_root()
        self._trans_dir = self._resolve_trans_dir()

    def _resolve_source_root(self) -> Path:
        sources = self._bundle.data_source.get("sources", {})
        for cfg in sources.values():
            if cfg.get("enabled") and cfg.get("path"):
                p = Path(cfg["path"]).resolve()
                if p.exists():
                    return p
        raise RuntimeError("未找到启用的 SOURCE 数据源")

    def _resolve_trans_dir(self) -> Path:
        import os
        env_dir = os.environ.get("IRIS_MEETING_TRANS_DIR", "")
        if not env_dir:
            from iris.config.loader import load_env_file
            env = load_env_file(self._root / ".env")
            env_dir = env.get("IRIS_MEETING_TRANS_DIR", "")
        if env_dir:
            p = Path(env_dir).expanduser().resolve()
            if p.exists():
                return p
        raise RuntimeError("IRIS_MEETING_TRANS_DIR 未配置或目录不存在")

    # ── 扫描 ──────────────────────────────────────────────────────
    def scan(self) -> List[Path]:
        """返回按文件名排序的转录文件列表。"""
        files = sorted(self._trans_dir.glob("*.txt"))
        print(f"扫描到 {len(files)} 个转录文件 ({self._trans_dir})", file=sys.stderr)
        return files

    def find_source_matches(self, stem: str) -> List[Path]:
        """在 SOURCE 所有子目录中查找匹配的 .md 文件。"""
        matches = []
        for subdir in self._source_root.iterdir():
            if subdir.is_dir() and not subdir.is_symlink():
                candidate = subdir / f"{stem}.md"
                if candidate.exists():
                    matches.append(candidate)
        return matches

    # ── 备份 ──────────────────────────────────────────────────────
    def backup_existing(self, stem: str, old_paths: List[Path]) -> List[Path]:
        """将匹配的 .md 文件重命名为 .bak.{timestamp}.md。"""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backups = []
        for p in old_paths:
            backup = p.parent / f"{stem}.bak.{ts}.md"
            p.rename(backup)
            backups.append(backup)
            print(f"  📦 备份: {p.name} → {backup.name}", file=sys.stderr)
        return backups

    # ── 处理单个文件 ──────────────────────────────────────────────
    def process_one(self, transcript_path: Path,
                    dry_run: bool = False) -> ProcessResult:
        stem = transcript_path.stem
        result = ProcessResult(stem=stem, transcript_path=str(transcript_path))

        # 查找匹配的 SOURCE 文件
        old_paths = self.find_source_matches(stem)
        result.old_source_paths = [str(p.relative_to(self._source_root)) for p in old_paths]

        if dry_run:
            result.status = "dry_run"
            self._print_dry_run(result, old_paths)
            return result

        # 备份
        if old_paths:
            result.backup_paths = [str(b.relative_to(self._source_root))
                                   for b in self.backup_existing(stem, old_paths)]

        # 调用管线
        t0 = time.time()
        try:
            pipe_result = self._pipeline.run(
                transcript_path=str(transcript_path),
                to_source=True,
                model=MODEL_NAME,
            )
            result.status = "processed"
            result.output_path = pipe_result.get("output_file", "")
            result.route = pipe_result.get("route", "")
            result.route_reason = pipe_result.get("route_reason", "")
            result.model = pipe_result.get("model", "")
            result.word_count = pipe_result.get("word_count", 0)
            result.duration_seconds = round(time.time() - t0, 1)
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            result.duration_seconds = round(time.time() - t0, 1)

        return result

    def _print_dry_run(self, result: ProcessResult, old_paths: List[Path]):
        label = "🔄 替换" if old_paths else "🆕 新建"
        print(f"  [{label}] {result.stem}", file=sys.stderr)
        if old_paths:
            for p in old_paths:
                rel = p.relative_to(self._source_root)
                print(f"     📦 将备份: {rel} → {p.name}.bak", file=sys.stderr)
        else:
            print(f"     📂 将归档到 SOURCE（LLM 路由判定）", file=sys.stderr)

    # ── 主循环 ────────────────────────────────────────────────────
    def run(self, *, dry_run: bool = False, resume: bool = True,
            limit: int = 0, force: bool = False):
        files = self.scan()
        state = StateManager(STATE_FILE)
        processed_stems = state.get_processed_stems() if not force else set()

        # 过滤已处理
        if resume and not force:
            pending = [f for f in files if f.stem not in processed_stems]
            skipped = len(files) - len(pending)
            if skipped:
                print(f"⏭ 跳过已处理 {skipped} 个文件 (--resume)", file=sys.stderr)
        else:
            pending = files
            if not dry_run:
                STATE_FILE.unlink(missing_ok=True)
                state = StateManager(STATE_FILE)

        if limit:
            pending = pending[:limit]

        total = len(pending)
        consecutive_failures = 0

        for i, tf in enumerate(pending, 1):
            print(f"\n[{i}/{total}] {tf.stem}", file=sys.stderr)

            result = self.process_one(tf, dry_run=dry_run)

            if not dry_run:
                state.save(result)

            if result.status == "processed":
                consecutive_failures = 0
                route_str = f" → {result.route}" if result.route else ""
                print(f"  ✅ {result.duration_seconds:.0f}s{route_str}",
                      file=sys.stderr)
            elif result.status == "failed":
                consecutive_failures += 1
                print(f"  ❌ 失败: {result.error}", file=sys.stderr)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n⚠️ 连续 {MAX_CONSECUTIVE_FAILURES} 次失败，中止",
                          file=sys.stderr)
                    break

            # 限流间隔
            if not dry_run and i < total:
                time.sleep(DELAY_SECONDS)

        # 汇总
        if not dry_run:
            summary = state.get_summary()
            print(f"\n{'='*50}", file=sys.stderr)
            print(f"完成: {summary['processed']}/{summary['total']} 成功"
                  f"  {summary['failed']} 失败", file=sys.stderr)
        else:
            print(f"\n{'='*50}", file=sys.stderr)
            print(f"[DRY-RUN] 共 {total} 个文件，未实际写入", file=sys.stderr)

    # ── 校验 ──────────────────────────────────────────────────────
    def verify(self):
        if not STATE_FILE.exists():
            print("❌ 状态文件不存在，请先执行刷新", file=sys.stderr)
            return

        state = StateManager(STATE_FILE)
        summary = state.get_summary()
        print(f"=== 校验报告 ===", file=sys.stderr)
        print(f"处理: {summary['processed']}/{summary['total']} 成功"
              f"  {summary['failed']} 失败", file=sys.stderr)

        # 路由分布
        routes: Dict[str, int] = {}
        orphans_in_source = []
        for item in state._items.values():
            if item.get("status") == "processed":
                route = item.get("route", "未知")
                routes[route] = routes.get(route, 0) + 1

        print(f"\n路由分布:", file=sys.stderr)
        for r, c in sorted(routes.items(), key=lambda x: -x[1]):
            print(f"  {r}: {c}", file=sys.stderr)

        # 输出文件存在性
        missing = []
        for item in state._items.values():
            if item.get("status") == "processed" and item.get("output_path"):
                if not Path(item["output_path"]).exists():
                    missing.append(item["stem"])
        if missing:
            print(f"\n⚠️ 输出文件缺失 ({len(missing)}):", file=sys.stderr)
            for s in missing:
                print(f"  - {s}", file=sys.stderr)
        else:
            print(f"\n✅ 所有输出文件存在", file=sys.stderr)

        # 备份完整性
        backed_up = sum(1 for item in state._items.values()
                       if item.get("backup_paths"))
        print(f"📦 备份文件: {backed_up} 个转录对应备份", file=sys.stderr)

        # 失败列表
        failed = [(item["stem"], item.get("error", ""))
                  for item in state._items.values()
                  if item.get("status") == "failed"]
        if failed:
            print(f"\n❌ 失败文件 ({len(failed)}):", file=sys.stderr)
            for stem, err in failed:
                print(f"  - {stem}: {err}", file=sys.stderr)


# ── CLI ───────────────────────────────────────────────────────────
def main():
    global DELAY_SECONDS

    parser = argparse.ArgumentParser(description="历史语音转写纪要翻新")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    parser.add_argument("--resume", action="store_true", help="从状态文件恢复中断的执行")
    parser.add_argument("--limit", type=int, default=0, help="限制处理文件数（用于烟雾测试）")
    parser.add_argument("--force", action="store_true", help="强制重新处理所有文件")
    parser.add_argument("--delay", type=int, default=DELAY_SECONDS, help=f"文件间延迟秒数（默认 {DELAY_SECONDS}）")
    parser.add_argument("--verify", action="store_true", help="校验模式")
    args = parser.parse_args()

    DELAY_SECONDS = args.delay

    refresher = MeetingMinutesRefresher(PROJECT_ROOT)

    # 信号处理
    def _handle_sigint(signum, frame):
        print("\n⏸ 中断信号，状态已保存。使用 --resume 继续。", file=sys.stderr)
        sys.exit(130)
    signal.signal(signal.SIGINT, _handle_sigint)

    if args.verify:
        refresher.verify()
    else:
        refresher.run(
            dry_run=args.dry_run,
            resume=args.resume,
            limit=args.limit,
            force=args.force,
        )


if __name__ == "__main__":
    main()
