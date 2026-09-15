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
import queue
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from iris.config.loader import ConfigBundle
from iris.games.replay_store import ReplayStore
from iris.games.undercover import UndercoverGame
from iris.llm.service import LLMService

logger = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "undercover_web.html"
_SSE_SENTINEL = None   # 游戏结束或中止时 put 进队列，通知 SSE handler 退出

# MIME 类型映射（图片）
_IMG_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
}


# ── 游戏会话 ─────────────────────────────────────────────────────

@dataclass
class GameSession:
    game_id: str
    event_queue: queue.Queue = field(default_factory=queue.Queue)
    advance_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    auto_advance: bool = True
    finished: bool = False
    error: str = ""
    # 游戏结束后保存最终 players_info，供 replay_store 使用
    players_info: list = field(default_factory=list)
    summary_role: str = ""
    summary_model_id: str = ""


# ── 服务器状态 ───────────────────────────────────────────────────

@dataclass
class ServerState:
    config: ConfigBundle
    replay_store: ReplayStore
    html: bytes
    games: Dict[str, GameSession] = field(default_factory=dict)
    games_lock: threading.Lock = field(default_factory=threading.Lock)


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
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _parse_path(self) -> tuple[str, str]:
        """返回 (path, query_string)。"""
        parsed = urlparse(self.path)
        return parsed.path, parsed.query

    # ── GET ─────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path, _ = self._parse_path()

        if path in ("/", "/index.html"):
            self._send(200, self._state().html, "text/html; charset=utf-8")
            return

        if path == "/api/models":
            self._handle_models()
            return

        if path.startswith("/uploads/"):
            token = path[len("/uploads/"):]
            self._handle_upload_preview(token)
            return

        if path.startswith("/api/game/") and path.endswith("/events"):
            game_id = path[len("/api/game/"):-len("/events")]
            self._handle_sse(game_id)
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
            while True:
                try:
                    item = session.event_queue.get(timeout=20)
                except queue.Empty:
                    # 心跳，防止代理超时
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue

                if item is _SSE_SENTINEL:
                    # 游戏结束
                    self.wfile.write(b"data: {\"type\":\"stream_end\"}\n\n")
                    self.wfile.flush()
                    break

                payload = json.dumps(item, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_history_list(self) -> None:
        items = self._state().replay_store.list_games()
        self._json(200, items)

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

        game_id = store.new_game_id()
        try:
            civ_file, spy_file = store.copy_images(game_id, civ_token, spy_token)
        except Exception as exc:
            self._json(500, {"error": f"图片复制失败: {exc}"})
            return

        civ_path = str(store.game_dir(game_id) / civ_file)
        spy_path = str(store.game_dir(game_id) / spy_file)

        players = [(p["role"], p["model_id"]) for p in req["players"]]
        spy_count = req.get("spy_count") or None
        seed = req.get("seed") or None
        order_mode = req.get("order_mode", "rotate")
        auto_advance = bool(req.get("auto_advance", True))

        sm = req.get("summary_model", {})
        summary_role = sm.get("role", "base_model")
        summary_model_id = sm.get("model_id", "")

        session = GameSession(
            game_id=game_id,
            auto_advance=auto_advance,
            summary_role=summary_role,
            summary_model_id=summary_model_id,
        )
        if not auto_advance:
            # 手动模式：首轮自动开始，轮结束后等待 advance
            session.advance_event.set()

        with state.games_lock:
            state.games[game_id] = session

        # 构建 players_info（用于 replay.json，is_spy 在游戏结束事件里才知道）
        players_info_base = [
            {"key": f"{role}/{model_id}", "role": role, "model_id": model_id,
             "is_spy": False, "display_name": f"{role}/{model_id}"}
            for role, model_id in players
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
        session.advance_event.set()
        self._json(200, {"ok": True})

    def _handle_abort(self, game_id: str) -> None:
        state = self._state()
        with state.games_lock:
            session = state.games.get(game_id)
        if session is None:
            self._json(404, {"error": "game_not_found"})
            return
        session.finished = True
        session.error = "aborted"
        # 设置 advance_event，让卡在 wait() 的游戏线程能退出
        session.advance_event.set()
        # 推送哨兵让 SSE 关闭
        session.event_queue.put(_SSE_SENTINEL)
        self._json(200, {"ok": True})


# ── 游戏线程 ─────────────────────────────────────────────────────

def _run_game(
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
) -> None:
    """在后台线程运行完整对局，结束后写复盘、触发总结。"""
    game_id = session.game_id

    def on_event(event_type: str, payload: dict) -> None:
        if session.finished and event_type != "game_end":
            return  # 已中止，丢弃非结束事件
        session.event_queue.put({"type": event_type, "payload": payload})

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
        )
        result = game.run()
    except Exception as exc:  # noqa: BLE001
        logger.exception("游戏 %s 运行异常", game_id)
        session.event_queue.put({"type": "error", "payload": {"message": str(exc)}})
        session.event_queue.put(_SSE_SENTINEL)
        session.finished = True
        return

    if session.finished:
        # 游戏被中止，补发哨兵（若未发过）
        session.event_queue.put(_SSE_SENTINEL)
        return

    session.finished = True

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
    except Exception as exc:  # noqa: BLE001
        logger.warning("复盘写入失败 %s: %s", game_id, exc)

    # 触发 LLM 总结
    if session.summary_model_id:
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
    players = req.get("players")
    if not isinstance(players, list) or len(players) < 3:
        return "players 需至少 3 个"

    seen: set = set()
    for p in players:
        if not isinstance(p, dict):
            return "players 每项需为 {role, model_id} 对象"
        role = p.get("role", "")
        model_id = p.get("model_id", "")
        if role not in ("base_model", "adv_model") or not model_id:
            return f"非法玩家配置: {p}"
        key = f"{role}/{model_id}"
        if key in seen:
            return f"重复玩家: {key}"
        seen.add(key)

    sm = req.get("summary_model", {})
    sm_role = sm.get("role", "")
    sm_model = sm.get("model_id", "")
    if sm_role and sm_model:
        sm_key = f"{sm_role}/{sm_model}"
        if sm_key in seen:
            return f"裁判模型不能与参与玩家相同: {sm_key}"

    if "image_civilian" not in req or "image_spy" not in req:
        return "需要 image_civilian 和 image_spy"

    spy_count = req.get("spy_count")
    if spy_count is not None:
        n = len(players)
        if not (1 <= spy_count < n / 2):
            return f"spy_count={spy_count} 不合法（需 ≥1 且小于玩家数的一半）"

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
        file_body = file_body.rstrip(b"\r\n")
        return file_body, filename

    raise ValueError("未找到文件部分")


# ── HTTP 服务器 ───────────────────────────────────────────────────

class UndercoverWebServer(ThreadingHTTPServer):
    """谁是卧底 Web 服务器。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: ConfigBundle, host: str = "127.0.0.1", port: int = 7862) -> None:
        super().__init__((host, port), _Handler)
        data_root = (Path(config.root) / "data") if hasattr(config, "root") else Path("data")
        store = ReplayStore(data_root)

        html = _TEMPLATE.read_bytes() if _TEMPLATE.exists() else b"<h1>Template not found</h1>"

        self.state = ServerState(
            config=config,
            replay_store=store,
            html=html,
        )

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        logger.info("谁是卧底 Web 界面已启动，访问 http://%s:%d", self.server_address[0], self.server_address[1])
        super().serve_forever(poll_interval)
