# 谁是卧底 Web UI — 实施计划

> **归档件**：本文是 v3.39.0 开发前的设计稿，记录「当初为什么这么切分」的决策依据（逐文件改动清单与行数预算、实施顺序、边界约束）。实现已于 v3.39.0 完成合入，**其中的行数预算与最终代码有出入，不要当作现状描述读**。面向使用者的说明见 [undercover-web-ui.md](undercover-web-ui.md)。

## 概览

在不破坏现有 CLI 命令的前提下，为「谁是卧底」对抗游戏增加一个 Web 界面，支持：
- 游戏设置（模型选择、图片上传、卧底数量、裁判模型）
- 实时观战（自动/手动步进，思考与输出分层展示，颜色区分）
- 历史对战复盘（存储、查看、LLM 总结）

技术框架：stdlib `http.server`（与 taskpanel 保持一致，零外部依赖），SSE 推送事件。

---

## 文件改动清单

### 1. `src/iris/games/undercover.py` — 最小改动（+约 50 行）

**改动位置**：`__init__` 新增两个关键字参数，`run()` 内部各关键节点 emit 事件。

**新增参数**：
```python
def __init__(
    self,
    config: ConfigBundle,
    *,
    ...（现有参数不变）...
    on_event: Optional[Callable[[str, dict], None]] = None,   # 事件回调
    advance_event: Optional["threading.Event"] = None,        # 手动步进锁
):
```

**事件 emit 节点**（在 `run()` 和子方法中调用 `self._emit(event_type, payload)`）：

| 节点 | event_type | 关键 payload 字段 |
|---|---|---|
| `run()` 开头 | `game_start` | players（含 role/model_id/key/is_spy）、spy_keys、image_civilian、image_spy、seed、spy_count |
| 每轮 while 开头 | `round_start` | round_no、speaking_order |
| `_describe_one()` 结束后 | `player_speech` | round_no、key、public: {description, response, status}、private: {observation_list, public_elements, withheld_elements, self_identity, self_confidence, self_reason} |
| `_run_vote_phase()` 每个玩家投票完成 | `vote_cast` | round_no、key、target、reason |
| `_resolve_elimination()` 后 | `elimination` | round_no、eliminated、was_spy、tally |
| 每轮末 `result.rounds.append` 后 | `round_end` | round_no、elapsed_sec、silent、malformed |
| 手动步进等待点 | 在 `round_end` emit 后，若 `advance_event` 非 None，调用 `advance_event.wait(timeout=300)` 后 `advance_event.clear()` |
| `return result` 前（各 winner 分支） | `game_end` | winner、final_survivors、spy_keys、total_rounds |

**私有方法**：
```python
def _emit(self, event_type: str, payload: dict) -> None:
    if self._on_event:
        try:
            self._on_event(event_type, payload)
        except Exception:
            pass  # 事件回调失败不影响游戏主流程
```

---

### 2. `src/iris/games/replay_store.py` — 新建（~200 行）

**职责**：复盘存储、读取、LLM 总结触发。

**目录结构**：
```
data/games/
└── 20260915-143022-a3f7/
    ├── civilian.<ext>
    ├── spy.<ext>
    ├── replay.json
    └── summary.md        ← 异步生成
```

**关键接口**：
```python
class ReplayStore:
    def __init__(self, data_root: Path): ...

    def create_game_dir(self, game_id: str) -> Path:
        """创建对局目录，返回路径。"""

    def save_images(self, game_id: str, civilian_path: str, spy_path: str) -> tuple[str, str]:
        """复制图片到对局目录，返回 (civilian_rel, spy_rel)。"""

    def save_result(self, game_id: str, result: GameResult, player_objects: list) -> None:
        """将 GameResult 序列化写入 replay.json（含 player 的 is_spy 信息）。"""

    def list_games(self) -> list[dict]:
        """列出所有对局，按时间倒序，含摘要字段。"""

    def load_game(self, game_id: str) -> dict:
        """加载单个对局完整数据（含 summary_ready 状态）。"""

    def trigger_summary(self, game_id: str, llm: LLMService, base_model: str, base_role: str) -> None:
        """在后台线程生成 LLM 总结，完成后更新 summary_ready。"""
```

