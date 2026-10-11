# VocoType 配置目录说明

> ⚠️ **部分内容已过时（2026-10-10 标注）**
>
> 本文写于 2026-06-29，描述的是**旧架构**（Iris 生成 → 手动拷贝到 VocoType）。
> 自 `docs/asr-corrector-design.md` 起改为「VocoType 只负责 ASR + 热词，替换与
> LLM 校正交给 Iris 的 asr-corrector」，因此：
>
> - 文末的**手动部署脚本已作废**：现由 `iris build-asr-prompt --deploy` 一键完成。
> - 脚本里「把 `replace_map` 合并进 VocoType `postprocess.json`」的做法与现架构
>   **相反**——`--deploy` 会主动**清空**该字段，避免两套替换逻辑打架。
> - 热词上限以 500 为准（本文写的 490 是当时的保守值，实际硬上限是 500）。
>
> 仍然有效的是：配置目录结构、`hotwords.txt` / `postprocess.json` /
> `ai_settings.json` 三者的格式与对应关系。

## 路径

```
~/Library/Application Support/VocoType/
```

即 `/Users/fengyoung/Library/Application Support/VocoType/`

## 目录结构

```
VocoType/
├── hotwords.txt          # 热词表（ASR boosting）
├── postprocess.json      # 后处理配置（替换词典 + Prompt）
├── ai_settings.json      # AI 模型与 Prompt 模板
├── ai_providers.json     # AI 服务商配置
├── ui_settings.json      # 界面设置
├── signed_license.json   # 许可证
├── user_token.json       # 用户 Token
├── user_cache.json       # 用户缓存
├── usage_stats.json      # 使用统计
├── vocotype.db           # 主数据库
└── dataset/              # 数据集目录
```

## 与 Iris 的对应关系

### 1. hotwords.txt ← `asr-hotwords-{date}-{time}.txt`

- **格式**：纯文本，每行一个热词
- **用途**：ASR 语音识别的热词增强（boosting），提升专有名词识别准确率
- **限制**：≤500 条，每条 ≤20 字符或 ≤10 中文字（见文首标注；`--max-hotwords` 默认 490 是留边距，硬上限为 500）
- **更新方式**：将 Iris 生成的 `asr-hotwords-*.txt` 复制到此路径覆盖

### 2. postprocess.json ← `asr-replace-dict-{date}-{time}.json`

- **格式**：JSON，`{"replace_map": {"误识别词": "正确词", ...}}`
- **用途**：后处理阶段的确定性词→词替换映射
- **限制**：≤990 条，错误词和正确词均 ≤20 字符或 ≤10 中文字
- **更新方式**：将 Iris 生成的 `asr-replace-dict-*.json` 中的 `replace_map` 合并到此文件

### 3. ai_settings.json — Prompt 模板

- **格式**：JSON，包含 `global` 和 `prompts` 两个顶层键
- `global`：当前使用的 Provider、Model、Prompt ID 等
- `prompts`：Prompt 模板列表，每条包含 `id`、`name`、`content`
- **默认 Prompt**（"纠错模式"）：语音转录修正官，负责修正错别字、同音字、口语修正指令
- **与 Iris 的关系**：`asr-prompt-v{ver}-{date}-{time}.md` 可作为新 Prompt 模板添加到 `prompts` 数组中

## Iris 一键部署脚本（示例）

```bash
# 将 Iris 产出部署到 VocoType
IRIS_OUT=./output
VOCO_DIR="$HOME/Library/Application Support/VocoType"
DATE=$(date +%Y%m%d)

# 1. 部署热词表（找最新的 hotwords 文件）
LATEST_HW=$(ls -t "$IRIS_OUT"/asr-hotwords-*.txt 2>/dev/null | head -1)
if [ -n "$LATEST_HW" ]; then
    cp "$LATEST_HW" "$VOCO_DIR/hotwords.txt"
    echo "✅ 热词表已部署: $(basename $LATEST_HW)"
fi

# 2. 部署替换词典（找最新的 replace-dict 文件）
LATEST_RD=$(ls -t "$IRIS_OUT"/asr-replace-dict-*.json 2>/dev/null | head -1)
if [ -n "$LATEST_RD" ]; then
    # 合并到 postprocess.json 的 replace_map
    python3 -c "
import json
new = json.load(open('$LATEST_RD'))
old = json.load(open('$VOCO_DIR/postprocess.json'))
old['replace_map'].update(new['replace_map'])
json.dump(old, open('$VOCO_DIR/postprocess.json', 'w'), ensure_ascii=False, indent=2)
"
    echo "✅ 替换词典已部署: $(basename $LATEST_RD)"
fi

echo "✅ VocoType 配置更新完成"
```
