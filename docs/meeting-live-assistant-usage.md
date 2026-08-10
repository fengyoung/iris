# 实时会议助理 — 使用指南

> Iris 3.23.0 · 会议中实时转写 + 逐段提炼要点/风险/问题/决策点 + 提示关键提问，过程实时写入 Markdown 文档。

## 快速开始

```bash
# 1. 启动会议助理（保持终端在前台）
iris meeting-live-assistant

# 2. 按住 vocotype 右 Option 说话（与平时一致），说一段、松开
# 3. 松开后自动：转写 → 校正 → 知识库检索 → 分析 → 面板实时更新
# 4. Ctrl+C 结束会议：打印统计帧，过程文档已完整
```

> ⚠️ **互斥**：与 `asr-corrector` 同时只能运行一个（都独占剪贴板）。启动时检测到对方实例会提示让位。

## 命令

```bash
iris meeting-live-assistant                    # 默认输出 data/meeting-live/YYYYMMDD-HHMM-会议记录.md
iris meeting-live-assistant --output ~/会议.md # 自定义路径（可指到 SOURCE 目录归档，自动带 frontmatter）
```

## 前置条件

- 安装 [VocoType](https://vocotype.com)（免费桌面版），录音热键为右 Option（键码 61，默认配置）
- macOS（剪贴板监听依赖 macOS API）
- Iris 已配置 LLM（`.env` 中的 API Key）与知识库（检索上下文，缺失时自动降级为无上下文分析）
- 替换词典/校正 Prompt（`build-asr-prompt --deploy` 产物）缺失时自动降级为仅词典/仅原文，不阻塞启动

## 配置（config/app.json）

```jsonc
"assistant": {
  "output_dir": "",        // 过程文档输出目录（默认 data/meeting-live/，--output 参数优先）
  "top_k": 5,              // 每段知识库检索条数
  "llm_model": "",         // 段分析 LLM 模型（空=走全局路由）
  "poll_interval": 0.5,    // 剪贴板轮询间隔（秒）
  "doc_rewrite_every": 1   // 每 N 段重写文档（1=每段）
}
```

**路径优先级**：`--output` > `assistant.output_dir` > `data/meeting-live/YYYYMMDD-HHMM-会议记录.md`

## 工作链路

```
说话 → vocotype 转写（右 Option 按住-松开）→ 剪贴板 → Iris 特征判定
  → 词典校正（毫秒级，立即显示）→ LLM 深度校正 ∥ 知识库检索（并行，10s 窗口）
  → LLM 分析（要点/风险/问题/决策点/建议提问，15s deadline）
  → 终端面板 + 过程文档原子重写
```

**积压策略**：说话快于分析时只处理最新段（中间段丢弃，面板显示「积压丢弃 N 段」），保证追得上会议节奏。

**降级链**：LLM 不可用 → 仅词典校正 + 面板/文档显示「分析不可用」，会议不中断。

## 面板说明

```
╔═══ 实时会议助理 · 已处理 3 段（积压丢弃 1 段）· 已处理段 3 ═══
  [11:50:23] 校正文本：……
  ── 本段分析 ──
  ✦ 要点：……   ✔ 决策：……   ⚠ 风险：……   ❓ 问题：……
  💡 建议提问：此刻值得追问的问题（核心价值）
  ── 会议累计（实时）──
  ✦ 要点(5)：……   ✔ 决策(2)：……   ⚠ 风险(1)：……
  ❓ 待解决(2)：……
  Ctrl+C 退出
```

## 过程文档

每段处理完立即写入（frontmatter + 逐段记录 + 会议累计区），**会后直接可用**：

```markdown
---
title: 实时会议记录 2026-08-10 11:50
date: 2026-08-10 11:50
type: 实时会议记录
source: meeting-live-assistant
---
## 📋 会议累计（实时更新）
### 关键要点 / 决策点 / 风险 / 待解决问题
## 🎙 段 1（11:50:23）
**校正文本**：……
**要点** / **风险** / **问题** / **决策点** / **建议提问**
```

- 原子写入（tmp + rename）：进程中断不损坏旧文件
- 归档知识库：`--output` 指到 SOURCE 目录（如 `05-会议纪要/`）即带 frontmatter
- 需要正式纪要时事后另跑 `transcribe-meeting`（本功能只做过程记录，不做会后归档）

## 常见问题

**Q: 启动时报「asr-corrector 正在运行」？**
A: 先 `Ctrl+C` 退出 asr-corrector 再启动会议助理（两者独占剪贴板，不能并行）。

**Q: 说话后面板没有新段？**
A: 检查 vocotype 是否转写并写入剪贴板（用 `verify_hotkey_inject.py --keycode 61` 可注入模拟说话验证链路）；非语音剪贴板复制会被特征判定过滤。

**Q: 分析总显示「分析不可用」？**
A: LLM 调用失败或超时（检查 API Key/网络），已降级为词典校正原文，会议不中断。

**Q: 面板「积压丢弃 N 段」是什么？**
A: 说话快于分析（LLM 3-8s）时自动只处理最新段，保证实时性；丢弃段不进文档。

**Q: 不开会能当普通语音输入用吗？**
A: 可以——它会逐段分析任意 vocotype 语音（不要求会议场景），但专门的输入增强请用 `asr-corrector`。
