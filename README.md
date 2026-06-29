# Iris 3.6

工作知识助手 — 个人知识库（Obsidian Wiki）与飞书知识库集成。

## 版本

**v3.6.0** — 全面代码审查修复 + 5 项架构重构，138 测试。

## 开发路线

| 步骤 | 内容 | 状态 |
|------|------|------|
| **步骤 1** | 最小化迁移：非知识库能力迁移（搭骨架） | ✅ 完成 |
| **步骤 2** | 本地知识库重构（新 Wiki 体系 + 能力重构） | ✅ 完成 |
| **步骤 3** | 飞书 → 本地知识库提炼 | ✅ 完成 |

## 快速开始

```bash
# 安装
pip install -e .

# 配置
cp .env.example .env
cp config/app.json.example config/app.json
cp config/llm.json.example config/llm.json
cp config/data_source.json.example config/data_source.json
# 编辑上述文件，填入 API Key 和路径
# llm.json 已预配置 DeepSeek (文本) + 百炼 Qwen (多模态) 双 Provider

# 初始化知识库
python scripts/run_cli.py scan-source
python scripts/run_cli.py build-chunks
python scripts/run_cli.py build-vector-index   # 需配置 embedding

# 日常维护
python scripts/run_cli.py daily-start
```

## 核心能力

| 类别 | 命令 | 说明 |
|------|------|------|
| 数据管道 | `scan-source`, `build-chunks`, `build-vector-index` | 文档扫描 / 切块 / 向量索引 |
| 检索问答 | `search`, `ask` | 混合检索（BM25 全文 + 向量）+ LLM 问答 |
| Wiki | `discover-wiki`, `build-wiki`, `wiki-update` | 发现 / 生成 / 增量更新 |
| 健康检查 | `wiki-lint`, `wiki-lint --fix` | 6 维质量检查 + 自动修复 |
| 报告 | `build-report`, `build-mindmap`, `build-biweekly-report` | 专题报告 / 思维导图 / 双周报 |
| 会议 | `transcribe-meeting`, `batch-transcribe`, `build-asr-prompt` | 转录纪要 / 批量处理 / ASR 三段校正 |
| 飞书 | `feishu-doc-convert`, `chat-digest` | 文档转换 / 聊天记录提炼 |
| 记忆 | `memory-*`, `working-set`, `sync-memory` | 记忆管理 / 工作上下文 |
| 工具 | `process`, `trello`, `extract-weekly-reports` | 图文处理 / 看板 / 周报提取 |
| 系统 | `daily-start`, `check-config`, `status`, `diagnose` | 日常维护 / 配置检查 |

## 知识库结构

```
SOURCE/                     LLM-WIKI/
├── 01-目标管理/             ├── 01-领域/    (领域知识地图)
├── 02-部门管理/             ├── 02-概念/    (核心概念术语)
├── 03-方案报告/             ├── 03-项目/    (项目知识沉淀)
├── 04-讨论思考/             ├── 04-人物/    (团队成员画像)
├── 05-会议纪要/             ├── index.md   (总索引)
├── 06-我的周报/             └── changelog.md
├── 07-成员周报/
└── 08-参考资料/
```

## 模型配置

| 角色 | 默认模型 | 提供商 | 能力 | 降级链 |
|------|---------|--------|------|--------|
| `base_model` | deepseek-v4-flash | DeepSeek | 纯文本 | → deepseek-v4-pro |
| `adv_model` | qwen3.6-plus | 百炼 | 文本 + 图片 🌐 | → qwen3.7-plus → qwen3.5-plus |

路由规则（7 条）：用户显式指定 → 多模态输入 → 复杂分析 → Wiki 重建 → 问答 → 文本兜底。

## 技术栈

- Python 3.9+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 Qwen 3.5/3.6/3.7 Plus 多模态）
- lark-cli（飞书接口，步骤 3）
- macOS Keychain（可选密钥存储）
- 138 个单元测试（10 个测试文件）

## 版本历史

| 版本 | 日期 | 要点 |
|------|------|------|
| **v3.6.0** | 2026-06-29 | 全面审查：6 Critical + 14 High 修复，5 项架构重构，138 测试 |
| v3.5.0 | 2026-06-29 | build-asr-prompt 三段 LLM Pipeline（热词 + 误识别 + Prompt） |
| v3.4.0 | 2026-06-27 | 代码审查修复（6 Critical Bug）+ BM25 重写 + 性能优化 |
| v3.3.0 | 2026-06 | 飞书 → 本地知识库提炼，步骤 3 完成 |
| v3.2.1 | 2026-06 | 会议纪要 LLM 动态路由 + 来源标识 |
| v3.2.0 | 2026-06 | 步骤 2 完成，Wiki 体系上线 |
| v3.1.0 | 2026-05 | 人物页面类型 + 协作网络 |
| v3.0.0 | 2026-05 | 项目初始化（从 Iris v2.7.1 重构） |