**replay.json schema**：
```json
{
  "id": "20260915-143022-a3f7",
  "created_at": "2026-09-15T14:30:22",
  "setup": {
    "players": [{"key": "...", "role": "...", "model_id": "...", "is_spy": false, "display_name": "..."}],
    "spy_keys": ["..."],
    "spy_count": 1,
    "seed": 42,
    "image_civilian": "civilian.jpg",
    "image_spy": "spy.jpg"
  },
  "result": {
    "winner": "civilians",
    "final_survivors": ["..."],
    "total_rounds": 4
  },
  "rounds": [...完整 RoundRecord.to_dict() 列表，speeches 含全部私有字段...],
  "summary_ready": false
}
```

**LLM 总结 prompt 提纲**（`_build_summary_prompt`）：
- 对局概况（身份揭晓后的完整玩家列表 + 卧底标注 + 胜负）
- 各轮公开描述 + 投票 + 淘汰事件
- 要求输出：总览段 → 关键转折点（哪轮暴露了破绽）→ 各模型表现点评 → 裁判总评

---

### 3. `src/iris/games/web_server.py` — 新建（~500 行）

**技术选型**：stdlib `ThreadingHTTPServer`（对齐 taskpanel），SSE 用 `text/event-stream` 手写。

**路由**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 返回 HTML 单页（从 templates/ 读入内存） |
| GET | `/api/models` | 返回可用模型列表（两个 role 分别列出） |
| POST | `/api/upload` | 上传图片，返回 `{"token": "..."}` |
| GET | `/uploads/<token>` | 返回上传的图片文件（用于预览） |
| POST | `/api/game/start` | 启动游戏，返回 `{"game_id": "..."}` |
| GET | `/api/game/<id>/events` | SSE 流 |
| POST | `/api/game/<id>/advance` | 手动步进（设置 advance_event） |
| POST | `/api/game/<id>/abort` | 终止游戏 |
| GET | `/api/history` | 历史对局列表 |
| GET | `/api/history/<id>` | 单个对局完整数据 |
| GET | `/api/history/<id>/image/<role>` | 返回复盘图片（role=civilian/spy） |

**并发模型**：
- 每局游戏在独立后台线程运行
- `on_event` 回调把事件 put 进 `queue.Queue`
- SSE handler 在请求线程 get 并 flush
- 游戏实例与 Queue、advance_event 挂在 `_games: dict[str, GameSession]` 上

**`/api/game/start` 请求体校验**：
```json
{
  "players": [{"role": "base_model", "model_id": "claude-opus"}],
  "summary_model": {"role": "base_model", "model_id": "claude-sonnet"},
  "image_civilian": "<upload_token>",
  "image_spy": "<upload_token>",
  "spy_count": 1,
  "seed": null,
  "order_mode": "rotate",
  "auto_advance": true
}
```

后端校验：
1. `summary_model` 的 `(role, model_id)` 不在 `players` 列表中
2. players 去重后 ≥ 3
3. spy_count 合法
4. 两张图 token 存在且对应文件存在

**SSE 事件格式**：
```
data: {"type": "game_start", "payload": {...}}

data: {"type": "player_speech", "payload": {...}}

data: {"type": "game_end", "payload": {...}}

data: {"type": "error", "payload": {"message": "..."}}
```

---

### 4. `src/iris/games/templates/undercover_web.html` — 新建（~1000 行）

**单文件 SPA，三个视图通过 JS 切换（无路由库）**：

#### 视图一：设置页（`#view-setup`）

- 玩家列表：动态增删行，每行 [角色下拉 | 模型 ID 下拉]，模型 ID 按选中角色动态填充
- 裁判模型：独立下拉，动态排除已选为玩家的 (role, model_id)，实时联动
- 图片上传：两个拖拽区域，上传后展示缩略图预览
- 卧底数量：数字输入（1 起，最大值 = (players.length - 1) / 2 向下取整）
- 运行模式：自动 / 手动步进 radio
- 随机种子：可选文本框（空 = 随机）
- [开始游戏] 按钮：前端验证后 POST /api/game/start

#### 视图二：观战页（`#view-game`）

布局：
```
顶部状态栏：第 N 轮 | 描述/投票阶段 | [下一轮] (手动模式) | [终止]
玩家卡片横排：每人一张卡（颜色、存活/淘汰状态）
时间线：垂直滚动，新事件追加到底部，自动滚动
```

