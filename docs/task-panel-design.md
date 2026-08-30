# iris task-panel — 任务面板 方案设计 v1.0

**日期**：2026-08-16 · **最后校验**：2026-08-26 · **状态**：已实现（v3.27.0）· **当前验证版本**：产品 3.28.1 / 协议 3.21

---

## 1. 背景与目标

iris 有大量长任务（build-chunks / build-wiki / transcribe-meeting / daily-start 分钟到小时级）和常驻进程（meeting-live-assistant / asr-corrector）。操作面在 CC CLI，任务面在后台进程，两者分离——需要一个统一视图回答：**什么在跑、跑到哪了、出过什么事**。

### 定位
- **只读展示层**：Web 页面查看任务状态与进程；所有操作仍在 CC CLI（任务由命令自身执行，面板不做 kill/重试）
- **常驻守护**：`iris task-panel start` 后台常驻（可选 launchd 开机自启），浏览器 `http://127.0.0.1:8765` 随时查看
- **零新依赖**：stdlib http.server + 单 HTML + 原生 JS 轮询

### 核心决策（需求确认结论）
| 决策 | 结论 |
|------|------|
| 数据来源 | **混合式**：TaskReporter 埋点（长任务）+ ps 探测兜底（未埋点进程 + stale 判定） |
| 服务生命周期 | 常驻守护（默认手动启动，install 可选开机自启） |
| 技术栈 | 零新依赖（stdlib http.server） |
| 监控范围 | iris 长任务 + 常驻进程（asr-corrector watchdog）；CC 会话任务不在范围内 |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│  任务进程（埋点侧）                                        │
│  with TaskReporter("daily-start") as r:                  │
│      r.report_phase("memory_sync", "第1/8阶段", 1/8)     │
└────────────────────┬────────────────────────────────────┘
                     ▼ 原子写
┌─────────────────────────────────────────────────────────┐
│  data/tasks/current/<task_id>.json   （运行中）            │
│  data/tasks/history.jsonl            （终态，200 条滚动）   │
│  data/tasks/task-panel.pid           （守护 pid）          │
└────────────────────┬────────────────────────────────────┘
                     ▼ 实时读盘
┌─────────────────────────────────────────────────────────┐
│  task-panel 守护进程（ThreadingHTTPServer, 127.0.0.1）    │
│  GET /            静态面板页（启动时读入内存）              │
│  GET /api/state   状态 JSON（每请求顺带 stale 判定）        │
└────────────────────┬────────────────────────────────────┘
                     ▼ fetch 2s 轮询
┌─────────────────────────────────────────────────────────┐
│  浏览器面板：汇总区 + 运行中卡片 + 历史区 + Agent 过滤      │
└─────────────────────────────────────────────────────────┘
  未埋点进程（asr-corrector）← probe watchdog（pid 文件只读探测）
```

**依赖方向**：`reporter → store`；`probe → store`；`server → store + probe`；`daemon → store + probe + server`。无环。

---

## 3. 状态模型与存储

### TaskStatus 字段
`task_id / name / command / agent_id / pid / status / phase / phase_detail / progress / started_at / ended_at / error`

### 状态机（四态）
```
running ──正常退出（with __exit__）──▶ success   ─┐
   │   ──异常（__exit__ 记 error 重抛）─▶ failed    ├─▶ history.jsonl（终态归档）
   │   ──进程被杀/崩溃（无 __exit__）─▶ interrupted（probe 兜底判定）
```

- **task_id** = `name-YYYYmmdd-HHMMSS-pid`：含 pid 保证同名并发多实例互不覆盖，时间戳前缀可排序
- **current/**：原子写（mkstemp + os.replace）；**history**：flock 串行追加 + 锁内幂等守卫（current 已删 / history 已含 task_id 则跳过）+ 250 条截断重写留 200
- **stale 兜底时机**：每次 /api/state 请求顺带执行（面板 2s 轮询即节拍，无独立定时线程）

### 容错红线
TaskReporter 所有磁盘操作失败**静默**（logging.warning）——埋点绝不能破坏 daily-start 等业务命令；`IRIS_TASK_PANEL_DISABLED=1` 全局禁用（测试隔离/逃生通道）。

---

## 4. 守护进程设计

### start（daemonize）
```
只读探测（pid 存活 + TCP 通）→ 已在运行则幂等退出
→ subprocess.Popen([python, -m, iris.taskpanel.daemon, --project-root, <root>],
                   start_new_session=True, stdout/stderr → panel.log)
