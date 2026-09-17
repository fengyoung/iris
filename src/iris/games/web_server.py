"""谁是卧底 Web 服务器 — stdlib ThreadingHTTPServer，零外部依赖。

路由：
    GET  /                          单页 SPA（从 templates/ 读入内存）
    GET  /api/models                可用模型列表
    POST /api/upload                上传图片，返回 token
    GET  /uploads/<token>           预览上传图片
    POST /api/game/start            启动游戏，返回 game_id
    GET  /api/game/<id>/events      SSE 事件流
    POST /api/game/<id>/advance     手动步进
    POST /api/game/<id>/abort       终止游戏
    GET  /api/history               历史对局列表
    GET  /api/history/<id>          单个对局完整数据
    GET  /api/history/<id>/image/<role>  对局图片 (role=civilian|spy)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections import deque
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator
from typing import Literal
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, cast
from urllib.parse import urlparse

from iris.config.loader import ConfigBundle
from iris.games.replay_store import ReplayStore
from iris.games.undercover import UndercoverGame, UndercoverGameError
from iris.llm.service import LLMService

logger = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "undercover_web.html"
_SSE_SENTINEL = None   # 游戏结束或中止时 put 进队列，通知 SSE handler 退出

# 裁判默认模型：走官方直连通道（与玩家模型互斥，前端会把它从玩家列表里禁用）。
# 定义成常量是因为它有 5 个落点（会话默认 / 请求模型 / 默认值接口 / 开局回退 / 断点回退），
# 散落的字面量改漏一处就会出现「前端显示 A、后端实跑 B」。
_DEFAULT_REFEREE_MODEL = "deepseek-flash"

# MIME 类型映射（图片）
_IMG_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
}


# ── 游戏会话 ─────────────────────────────────────────────────────

class EventLog:
    """有界广播日志，每个连接独立游标；结束事件可重复读取。"""

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.entries: deque = deque(maxlen=4096)
        self.sequence = 0
        self.closed = False

    def put(self, event) -> None:
        with self.condition:
            if self.closed:
                return
            self.sequence += 1
            if event is None:
                event = {"type": "stream_end"}
                self.closed = True
            self.entries.append((self.sequence, event))
            self.condition.notify_all()

    def read(self, cursor: int, timeout: float = 20):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence > cursor or self.closed, timeout)
            if self.entries and cursor < self.entries[0][0] - 1:
                return [(self.sequence, {"type": "resync_required"})], True
            return [(i, event) for i, event in self.entries if i > cursor], self.closed


@dataclass
class GameSession:
    game_id: str
    event_queue: EventLog = field(default_factory=EventLog)
    advance_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    auto_advance: bool = True
    finished: bool = False
    finished_at: float = 0.0
    error: str = ""
    players_info: list = field(default_factory=list)
    summary_role: str = ""
    summary_model_id: str = ""
    referee_model: str = _DEFAULT_REFEREE_MODEL
    referee_role: str = "base_model"
    phase: str = "starting"
    round_no: int = 0
    replay_ready: bool = False


class PlayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["base_model", "adv_model"]
    model_id: StrictStr = Field(min_length=1, max_length=128)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    players: list[PlayerRequest] = Field(min_length=3, max_length=12)
    image_civilian: StrictStr = Field(pattern=r"^[0-9a-f]{32}\.(png|jpg|jpeg|gif|webp|bmp)$")
    image_spy: StrictStr = Field(pattern=r"^[0-9a-f]{32}\.(png|jpg|jpeg|gif|webp|bmp)$")
    spy_count: Optional[StrictInt] = None
    seed: Optional[StrictInt] = None
    order_mode: Literal["rotate", "fixed"] = "rotate"
    auto_advance: bool = True
    summary_model: Optional[PlayerRequest] = None
    referee_model: StrictStr = Field(default=_DEFAULT_REFEREE_MODEL, min_length=1, max_length=128)
    referee_role: StrictStr = Field(default="base_model", min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_players(self):
        keys = [(p.role, p.model_id) for p in self.players]
        if len(set(keys)) != len(keys):
            raise ValueError("玩家不能重复")
        if self.spy_count is not None and not 1 <= self.spy_count < len(keys) / 2:
            raise ValueError("卧底人数需至少 1 且小于玩家数的一半")
        if self.summary_model and (self.summary_model.role, self.summary_model.model_id) in keys:
            raise ValueError("裁判模型不能与参与玩家相同")
        if (self.referee_role, self.referee_model) in keys:
            raise ValueError("裁判模型不能与参与玩家相同")
        return self


# ── 服务器状态 ───────────────────────────────────────────────────

@dataclass
class ServerState:
    config: ConfigBundle
    replay_store: ReplayStore
    html: bytes
    games: Dict[str, GameSession] = field(default_factory=dict)
    games_lock: threading.Lock = field(default_factory=threading.Lock)
    game_slots: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(2))


# ── 请求处理器 ───────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    server_version = "IrisUndercoverWeb/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.debug("HTTP %s", format % args)

    # ── 辅助 ────────────────────────────────────────────────────

    def _state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: Any) -> None:
        if code >= 400 and isinstance(data, dict) and "error" in data:
            data = {**data, "code": {400: "INVALID_REQUEST", 403: "FORBIDDEN",
                    404: "NOT_FOUND", 409: "INVALID_STATE", 429: "BUSY"}.get(code, "SERVER_ERROR"),
                    "retryable": code in (429, 503)}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支持分块请求")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            raise ValueError("需要唯一 Content-Length")
        length = int(lengths[0])
        if not 0 <= length <= 20 * 1024 * 1024:
            raise ValueError("请求体超过 20 MB 或长度非法")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("请求体不完整")
        return body

    def _check_origin(self) -> bool:
        address = cast(tuple, self.server.server_address)
        expected = f"{address[0]}:{address[1]}"
        hosts = {expected, f"localhost:{address[1]}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in hosts or (origin and origin != f"http://{host}"):
            self._json(403, {"error": "禁止跨站访问"})
            return False
        return True

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)


    def _parse_path(self) -> tuple[str, str]:
        """返回 (path, query_string)。"""
        parsed = urlparse(self.path)
        return parsed.path, parsed.query

    # ── GET ─────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_origin():
            return
        path, _ = self._parse_path()

        if path in ("/", "/index.html"):
            self._send(200, self._state().html, "text/html; charset=utf-8")
            return

        if path == "/api/models":
            self._handle_models()
            return

        if path == "/api/config/defaults":
            self._handle_config_defaults()
            return

        if path == "/api/games/incomplete":
            self._handle_incomplete_games()
            return

        if path.startswith("/uploads/"):
            token = path[len("/uploads/"):]
            self._handle_upload_preview(token)
            return

        if path.startswith("/api/game/") and path.endswith("/events"):
            game_id = path[len("/api/game/"):-len("/events")]
            self._handle_sse(game_id)
            return

        if path.startswith("/api/game/"):
            game_id = path[len("/api/game/"):]
            self._handle_game_status(game_id)
            return

        if path == "/api/history":
            self._handle_history_list()
            return

        if path.startswith("/api/history/"):
            rest = path[len("/api/history/"):]
            parts = rest.split("/")
            if len(parts) == 1:
                self._handle_history_detail(parts[0])
            elif len(parts) == 3 and parts[1] == "image":
                self._handle_history_image(parts[0], parts[2])
            else:
                self._json(404, {"error": "not_found"})
            return

        self._json(404, {"error": "not_found"})

    def _handle_models(self) -> None:
        state = self._state()
        llm = LLMService(state.config)
        mgr = llm.get_provider().get_model_manager()
        result: Dict[str, list] = {}
        for role in ("base_model", "adv_model"):
            result[role] = mgr.list_models(role)
        self._json(200, result)

    def _handle_config_defaults(self) -> None:
        """返回裁判默认模型和全部多模态玩家列表，供前端预填充配置。"""
        state = self._state()
        llm = LLMService(state.config)
        mgr = llm.get_provider().get_model_manager()
        players = []
        for role in ("base_model", "adv_model"):
            for model_id, cfg in mgr.get_models_by_priority(role):
                # 只返回支持多模态的模型（cfg 可能是 dict 或 dataclass）
                if isinstance(cfg, dict):
                    is_multimodal = cfg.get("multimodal", False)
                else:
                    is_multimodal = getattr(cfg, "multimodal", False)
                if is_multimodal:
                    players.append({"role": role, "model_id": model_id, "multimodal": True})
        self._json(200, {
            "referee_model": _DEFAULT_REFEREE_MODEL,
            "referee_role": "base_model",
            "available_players": players,
        })

    def _handle_incomplete_games(self) -> None:
        """返回有断点但无完整复盘的对局列表。"""
        items = self._state().replay_store.list_incomplete_games()
        self._json(200, items)

    def _handle_upload_preview(self, token: str) -> None:
        p = self._state().replay_store.upload_path(token)
        if p is None:
            self._json(404, {"error": "token_not_found"})
            return
        mime = _IMG_MIME.get(p.suffix.lower(), "application/octet-stream")
        self._send(200, p.read_bytes(), mime)

    def _handle_sse(self, game_id: str) -> None:
        state = self._state()
        with state.games_lock:
            session = state.games.get(game_id)
        if session is None:
            self._json(404, {"error": "game_not_found"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            cursor = max(0, int(self.headers.get("Last-Event-ID", "0")))
        except ValueError:
            cursor = 0
        try:
            while True:
                entries, closed = session.event_queue.read(cursor)
                if not entries:
                    self.wfile.write(b": ping\n\n")
                for event_id, item in entries:
                    payload = json.dumps(item, ensure_ascii=False)
                    self.wfile.write(f"id: {event_id}\ndata: {payload}\n\n".encode("utf-8"))
                    cursor = event_id
                self.wfile.flush()
                if closed:
                    self.close_connection = True
                    break
        except (OSError, TimeoutError):
            pass

    def _handle_history_list(self) -> None:
        items = self._state().replay_store.list_games()
        self._json(200, items)

    def _handle_game_status(self, game_id: str) -> None:
        with self._state().games_lock:
            session = self._state().games.get(game_id)
        if session is None:
            self._json(404, {"error": "game_not_found"})
            return
        with session.event_queue.condition:
            status = "running"
            if session.finished:
                status = "failed" if session.error else "finished"
            elif session.cancel_event.is_set():
                status = "cancelling"
            self._json(200, {
                "game_id": game_id, "status": status, "error": session.error,
                "phase": session.phase, "round_no": session.round_no,
                "auto_advance": session.auto_advance, "replay_ready": session.replay_ready,
                "sequence": session.event_queue.sequence,
            })

    def _handle_history_detail(self, game_id: str) -> None:
        try:
            data = self._state().replay_store.load_game(game_id)
            self._json(200, data)
        except Exception as exc:  # noqa: BLE001
            self._json(404, {"error": str(exc)})

    def _handle_history_image(self, game_id: str, role: str) -> None:
        if role not in ("civilian", "spy"):
            self._json(400, {"error": "role must be civilian or spy"})
            return
        p = self._state().replay_store.image_path(game_id, role)
        if p is None:
            self._json(404, {"error": "image_not_found"})
            return
        mime = _IMG_MIME.get(p.suffix.lower(), "application/octet-stream")
        self._send(200, p.read_bytes(), mime)

    # ── POST ────────────────────────────────────────────────────

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._dispatch_post()
        except (ValueError, TypeError, TimeoutError) as exc:
            self.close_connection = True
            self._json(400, {"error": str(exc)})

    def _dispatch_post(self) -> None:
        if not self._check_origin():
            return
        path, _ = self._parse_path()

        if path == "/api/upload":
            self._handle_upload()
            return

        if path == "/api/game/start":
            self._handle_game_start()
            return

        if path.startswith("/api/game/") and path.endswith("/advance"):
            game_id = path[len("/api/game/"):-len("/advance")]
            self._handle_advance(game_id)
            return

        if path.startswith("/api/game/") and path.endswith("/abort"):
            game_id = path[len("/api/game/"):-len("/abort")]
            self._handle_abort(game_id)
            return

        if path.startswith("/api/game/") and path.endswith("/resume"):
            game_id = path[len("/api/game/"):-len("/resume")]
            self._handle_game_resume(game_id)
            return

        self._json(404, {"error": "not_found"})

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json(400, {"error": "multipart/form-data required"})
            return

        body = self._read_body()
        # 简单解析 multipart：找第一个文件部分的 filename 和内容
        try:
            token, filename = _parse_multipart_file(content_type, body)
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": f"parse failed: {exc}"})
            return

        state = self._state()
        saved_token = state.replay_store.save_upload(token, filename)
        self._json(200, {"token": saved_token, "filename": filename})

    def _handle_game_start(self) -> None:
        body = self._read_body()
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            self._json(400, {"error": "invalid JSON"})
            return

        err = _validate_start_request(req)
        if err:
            self._json(400, {"error": err})
            return

        state = self._state()
        store = state.replay_store

        # 检查图片 token
        civ_token = req["image_civilian"]
        spy_token = req["image_spy"]
        if store.upload_path(civ_token) is None:
            self._json(400, {"error": f"平民图像 token 不存在: {civ_token}"})
            return
        if store.upload_path(spy_token) is None:
            self._json(400, {"error": f"卧底图像 token 不存在: {spy_token}"})
            return

        if not state.game_slots.acquire(blocking=False):
            self._json(429, {"error": "最多同时运行两局，请等待或取消现有对局"})
            return
        game_id = store.new_game_id()
        try:
            civ_file, spy_file = store.copy_images(game_id, civ_token, spy_token)
        except Exception as exc:
            state.game_slots.release()
            self._json(500, {"error": f"图片复制失败: {exc}"})
            return

        civ_path = str(store.game_dir(game_id) / civ_file)
        spy_path = str(store.game_dir(game_id) / spy_file)

        players = [(p["role"], p["model_id"]) for p in req["players"]]
        spy_count = req.get("spy_count") or None
        seed = req.get("seed")
        order_mode = req.get("order_mode", "rotate")
        auto_advance = bool(req.get("auto_advance", True))

        referee_model = req.get("referee_model", _DEFAULT_REFEREE_MODEL)
        referee_role = req.get("referee_role", "base_model")

        # 总结固定由裁判模型产出（界面上的「裁判模型（增量校验 + 总结）」）。
        # 曾经这里只读 req["summary_model"]，而前端改发 referee_model 之后没人再发它——
        # summary_model_id 恒为空，复盘一律记成 summary_status=skipped，总结从未生成过。
        # 仍接受显式传入的 summary_model 以兼容旧客户端。
        sm = req.get("summary_model") or {}
        summary_role = sm.get("role", referee_role)
        summary_model_id = sm.get("model_id", referee_model)

        session = GameSession(
            game_id=game_id,
            auto_advance=auto_advance,
            summary_role=summary_role,
            summary_model_id=summary_model_id,
            referee_model=referee_model,
            referee_role=referee_role,
        )

        with state.games_lock:
            expired = [key for key, game in state.games.items() if game.finished and time.monotonic() - game.finished_at > 3600]
            for key in expired:
                del state.games[key]
            state.games[game_id] = session

        # 构建 players_info（用于 replay.json，is_spy 在游戏结束事件里才知道）
        players_info_base = [
            {"key": f"{role}/{model_id}", "role": role, "model_id": model_id,
             "is_spy": False, "display_name": f"{role}/{model_id}",
             "player_number": idx + 1}
            for idx, (role, model_id) in enumerate(players)
        ]
        session.players_info = players_info_base

        # 启动游戏线程
        t = threading.Thread(
            target=_run_game,
            args=(state, session, civ_path, spy_path, players, spy_count, seed, order_mode,
                  civ_file, spy_file),
            daemon=True,
            name=f"game-{game_id}",
        )
        session.thread = t
        t.start()

        self._json(200, {"game_id": game_id})

    def _handle_advance(self, game_id: str) -> None:
        state = self._state()
        with state.games_lock:
            session = state.games.get(game_id)
        if session is None:
            self._json(404, {"error": "game_not_found"})
            return
        with session.event_queue.condition:
            if session.finished or session.cancel_event.is_set() or session.phase != "waiting":
                self._json(409, {"error": "当前不在等待下一轮状态"})
                return
            session.phase = "continuing"
            session.advance_event.set()
        self._json(200, {"ok": True})

    def _handle_abort(self, game_id: str) -> None:
        state = self._state()
        with state.games_lock:
            session = state.games.get(game_id)
        if session is None:
            self._json(404, {"error": "game_not_found"})
            return
        if session.finished or session.phase == "saving":
            self._json(409, {"error": "对局已结束或正在保存"})
            return
        session.cancel_event.set()
        # 设置 advance_event，让卡在 wait() 的游戏线程能退出
        session.advance_event.set()
        # 等待后台保存部分复盘后再关闭事件流。
        self._json(200, {"ok": True})

    def _handle_game_resume(self, game_id: str) -> None:
        """从断点恢复对局：加载 checkpoint.json，重建 GameSession，继续运行。"""
        # 检查与注册必须处于同一临界区，避免双击或并发请求覆盖仍在运行的会话。
        with self._state().games_lock:
            existing = self._state().games.get(game_id)
            if existing is not None and not existing.finished:
                self._json(409, {"error": "对局仍在运行，无需恢复"})
                return
            self._resume_locked(game_id)

    def _resume_locked(self, game_id: str) -> None:
        """调用方持有 games_lock，完成断点读取和会话注册。"""
        state = self._state()
        store = state.replay_store

        # 验证断点存在且对局尚未完成
        replay_dir = store.game_dir(game_id)
        if (replay_dir / "replay.json").exists():
            self._json(400, {"error": "对局已完成，无需恢复"})
            return
        cp = store.load_checkpoint(game_id)
        if cp is None:
            self._json(404, {"error": "断点不存在或已损坏"})
            return
        if cp.get("resume_version") != 1:
            self._json(409, {"error": "旧断点缺少完整轮次记录，无法安全恢复，请开始新局"})
            return
        setup = cp.get("setup", {})
        players_info = setup.get("players", [])
        players = [(p["role"], p["model_id"]) for p in players_info]
        try:
            UndercoverGame.checkpoint_rounds(cp, [f"{role}/{model}" for role, model in players])
        except UndercoverGameError as exc:
            self._json(409, {"error": str(exc)})
            return
        spy_count = len(cp.get("spy_keys", []))
        seed = setup.get("seed")
        order_mode = setup.get("order_mode", "rotate")
        civ_file = setup.get("image_civilian", "")
        spy_file = setup.get("image_spy", "")
        civ_path = str(replay_dir / civ_file) if civ_file else ""
        spy_path = str(replay_dir / spy_file) if spy_file else ""
        referee_model = setup.get("referee_model", _DEFAULT_REFEREE_MODEL)
        referee_role_v = setup.get("referee_role", "base_model")
        # 同 _handle_game_start：总结由裁判产出；旧断点里可能没有 summary_model_key，
        # 此时回落到裁判而不是留空（留空会让复盘写成 summary_status=skipped）。
        sm_key = setup.get("summary_model_key", "")
        if sm_key:
            summary_role, summary_model_id = (sm_key.split("/", 1) + [""])[:2]
        else:
            summary_role, summary_model_id = referee_role_v, referee_model

        session = GameSession(
            game_id=game_id,
            auto_advance=setup.get("auto_advance", True),
            summary_role=summary_role,
            summary_model_id=summary_model_id,
            referee_model=referee_model,
            referee_role=referee_role_v,
        )
        session.players_info = [
            {**p, "player_number": idx + 1}
            for idx, p in enumerate(players_info)
        ]

        if not state.game_slots.acquire(blocking=False):
            self._json(429, {"error": "最多同时运行两局，请等待或取消现有对局"})
            return
        state.games[game_id] = session

        t = threading.Thread(
            target=_run_game,
            args=(state, session, civ_path, spy_path, players, spy_count, seed, order_mode,
                  civ_file, spy_file),
            kwargs={"checkpoint": cp},
            daemon=True,
            name=f"game-resume-{game_id}",
        )
        session.thread = t
        try:
            t.start()
        except RuntimeError:
            del state.games[game_id]
            state.game_slots.release()
            raise
        self._json(200, {"game_id": game_id, "resumed_from_round": len(cp.get("completed_rounds", [])),
                         "auto_advance": session.auto_advance})


# ── 游戏线程 ─────────────────────────────────────────────────────

def _run_game(state, session, *args, **kwargs) -> None:
    """保证槽位与终态释放；分钟级对局接入任务面板。"""
    from iris.taskpanel.reporter import TaskReporter
    try:
        with TaskReporter("undercover-game", task_id=session.game_id,
                          data_root=Path(state.config.root) / "data") as reporter:
            reporter.report_phase("运行对局")
            _run_game_impl(state, session, *args, **kwargs)
            reporter.report_phase("已取消" if session.cancel_event.is_set() else "保存复盘")
            if session.error and not session.cancel_event.is_set():
                raise RuntimeError(session.error)
    except Exception:
        logger.exception("对局未正常完成 game_id=%s", session.game_id)
    finally:
        session.finished = True
        session.finished_at = time.monotonic()
        session.event_queue.put(_SSE_SENTINEL)
        state.game_slots.release()


def _run_game_impl(
    state: ServerState,
    session: GameSession,
    civ_path: str,
    spy_path: str,
    players: list,
    spy_count,
    seed,
    order_mode: str,
    civ_file: str,
    spy_file: str,
    checkpoint: Optional[Dict[str, Any]] = None,
) -> None:
    """在后台线程运行完整对局，结束后写复盘、触发总结。"""
    game_id = session.game_id
    store = state.replay_store

    # 构建 checkpoint 时用的 setup 数据（用于 save_checkpoint）
    _setup_for_checkpoint: Dict[str, Any] = {
        "players": session.players_info,
        "seed": seed,
        "order_mode": order_mode,
        "auto_advance": session.auto_advance,
        "image_civilian": civ_file,
        "image_spy": spy_file,
        "referee_model": session.referee_model,
        "referee_role": session.referee_role,
        "summary_model_key": f"{session.summary_role}/{session.summary_model_id}" if session.summary_model_id else "",
    }

    def on_event(event_type: str, payload: dict) -> None:
        if session.finished and event_type != "game_end":
            return  # 已中止，丢弃非结束事件
        with session.event_queue.condition:
            session.round_no = payload.get("round_no", session.round_no)
            phases = {"game_start": "starting", "round_start": "describing",
                      "vote_cast": "voting", "game_end": "saving",
                      "round_waiting": "waiting"}
            session.phase = phases.get(event_type, session.phase)
            session.event_queue.put({"type": event_type, "payload": payload})
        # checkpoint 事件触发落盘
        if event_type == "checkpoint":
            try:
                store.save_checkpoint(
                    game_id,
                    setup=_setup_for_checkpoint,
                    completed_rounds=payload.get("completed_rounds", []),
                    alive_keys=payload.get("alive_keys", []),
                    spy_keys=payload.get("spy_keys", []),
                    speaking_order_base=payload.get("speaking_order_base", []),
                )
            except Exception as exc:  # noqa: BLE001 — 断点失败不阻断游戏
                logger.warning("断点写入失败 %s: %s", game_id, exc)

    advance_ev = None if session.auto_advance else session.advance_event

    try:
        game = UndercoverGame(
            state.config,
            image_a_path=civ_path,
            image_b_path=spy_path,
            players=players,
            spy_count=spy_count,
            seed=seed,
            order_mode=order_mode,
            on_event=on_event,
            advance_event=advance_ev,
            cancel_event=session.cancel_event,
            referee_model_id=session.referee_model,
            referee_role=session.referee_role,
        )
        result = game.run(checkpoint=checkpoint) if checkpoint else game.run()
    except Exception as exc:  # noqa: BLE001
        logger.exception("游戏 %s 运行异常", game_id)
        session.event_queue.put({"type": "error", "payload": {"message": str(exc)}})
        session.event_queue.put(_SSE_SENTINEL)
        session.error = str(exc)
        return

    # 更新 players_info 里的 is_spy 字段
    spy_set = set(result.spy_keys)
    players_info = [
        {**p, "is_spy": p["key"] in spy_set}
        for p in session.players_info
    ]
    session.players_info = players_info

    # 写复盘
    sm_key = f"{session.summary_role}/{session.summary_model_id}"
    try:
        state.replay_store.save_result(
            game_id, result, players_info,
            civ_file, spy_file,
            summary_model_key=sm_key,
        )
        session.replay_ready = True
    except Exception as exc:  # noqa: BLE001
        session.error = f"复盘写入失败: {exc}"
        session.event_queue.put({"type": "error", "payload": {"message": session.error}})
        logger.warning("复盘写入失败 %s: %s", game_id, exc)

    # 触发 LLM 总结。取消的对局同样出总结——已跑完的轮次仍有分析价值，
    # 复盘里 winner=cancelled 已经标明这是部分复盘。
    if session.summary_model_id and not session.error:
        try:
            llm = LLMService(state.config)
            state.replay_store.trigger_summary(
                game_id, llm,
                session.summary_role,
                session.summary_model_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("总结触发失败 %s: %s", game_id, exc)

    # 通知 SSE 流结束
    session.event_queue.put(_SSE_SENTINEL)


# ── 校验 & 工具 ──────────────────────────────────────────────────

def _validate_start_request(req: dict) -> str:
    """返回错误字符串，无错时返回空字符串。"""
    try:
        StartRequest.model_validate(req)
    except ValidationError as exc:
        return str(exc)
    return ""


def _parse_multipart_file(content_type: str, body: bytes) -> tuple[bytes, str]:
    """从 multipart/form-data body 中提取第一个文件的内容和文件名。"""
    # 获取 boundary
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
            break
    if not boundary:
        raise ValueError("未找到 boundary")

    sep = ("--" + boundary).encode()
    parts = body.split(sep)

    for raw_part in parts[1:]:
        if raw_part.startswith(b"--"):
            continue
        # 分离 header 和 body
        if b"\r\n\r\n" in raw_part:
            header_bytes, file_body = raw_part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in raw_part:
            header_bytes, file_body = raw_part.split(b"\n\n", 1)
        else:
            continue

        headers_text = header_bytes.decode("utf-8", errors="replace")
        if "filename=" not in headers_text:
            continue

        # 提取文件名
        filename = "upload.bin"
        for line in headers_text.splitlines():
            if "filename=" in line:
                for segment in line.split(";"):
                    segment = segment.strip()
                    if segment.startswith("filename="):
                        filename = segment[len("filename="):].strip('"')
                        break

        # 去掉末尾的 \r\n
        if file_body.endswith(b"\r\n"):
            file_body = file_body[:-2]
        elif file_body.endswith(b"\n"):
            file_body = file_body[:-1]
        return file_body, filename

    raise ValueError("未找到文件部分")


# ── HTTP 服务器 ───────────────────────────────────────────────────

class UndercoverWebServer(ThreadingHTTPServer):
    """谁是卧底 Web 服务器。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: ConfigBundle, host: str = "127.0.0.1", port: int = 7862) -> None:
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("游戏服务仅允许本机监听；远程发布需要独立认证网关")
        self._connections = threading.BoundedSemaphore(16)
        super().__init__((host, port), _Handler)
        data_root = (Path(config.root) / "data") if hasattr(config, "root") else Path("data")
        store = ReplayStore(data_root)

        html = _TEMPLATE.read_bytes() if _TEMPLATE.exists() else b"<h1>Template not found</h1>"

        self.state = ServerState(
            config=config,
            replay_store=store,
            html=html,
        )

    def handle_error(self, request, client_address) -> None:
        """客户端中途断开不是服务端故障，不打整栈到控制台。

        浏览器会开预连接再关掉、刷新页面时取消 SSE 长连接——这些都会让
        `rfile.readline` 抛 ConnectionResetError，默认实现（traceback.print_exc）
        会把几十行栈刷满终端，看着像服务崩了。降为 debug 记录，真出问题时
        打开 debug 日志仍可查；其余异常保持默认行为，不跟着一起吞。
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError,
                            ConnectionAbortedError, TimeoutError)):
            logger.debug("客户端断开 %s（%s）", client_address, type(exc).__name__)
            return
        super().handle_error(request, client_address)

    def process_request(self, request, client_address) -> None:
        if not self._connections.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connections.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        logger.info("谁是卧底 Web 界面已启动，访问 http://%s:%d", self.server_address[0], self.server_address[1])
        super().serve_forever(poll_interval)