颜色系统（JS 动态分配）：
```javascript
function playerColors(index, total) {
    const h = Math.round((index / total) * 360);
    return {
        main:   `hsl(${h}, 58%, 42%)`,   // 描述卡左边框
        muted:  `hsl(${h}, 35%, 90%)`,   // 思考卡背景
        text:   `hsl(${h}, 50%, 28%)`,   // 思考卡文字
        badge:  `hsl(${h}, 58%, 42%)`,   // 玩家卡背景
    };
}
```

时间线卡片类型：
1. **思考卡**（`player_speech` 事件，private 部分）
   - muted 背景 + 斜体 + 🔒 标签 + "仅观战者可见"小字
   - 折叠展示观察清单，点击展开
   - 显示身份自评（平民/卧底/不确定）+ 置信度 + 依据

2. **描述卡**（`player_speech` 事件，public 部分）
   - main 色左边框 + 白底
   - 描述 + 回应两段

3. **投票卡**（`vote_cast` 事件）
   - main 色左边框（淡化版）
   - "投票给 XXX：原因"

4. **裁判陈述**（`round_start` / `round_end` / `elimination` / `game_end`）
   - 无色条，`hsl(0,0%,22%)` 文字，灰色背景
   - elimination 追加 ⚠️ 并标注是否为卧底

5. **游戏结束横幅**（`game_end`）
   - 全宽，胜负颜色（平民蓝/卧底红/平局灰）

SSE 连接：
```javascript
const es = new EventSource(`/api/game/${gameId}/events`);
es.onmessage = e => handleEvent(JSON.parse(e.data));
```

#### 视图三：历史列表页（`#view-history`）

- 卡片网格，每张卡：日期时间、胜者、模型数、卧底标注、是否有总结
- 点击进入复盘详情（内嵌在同一页面，推入 `#view-replay`）

#### 视图四：复盘详情页（`#view-replay`）

- 顶部：两张图并排（平民/卧底），胜负横幅，总轮数
- 裁判总结区（若 summary_ready=false 显示"生成中..."，轮询）
- 轮次折叠列表：默认折叠，点击展开后渲染与观战视图相同的时间线
- [👁 显示私有思考] toggle（默认关闭）
- [⬇ 导出 JSON] 按钮

---

### 5. `src/iris/app/cli/_handlers/_games.py` — 追加（+约 40 行）

新增 `handle_undercover_game_web` handler：

```python
def handle_undercover_game_web(args, bundle, logger) -> int:
    """启动谁是卧底 Web 界面。"""
    from iris.games.web_server import UndercoverWebServer
    port = getattr(args, "port", 7862)
    host = getattr(args, "host", "127.0.0.1")
    server = UndercoverWebServer(bundle, host=host, port=port)
    logger.log("undercover_game_web", {"host": host, "port": port})
    print(f"游戏界面已启动：http://{host}:{port}")
    server.serve_forever()
    return 0
```

`GAMES_HANDLERS` 追加：`"undercover-game-web": handle_undercover_game_web`

argparse 参数：
- `--port`：默认 7862
- `--host`：默认 127.0.0.1

---

## 实施顺序

1. **undercover.py 事件钩子**（基础，后续依赖它）
2. **replay_store.py**（独立，可并行测试）
3. **web_server.py**（依赖 1 和 2）
4. **undercover_web.html**（依赖 3 提供的 API）
5. **_games.py CLI 注册**（最后接入）

---

## 关键约束与边界

- 不引入任何新的 Python 包依赖（Flask、FastAPI 等均不引入）
- 图片上传暂存到 `data/games/uploads/`，生命周期：服务进程重启后清理
- 复盘图片从对局目录读取，有独立路由 `/api/history/<id>/image/<role>`
- LLM 总结在 `game_end` 事件发出后在后台线程触发，失败不影响对局存档
- `advance_event.wait(timeout=300)` 防止手动模式下窗口关闭后游戏永远卡住
- 游戏线程结束后，SSE 队列放入 sentinel `None` 让 SSE handler 正常关闭
- 现有 `undercover-game` CLI 命令行为不受任何影响（`on_event=None` 时 `_emit` 是 no-op）