→ 就绪轮询 0.2s × 25（pid 文件出现 + TCP 可连）
```

⚠️ **只读探测红线**：严禁用 ProcessRegistry.register() 探测（未运行时会写入假 pid 文件造成假占——live.py _probe_running 注释点名的坑）。

### 守护进程本体（main）
- ProcessRegistry("task-panel", data/tasks) 互斥 + `--project-root` 显式传参（不依赖包位置推断，launchd 同传）
- 显式 SIGTERM/SIGINT handler → stop_event（Python 3.13 sleep 不被信号中断的坑）
- **shutdown() 必须在非 serve_forever 线程**调用（分离线程 wait stop_event → server.shutdown），否则死锁

### stop / status / install
- **stop**：读 pid → SIGTERM → 5s 轮询；残留 pid 文件由下次 start 的 stale 清理兜底
- **status**：只读探测 + GET /api/state 取 uptime（API 不通不影响运行判定）
- **install**：plistlib 生成 `~/Library/LaunchAgents/com.iris.task-panel.plist`——RunAtLoad + KeepAlive `{SuccessfulExit: false}`（stop 优雅退出不复活、崩溃自动拉起）+ StandardOut/ErrorPath → panel.log

---

## 5. Web 面板（static/index.html）

| 区域 | 内容 |
|------|------|
| header | 标题 + 连接状态点（绿/灰）+ 守护 pid/端口/uptime + 版本 + 只读提示 |
| 离线横幅 | fetch 失败时显示「执行 iris task-panel start」（保留已渲染数据降级） |
| Agent 过滤 | select（选项来自 /api/state.agents，localStorage 记忆） |
| 汇总区 | 运行中总数 + 类型分布 · 历史成功/失败/中断 · 成功率 |
| 运行中卡片 | 名称 + 状态 pill（呼吸动画）+ 阶段徽标 + 进度条 + 耗时实时重算 + pid/agent + 命令截断 |
| 历史区 | 左色条（绿成功/红失败/黄中断）+ 起止时间 + 耗时 + error 摘要 |
| toast | interrupted_now 命中时黄条提示 |

- **轮询**：2s setInterval + visibilitychange 隐藏暂停/恢复立即刷新（省电）
- **安全**：动态文本全部 textContent（防 XSS）；no-store 缓存头
- **主题**：深色 #0f1115 底 + 语义色（绿/红/黄/青/蓝/紫），呼应 meeting-live-assistant 面板

---

## 6. 埋点接入清单（首批 5+1）

| 命令 | 接入方式 | 阶段 |
|------|---------|------|
| daily-start | `_system.py` with 包裹全流程 | 8 阶段 progress i/8 |
| build-chunks | chunker 加可选 `progress_callback(done, total, source_name)` | 逐文档 + 数据源名 |
| build-wiki | generator.build_pages 加可选回调 | 逐页 len(items) 分母 |
| transcribe-meeting | pipeline.run() with 包裹 Step 1-3 | parse/transcribe/wiki_context/llm_minutes/done |
| meeting-live-assistant | run() register 后 with 包裹 | listening/analyze（每批段数）/summary |
| asr-corrector | 零埋点 | watchdog 探测（仅运行/未运行 + pid） |

**扩展规则**（已固化进 CLAUDE.md 开发约定）：新增长任务/常驻命令必须评估接入 TaskReporter；不接需在需求讨论时说明理由。

**回调约定**：进度回调全部可选参数默认 None——零行为变化，不破坏现有调用方。

---

## 7. 测试计划

| 层 | 内容 | 数量 |
|----|------|:---:|
| unit | store（往返/截断 260→200/并发追加/幂等）· reporter（三态/重抛/静默/多实例）· probe（真假 pid/stale/watchdog）· server（port=0 真实 HTTP/路由/stale 触发/10 线程并发）· daemon（端口解析/plist/status） | 64 |
| integration | start→status→stop 全链路 + 埋点→面板 e2e（真实守护子进程 + 真实 HTTP）+ stale 兜底 | 5 |

---

## 8. 风险与边界

| 风险 | 对策 |
|------|------|
| 埋点破坏业务命令 | 全部静默 + disabled 开关；回调默认 None |
| 多 Agent 并发写 | task_id 含 pid 文件唯一；history flock + 幂等守卫 |
| daemon 进程泄漏 | 只读探测红线 + stop 超时残留由 start stale 清理兜底 |
| 端口冲突 | 就绪探测失败提示 IRIS_TASK_PANEL_PORT；不主动杀他人进程 |
| panel.log 增长 | 无限追加（本版不做轮转，登记待办） |
| 既有 bug 边界 | pipeline.run_batch 不存在（batch-transcribe 路径无埋点，不在本版修） |
