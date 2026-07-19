# ASR 实时校正引擎 — 使用指南

> Iris 3.19.5 · 将 vocotype 语音转写接入 Iris，实现实时纠错 + 润色 + 反馈闭环。

## 快速开始

```bash
# 1. 生成配置文件并一键部署
iris3 build-asr-prompt --deploy

# 2. 在 vocotype 设置中关闭 AI 优化、清空替换词典
# 3. 启动校正引擎
iris3 asr-corrector --correct-mode full

# 4. 正常使用 vocotype 语音输入，Iris 后台自动校正
```

## 命令一览

| 命令 | 用途 | 常用参数 |
|------|------|------|
| `iris3 build-asr-prompt --deploy` | 生成三件套 + 部署到 vocotype | `--deploy` 一键部署 |
| `iris3 asr-corrector` | 启动实时校正守护进程 | `--correct-mode fast\|full` |
| `iris3 asr-audit --pretty` | 热词覆盖率 + 词典质量检查 | `--pretty` 人类可读 |
| `iris3 asr-report --notes "..."` | 手动纠错（剪贴板原文 + 用户提供正确文本） | |

## 前置条件

- 安装 [VocoType](https://vocotype.com)（免费桌面版）
- macOS（剪贴板监听依赖 macOS API）
- Iris 项目已配置 LLM（`.env` 中的 API Key）

## 配置

校正策略通过 `config/asr_profiles.json` 配置（首次使用从 `.example` 复制）：

```json
{
  "default": {
    "mode": "full",
    "llm": {
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "timeout_ms": 4000
    }
  }
}
```

## 工作链路

```
用户说话 → vocotype (ASR + 热词) → 剪贴板 → Iris 检测 → 词典校正 → LLM 润色 → 剪贴板 → 光标
```

## 文件说明

| 文件 | 用途 |
|------|------|
| `data/asr_replace_dict.json` | 替换词典（990 条），每次校正的第一步 |
| `data/asr_prompt.md` | LLM 校正 Prompt（~930 字），编辑助手角色 |
| `data/asr_feedback.jsonl` | 校正记录（自动积累），Phase 1 反向优化用 |
| `config/asr_profiles.json` | LLM 使用参数（provider/model/timeout） |

## 常见问题

**Q: 不安装 vocotype 能用吗？**
A: 不能。`iris-asr-corrector` 依赖 vocotype 提供 ASR 能力。Iris 其他功能不受影响。

**Q: 如何判断是否生效？**
A: 启动 `asr-corrector` 后，用 vocotype 说句话，终端会显示 `[Iris] ✅` 或 `[Iris] 🤖` 修正记录。也可以查看 `data/asr_feedback.jsonl`。

**Q: LLM 校正太慢怎么办？**
A: 使用 `--correct-mode fast` 仅启用词典校正，毫秒级。

**Q: 如何贡献纠错数据？**
A: 发现校正结果不对时，运行 `iris3 asr-report --notes "正确的文本"`。
