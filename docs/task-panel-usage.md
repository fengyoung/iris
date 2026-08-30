# 任务面板 — 使用指南

> 当前验证版本：Iris 3.28.1 · 功能基线：v3.27.0。Web 只读展示层：在浏览器中查看 iris 任务状态与进程。操作仍在 CC CLI——面板不做任何任务操作（无 kill/重试），只回答「什么在跑、跑到哪了、出过什么事」。

## 快速开始

```bash
# 1. 启动面板守护进程（后台常驻，关终端不杀）
iris task-panel start
# → 任务面板已启动: http://127.0.0.1:8765

# 2. 浏览器打开 http://127.0.0.1:8765
# 3. 在 CC CLI 中正常执行 iris 长任务（daily-start / build-wiki / …）
#    → 面板自动显示任务卡片（阶段/进度/耗时），每 2s 刷新
# 4. 任务结束自动归档历史（成功绿/失败红/中断黄）
```

> 可选：`iris task-panel install` 生成开机自启配置（launchd，崩溃自动拉起）。

## 命令

```bash
iris task-panel start          # 启动守护（已运行时幂等退出）
iris task-panel start --port 9000   # 指定端口
iris task-panel stop           # 停止守护（SIGTERM 优雅退出）
iris task-panel status         # 状态 + 面板 URL + 已运行时长
iris task-panel install        # 生成 ~/Library/LaunchAgents/com.iris.task-panel.plist
                               #   启用: launchctl load <plist>
```

**端口优先级**：`--port` > `IRIS_TASK_PANEL_PORT` 环境变量 > 8765（校验 1024-65535）。

## 面板说明

```
┌──────────────────────────────────────────────────────────┐
│ 📊 Iris 任务面板  ●已连接  守护 pid 123 · 端口 8765 · 2m   │
├──────────────────────────────────────────────────────────┤
│ [Agent 过滤: 全部 ▾]                                       │
│ ┌ 运行中 2 · daily-start 1 · build-wiki 1 ┐ ┌ 历史成功 18 ┐ │
│ ┌ 历史失败 2 ┐ ┌ 历史中断 1 ┐ ┌ 成功率 86%（近 21 条） ┐     │
├──────────────────────────────────────────────────────────┤
│ ▶ 运行中                                                   │
│  ┌ daily-start [运行中]                                    │
│    [wiki_maintenance] 第5/8阶段：Wiki 维护                  │
│    ██████████░░░░░░ 62%                                    │
│    ⏱ 3分25秒 · pid 4567 · 启动 08-16 10:03                 │
│    daily-start                                             │
│  └────────────────────────────────────────┘                │
│ ✔ 历史（最近 200 条）                                       │
│  ▌ build-chunks   成功   10:03 → 10:15 · 耗时 12分3秒       │
│  ▌ transcribe-meeting  失败  RuntimeError: LLM 超时         │
│  ▌ build-wiki     中断   进程被终止或崩溃（pid 不存在）      │
└──────────────────────────────────────────────────────────┘
```

**任务状态四种**：

| 状态 | 含义 | 颜色 |
|------|------|:---:|
| 运行中 | 当前执行中（阶段/进度实时更新） | 青 |
| 成功 | 正常退出 | 绿 |
| 失败 | 异常退出（error 记录原因） | 红 |
| 中断 | 进程被杀/崩溃（探测兜底判定，无需任务自己上报） | 黄 |

**Agent 过滤**：多 Agent 并发时按 `IRIS_AGENT_ID` 过滤任务卡片（选项自动收集，选择记忆在浏览器）。

## 哪些任务会出现在面板

**已埋点**（阶段/进度完整）：
- `daily-start` — 8 阶段进度
- `build-chunks` — 逐文档切块进度（含数据源名）
- `build-wiki` — 逐页生成进度
- `transcribe-meeting` — 5 阶段（解析/转写/Wiki 上下文/LLM 纪要/写盘）
- `meeting-live-assistant` — 聆听/分析段数/会议总结

**探测兜底**（仅运行/未运行 + pid）：
- `asr-corrector` — 未埋点，由 watchdog 只读探测显示

**不产生记录**：参数错误（如文件不存在）——瞬时失败不算任务执行。

## 数据位置

```
data/tasks/current/<task_id>.json   运行中任务（原子写）
data/tasks/history.jsonl            终态归档（200 条滚动）
data/tasks/task-panel.pid           守护进程 pid
data/tasks/panel.log                守护进程日志（排查用）
```

## 常见问题

**Q: 面板打不开 / 显示「守护进程未运行」？**
A: 执行 `iris task-panel start`。若启动失败，看 `data/tasks/panel.log` 尾部（start 命令失败时也会自动打印）。

**Q: 任务在跑但面板没显示？**
A: 面板只显示 v3.27.0 起已埋点的命令和 watchdog 进程；其他进程暂不感知。任务进程与面板服务须使用同一项目根（`--project-root`）。

**Q: 任务被 Ctrl+C / kill 了，历史显示什么？**
A: 显示「中断」（黄色）——面板每次轮询探测 pid 存活，发现进程死亡自动判定，无需任务上报。

**Q: 会记录 CC 会话里的操作吗？**
A: 不会。面板只追踪 iris 自身命令的执行，CC 会话任务不在范围内。

**Q: 端口被占用怎么办？**
A: `IRIS_TASK_PANEL_PORT=9000 iris task-panel start` 或 `--port` 换端口；面板不会主动占用/终止他人进程。

**Q: install 后开机自启如何移除？**
A: `launchctl unload ~/Library/LaunchAgents/com.iris.task-panel.plist` 并删除 plist 文件。

**Q: 想让新任务也出现在面板？**
A: 在新任务代码里 `with TaskReporter("<name>") as r: r.report_phase(...)`（磁盘错误全静默，不影响业务）；常驻进程可只依赖探测兜底。详见 [task-panel-design.md](task-panel-design.md) 第 6 节。
