"""对局复盘存储：目录管理、replay.json 写入、图片副本、LLM 总结生成。

每局对局在 data/games/<game_id>/ 下独立存储：
    civilian.<ext>   平民图像副本
    spy.<ext>        卧底图像副本
    replay.json      完整对局记录（含私有思考）
    summary.md       裁判 LLM 总结（异步写入）
"""

from __future__ import annotations

import logging
import shutil
import threading
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from iris.core.exceptions import IrisRuntimeError, IrisValueError
from iris.utils.shared import atomic_write_json, atomic_write_text, atomic_write_bytes
from iris.core.locks import FileLock

logger = logging.getLogger(__name__)

# replay.json schema 版本
_REPLAY_SCHEMA_VERSION = 1


class ReplayStoreError(IrisRuntimeError):
    """复盘存储相关错误。"""


class ReplayStore:
    """管理谁是卧底对局的持久化存储与 LLM 总结。"""

    def __init__(self, data_root: Path) -> None:
        self._root = data_root / "games"
        self._root.mkdir(parents=True, exist_ok=True)
        self._uploads = data_root / "games" / "uploads"
        self._uploads.mkdir(parents=True, exist_ok=True)

    # ── 对局目录 ─────────────────────────────────────────────────

    def new_game_id(self) -> str:
        """生成格式为 YYYYMMDD-HHMMSS-<4hex> 的对局 ID。"""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = uuid4().hex
        return f"{ts}-{suffix}"

    def game_dir(self, game_id: str) -> Path:
        if not isinstance(game_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", game_id):
            raise ReplayStoreError("非法对局 ID")
        path = (self._root / game_id).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ReplayStoreError("对局目录越界")
        return path

    def ensure_game_dir(self, game_id: str) -> Path:
        d = self.game_dir(game_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── 图片上传暂存区 ───────────────────────────────────────────

    def save_upload(self, data: bytes, filename: str) -> str:
        with FileLock(self._uploads / "uploads"):
            return self._save_upload(data, filename)

    def _save_upload(self, data: bytes, filename: str) -> str:
        """将上传图片写入暂存区，返回 token（暂存文件名去扩展名部分）。"""
        from iris.games.image_validation import downscale_for_upload, validate_image
        suffix = Path(filename).suffix.lower()
        allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        if suffix not in allowed:
            raise IrisValueError(f"不支持的扩展名 {suffix or '（无）'}，仅支持 png / jpg / jpeg / gif / webp / bmp")
        if not data:
            raise IrisValueError("上传内容为空")
        if len(data) > 20 * 1024 * 1024:
            raise IrisValueError(f"图片 {len(data) / 1024 / 1024:.1f} MB，超过 20 MB 上限")
        validate_image(data, suffix)
        # 归一化在暂存前完成：预览、模型输入与复盘三处看到的即同一张图，
        # 复盘还原的就是模型实际看到的画面。原始图仍保留在用户本机。
        data, suffix = downscale_for_upload(data, suffix)
        self.cleanup_uploads()
        if sum(p.stat().st_size for p in self._uploads.iterdir() if p.is_file()) + len(data) > 200 * 1024 * 1024:
            raise IrisValueError("上传暂存区已满")
        token = uuid4().hex + suffix
        atomic_write_bytes(self._uploads / token, data)
        return token

    def cleanup_uploads(self) -> None:
        """上传文件仅用于复制，过期一天后清理；对局图片独立保留。"""
        cutoff = time.time() - 86400
        for path in self._uploads.iterdir():
            if re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{1,8}", path.name) and path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def upload_path(self, token: str) -> Optional[Path]:
        """返回上传文件路径，token 不存在时返回 None。"""
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{1,8}", token, re.I):
            return None
        p = (self._uploads / token).resolve()
        try:
            p.relative_to(self._uploads.resolve())
        except ValueError:
            return None
        return p if p.is_file() and not p.is_symlink() else None

    # ── 图片副本 ─────────────────────────────────────────────────

    def copy_images(
        self,
        game_id: str,
        civilian_token: str,
        spy_token: str,
    ) -> tuple[str, str]:
        """把两张暂存图片复制到对局目录，返回 (civilian_filename, spy_filename)。"""
        d = self.ensure_game_dir(game_id)

        civ_src = self.upload_path(civilian_token)
        spy_src = self.upload_path(spy_token)
        if civ_src is None:
            raise ReplayStoreError(f"平民图像 token 不存在: {civilian_token}")
        if spy_src is None:
            raise ReplayStoreError(f"卧底图像 token 不存在: {spy_token}")

        civ_dst = d / f"civilian{civ_src.suffix}"
        spy_dst = d / f"spy{spy_src.suffix}"
        shutil.copy2(civ_src, civ_dst)
        shutil.copy2(spy_src, spy_dst)
        return civ_dst.name, spy_dst.name

    # ── 写 replay.json ───────────────────────────────────────────

    def save_result(
        self,
        game_id: str,
        result,                     # GameResult（避免循环导入用 Any）
        players_info: List[Dict[str, Any]],
        civilian_filename: str,
        spy_filename: str,
        summary_model_key: str = "",
    ) -> None:
        """将 GameResult 序列化并原子写入 replay.json。

        Args:
            result: UndercoverGame.run() 返回的 GameResult
            players_info: [{"key","role","model_id","is_spy","display_name"}, ...]
            civilian_filename: 对局目录内的平民图片文件名
            spy_filename: 对局目录内的卧底图片文件名
            summary_model_key: "role/model_id" 形式的裁判模型标识
        """
        d = self.ensure_game_dir(game_id)

        rounds_data = []
        for r in result.rounds:
            rd = asdict(r)
            rounds_data.append(rd)

        payload: Dict[str, Any] = {
            "schema_version": _REPLAY_SCHEMA_VERSION,
            "id": game_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "setup": {
                "players": players_info,
                "spy_keys": result.spy_keys,
                "spy_count": result.spy_count,
                "seed": result.seed,
                "order_mode": result.order_mode,
                "image_civilian": civilian_filename,
                "image_spy": spy_filename,
                "summary_model_key": summary_model_key,
            },
            "result": {
                "winner": result.winner,
                "final_survivors": result.final_survivors,
                "total_rounds": len(result.rounds),
                "errors": result.errors,
            },
            "rounds": rounds_data,
            "summary_ready": False,
            "summary_status": "pending" if summary_model_key.rstrip("/") not in ("", "base_model", "adv_model") and result.winner != "cancelled" else "skipped",
        }
        with FileLock(d / "replay.json"):
            atomic_write_json(d / "replay.json", payload)

        # 对局已完整存档，删除断点文件（若存在）
        checkpoint = d / "checkpoint.json"
        if checkpoint.exists():
            try:
                checkpoint.unlink()
            except OSError as exc:  # noqa: BLE001
                logger.warning("删除断点文件失败（非关键）: %s", exc)

    # ── 列表 & 加载 ──────────────────────────────────────────────

    def list_games(self) -> List[Dict[str, Any]]:
        """列出所有对局摘要，按创建时间倒序。"""
        items: List[Dict[str, Any]] = []
        for d in sorted(self._root.iterdir(), reverse=True):
            if not d.is_dir() or d.name == "uploads":
                continue
            replay = d / "replay.json"
            if not replay.exists():
                continue
            try:
                import json
                data = json.loads(replay.read_text(encoding="utf-8"))
                setup = data.get("setup", {})
                result = data.get("result", {})
                items.append({
                    "id": data.get("id", d.name),
                    "created_at": data.get("created_at", ""),
                    "winner": result.get("winner", ""),
                    "total_rounds": result.get("total_rounds", 0),
                    "player_count": len(setup.get("players", [])),
                    "spy_keys": setup.get("spy_keys", []),
                    "spy_count": setup.get("spy_count", 0),
                    "summary_ready": data.get("summary_ready", False),
                    "summary_status": data.get("summary_status", "unknown"),
                    "players": [p.get("key", "") for p in setup.get("players", [])],
                    "seed": setup.get("seed"),
                })
            except Exception as exc:  # noqa: BLE001 — 损坏对局不影响列表
                logger.warning("加载复盘 %s 失败，已跳过: %s", d.name, exc)
        return items

    def load_game(self, game_id: str) -> Dict[str, Any]:
        """加载单个对局完整数据，包含 summary 内容（若已生成）。"""
        import json

        d = self.game_dir(game_id)
        replay = d / "replay.json"
        if not replay.exists():
            raise ReplayStoreError(f"对局不存在: {game_id}")

        data = json.loads(replay.read_text(encoding="utf-8"))

        summary_file = d / "summary.md"
        if summary_file.exists():
            data["summary_text"] = summary_file.read_text(encoding="utf-8")
        else:
            data["summary_text"] = ""

        return data

    def image_path(self, game_id: str, role: str) -> Optional[Path]:
        """返回对局图片路径（role='civilian'|'spy'），不存在返回 None。"""
        d = self.game_dir(game_id)
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            p = d / f"{role}{ext}"
            if p.exists():
                return p
        return None

    # ── 断点存档 ─────────────────────────────────────────────────

    def save_checkpoint(
        self,
        game_id: str,
        setup: Dict[str, Any],
        completed_rounds: List[Dict[str, Any]],
        alive_keys: List[str],
        spy_keys: List[str],
        speaking_order_base: List[str],
    ) -> None:
        """每轮结束后原子写入 checkpoint.json，供断点恢复使用。"""
        d = self.ensure_game_dir(game_id)
        payload: Dict[str, Any] = {
            "schema_version": _REPLAY_SCHEMA_VERSION,
            "game_id": game_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "setup": setup,
            "completed_rounds": completed_rounds,
            "alive_keys": alive_keys,
            "spy_keys": spy_keys,
            "speaking_order_base": speaking_order_base,
        }
        with FileLock(d / "checkpoint.json"):
            atomic_write_json(d / "checkpoint.json", payload)

    def load_checkpoint(self, game_id: str) -> Optional[Dict[str, Any]]:
        """加载 checkpoint.json，不存在或损坏时返回 None。"""
        import json
        try:
            d = self.game_dir(game_id)
            cp = d / "checkpoint.json"
            if not cp.exists():
                return None
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — 损坏断点不影响正常流程
            logger.warning("加载断点 %s 失败: %s", game_id, exc)
            return None

    def list_incomplete_games(self) -> List[Dict[str, Any]]:
        """列出有 checkpoint.json 但没有完整 replay.json 的对局（即断点对局）。"""
        items: List[Dict[str, Any]] = []
        for d in sorted(self._root.iterdir(), reverse=True):
            if not d.is_dir() or d.name == "uploads":
                continue
            checkpoint = d / "checkpoint.json"
            replay = d / "replay.json"
            if not checkpoint.exists() or replay.exists():
                continue
            try:
                import json
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
                items.append({
                    "id": data.get("game_id", d.name),
                    "updated_at": data.get("updated_at", ""),
                    "completed_rounds": len(data.get("completed_rounds", [])),
                    "alive_keys": data.get("alive_keys", []),
                    "spy_count": len(data.get("spy_keys", [])),
                    "player_count": len(data.get("setup", {}).get("players", [])),
                })
            except Exception as exc:  # noqa: BLE001 — 损坏断点不影响列表
                logger.warning("读取断点摘要 %s 失败: %s", d.name, exc)
        return items

    # ── LLM 总结（异步） ─────────────────────────────────────────

    def trigger_summary(
        self,
        game_id: str,
        llm,                    # LLMService（避免循环导入）
        summary_role: str,
        summary_model_id: str,
    ) -> None:
        """在后台线程生成裁判 LLM 总结，完成后原子更新 replay.json。"""
        t = threading.Thread(
            target=self._generate_summary,
            args=(game_id, llm, summary_role, summary_model_id),
            daemon=True,
            name=f"summary-{game_id}",
        )
        t.start()

    def _generate_summary(
        self,
        game_id: str,
        llm,
        summary_role: str,
        summary_model_id: str,
    ) -> None:
        import json

        try:
            data = self.load_game(game_id)
            prompt = _build_summary_prompt(data)
            gen = llm.generate_as(
                summary_role,
                summary_model_id,
                prompt,
                route_context={"task_type": "undercover_summary"},
            )
            summary_text = gen.text.strip()
            if not summary_text:
                raise ValueError("总结返回空内容")

            d = self.game_dir(game_id)
            atomic_write_text(d / "summary.md", summary_text)

            # 更新 summary_ready 标志
            replay = d / "replay.json"
            with FileLock(replay):
                payload = json.loads(replay.read_text(encoding="utf-8"))
                payload["summary_ready"] = True
                payload["summary_status"] = "ready"
                atomic_write_json(replay, payload)

            logger.info("对局 %s 总结生成完成", game_id)
        except Exception as exc:  # noqa: BLE001 — 总结失败不影响复盘存档
            logger.warning("对局 %s 总结生成失败: %s", game_id, exc)
            replay = self.game_dir(game_id) / "replay.json"
            try:
                with FileLock(replay):
                    payload = json.loads(replay.read_text(encoding="utf-8"))
                    payload["summary_status"] = "failed"
                    payload["summary_error"] = str(exc)
                    atomic_write_json(replay, payload)
            except (OSError, ValueError):
                logger.exception("无法保存总结失败状态 %s", game_id)


# ── 总结 prompt ──────────────────────────────────────────────────


def _build_summary_prompt(data: Dict[str, Any]) -> str:
    """根据 replay.json 数据构建裁判总结 prompt。"""
    setup = data.get("setup", {})
    result = data.get("result", {})
    rounds = data.get("rounds", [])

    players = setup.get("players", [])
    spy_keys = set(setup.get("spy_keys", []))

    # 构建玩家说明（身份已揭晓）
    player_lines = []
    for p in players:
        key = p.get("key", "")
        role = "卧底" if key in spy_keys else "平民"
        player_lines.append(f"  - {key}（{role}）")
    players_text = "\n".join(player_lines)

    winner_map = {"civilians": "平民方获胜", "spy": "卧底方获胜", "stalemate": "平局（达到轮次上限）"}
    winner_text = winner_map.get(result.get("winner", ""), result.get("winner", ""))

    # 构建轮次摘要
    round_lines = []
    for r in rounds:
        rno = r.get("round_no", "?")
        speeches = r.get("speeches", [])
        speech_texts = []
        for s in speeches:
            key = s.get("key", "?")
            desc = s.get("description", "")
            resp = s.get("response", "")
            speech_texts.append(f"    {key}：描述「{desc}」，回应「{resp}」")

        votes = r.get("votes", {})
        vote_texts = [f"    {voter} → {target}" for voter, target in votes.items()]

        eliminated = r.get("eliminated")
        elim_text = ""
        if eliminated:
            was_spy = r.get("eliminated_was_spy", False)
            elim_text = f"\n  淘汰：{eliminated}（{'卧底' if was_spy else '平民'}）"

        round_lines.append(
            f"【第 {rno} 轮】\n  发言：\n" +
            "\n".join(speech_texts) +
            "\n  投票：\n" +
            "\n".join(vote_texts) +
            elim_text
        )
    rounds_text = "\n\n".join(round_lines)

    return f"""你是一名「谁是卧底」游戏的裁判，现在请对刚刚结束的对局进行点评总结。

【游戏信息】
参与玩家（身份已揭晓）：
{players_text}
最终结果：{winner_text}，共 {result.get('total_rounds', 0)} 轮

【完整对局记录】
{rounds_text}

【总结要求】
请按以下结构输出裁判总结，使用 Markdown 格式：

1. **对战概览** — 一段话说明双方阵容、关键数字和最终结果。
2. **关键转折点** — 指出哪一轮、哪个玩家的哪句描述成为暴露或误判的关键，简要说明原因。
3. **各模型表现点评** — 逐一点评每位玩家的表现：平民的侦查力与从众分析，卧底的伪装质量与应对策略。
4. **裁判总评** — 对整局博弈水平给出总体评价，不超过 3 句话。

语言风格：客观、具体、简洁，避免空洞的表扬。"""
