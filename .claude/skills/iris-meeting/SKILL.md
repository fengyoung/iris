---
name: iris-meeting
version: 1.0.0
description: Iris 会议纪要 — 音频转写 + LLM 纪要生成 + 智能路由归档。当用户需要转录会议录音、生成会议纪要、批量处理会议文件时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py transcribe-meeting --help"
---

# Iris 会议纪要

将会议录音转写为文字，生成结构化纪要，并自动归档到正确的 SOURCE 子目录。

## 核心工作流

### 单次会议处理

```bash
python3 scripts/run_cli.py transcribe-meeting \
  --audio-file <音频文件路径> \
  --whisper-model base \
  --to-source
```

**三步骤**：
1. `[1/3]` Whisper 语音转写
2. `[2/3]` 加载 Wiki 上下文（用于 ASR 校正）
3. `[3/3]` LLM 生成结构化纪要 + 路由归档

### 已有转写文本的处理

```bash
python3 scripts/run_cli.py transcribe-meeting \
  --transcript-file <转写文本路径> \
  --to-source
```

跳过语音转写步骤，直接生成纪要。

### 批量处理

```bash
# 指定多个文件
python3 scripts/run_cli.py batch-transcribe --files "file1.m4a,file2.m4a"

# 扫描整个目录
python3 scripts/run_cli.py batch-transcribe --dir <目录路径>
```

## Claude 的工作流程

### 场景 A：用户有音频文件

**步骤 1：确认文件**

帮助用户找到音频文件：
- 如果用户只给了文件名 → 在 `IRIS_MEETING_TRANS_DIR` 环境变量指定的目录中搜索
- 如果是相对路径 → 解析为绝对路径
- 如果用户不确定 → 用 `find` 搜索最近的 `.m4a`/`.mp3`/`.wav` 文件

**步骤 2：确认参数**

询问或推断：
- Whisper 模型选择：默认 `base`（平衡速度和准确度），用户可选 `tiny`（更快）/ `small`（更准）/ `medium`（更准）
- 是否归档到 SOURCE（`--to-source`）：默认推荐开启
- 是否强制重新转写（`--force`）：如果已有转写缓存

**步骤 3：执行**

```bash
python3 scripts/run_cli.py transcribe-meeting \
  --audio-file "<绝对路径>" \
  --whisper-model base \
  --to-source
```

**步骤 4：展示结果**

在对话中展示：
- 📝 会议类型、主题
- 📂 归档位置
- 🔑 关键决策和待办事项摘要

### 场景 B：用户直接给了转写文本

**步骤 1：确认文件**

帮助定位转写文本文件。

**步骤 2：执行**

```bash
python3 scripts/run_cli.py transcribe-meeting \
  --transcript-file "<路径>" \
  --to-source
```

### 会议文件命名规范

音频文件和转写文本应遵循 `YYYYMMDD-{type}-{topic}` 格式：
- `20240315-周会-项目同步.m4a`
- `20240315-讨论-技术方案评审.txt`

### 路由目标

纪要会根据参会人数和内容自动路由：

| 路由目标 | 判定条件 |
|---------|---------|
| `05-会议纪要/` | 多人（≥3）正式会议，有决策/待办 |
| `04-讨论思考/` | 1对1/双人讨论 |
| `03-方案报告/` | 产出正式方案或技术结论 |
| `08-参考资料/` | 外部学习资料、参会笔记 |

## 前置检查

在执行前，Claude 应检查：

1. **Whisper 是否安装**：`python3 -c "import whisper"` （可选依赖）
2. **热词表是否过期**：检查 `output/` 下的 `asr-hotwords-*.txt` 是否需要更新
3. **音频文件是否存在**：先验证路径

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| Whisper 未安装 | 提示安装：`pip install openai-whisper` |
| 音频格式不支持 | 建议用 ffmpeg 转换：`ffmpeg -i input.mp3 output.wav` |
| LLM 纪要生成失败 | 检查 LLM 配置，尝试降级模型 |
| 路由分类不确定 | 展示内容摘要，让用户手动选择目标目录 |
