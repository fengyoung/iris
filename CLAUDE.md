# Iris 3.12.0 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 项目概览

### 当前规模

| 维度 | 数据 |
|------|------|
| 源代码 | ~17,300 行，94 个文件，19 个模块 |
| CLI 命令 | 45 个 |
| 单元测试 | 169 个（13 个测试文件） |
| Claude Code Skill | 6 个项目级 Skill |
| 数据源 | 594 个文档，3,402 个 Chunk |
| 向量索引 | 5,810 条，25 MB |
| Wiki 页面 | 91 页（领域 6 / 概念 7 / 项目 8 / 人物 70） |

### 关键路径

```
Obsidian 仓库：.../WORKSPACE/
                └── WIKI-ROOT/
                    ├── SOURCE/          ← 数据源（8 类分层）
                    │   ├── 01-目标管理/  02-部门管理/
                    │   ├── 03-方案报告/  04-讨论思考/
                    │   ├── 05-会议纪要/  06-我的周报/
                    │   ├── 07-成员周报/  08-参考资料/
                    │   └── v2-data/ → ../../v2-data (软链)
                    └── LLM-WIKI/        ← Wiki 输出（4 种页面类型）
                        ├── 01-领域/  02-概念/  03-项目/  04-人物/
                        ├── index.md  changelog.md
```

路径通过 `.env` 中的 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置。

---

## 核心架构

### Wiki 体系

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化交叉链接 Markdown Wiki。

| 类型 | 目录 | 前缀 | 当前数量 |
|------|------|------|:---:|
| 领域 (domain) | `01-领域/` | `领域-` | 6 |
| 概念 (concept) | `02-概念/` | `概念-` | 7 |
| 项目 (project) | `03-项目/` | `项目-` | 8 |
| 人物 (person) | `04-人物/` | `人物-` | 70 |

Wiki 命令一览：

| 命令 | 说明 |
|------|------|
| `discover-wiki` | 从 Chunk 中发现 Wiki 候选（4 种类型，分层排序） |
| `build-wiki` | 生成 Wiki 页面（单页/批量/审核模式） |
| `build-wiki-nav` | 维护 index.md 总索引 |
| `wiki-pipeline` | 发现→审核→生成全流程 |
| `wiki-lint` / `wiki-lint --fix` | 6 维健康检查 / 自动修复 |
| `wiki-update` | 增量更新（daily-start 自动集成） |
| `enrich-persons` | 飞书通讯录自动补充人物 Wiki 部门/邮箱 |
| `deep-eval` | Wiki 深度评估（引用准确性 + 全面性） |
| `build-asr-prompt` | 三段 LLM Pipeline：热词 → 误识别 → 策略 Prompt |

### 飞书集成

| 管道 | 说明 |
|------|------|
| `feishu-doc-convert` | 飞书文档 → 本地 Markdown + 路由归档 + 排重 |
| `chat-digest` | 聊天记录 AI 提炼为结构化文档 |

### 会议纪要路由

`transcribe-meeting --to-source` 自动判定归档目录：

| 路由目标 | 判定条件 |
|---------|---------|
| `05-会议纪要/` | 多人（≥3）正式会议 |
| `04-讨论思考/` | 1对1/双人讨论（首要信号） |
| `03-方案报告/` | 产出正式方案或技术结论 |
| `08-参考资料/` | 外部学习资料 |

路由规则存于 `config/meeting_routes.json`（gitignored），代码零硬编码。

### 记忆系统（5 子模块）

| 模块 | 职责 |
|------|------|
| `lifecycle.py` | 自治维护（老化/冲突检测/合并） |
| `long_term.py` | 用户画像 + 概念纠正 |
| `session.py` | 会话记忆 |
| `working.py` | 工作上下文（Markdown 文件） |
| `manager.py` | 统一编排（浏览/删除/导入/导出） |

### 复杂输入三阶段流水线

```
Stage 1 (base model)  → 动态生成多模态分析指令
Stage 2 (adv model)   → 图片/PDF 多模态理解
Stage 3 (base model)  → 整合润色输出
```

---

## 配置体系

```
优先级：OS 环境变量 > .env 文件 > macOS Keychain
```

| 层 | 文件 | 版本控制 |
|----|------|---------|
| ① | `.env` | gitignored |
| ② | `config/*.json` | gitignored |
| ③ | `config/*.json.example` | 版本控制 |
| ④ | `data/` | 全 gitignore |

### 关键环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证 |

---

## 版本体系（三层解耦）

| 层 | 位置 | 当前值 | 含义 |
|------|------|:---:|------|
| **产品版本** | `pyproject.toml` | 3.12.0 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.8 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | 3.2/3.4 | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

- Python 3.9+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen）
- Pydantic v2（配置类型安全校验）
- lark-cli（飞书接口层）
- macOS Keychain（可选密钥存储）
- PyMuPDF / python-docx（文档处理）

---

## 项目结构

```
iris3/
├── pyproject.toml              # 产品版本 3.12.0
├── README.md
├── CLAUDE.md                   # 本文件
├── CHANGELOG.md                # 完整版本历史
├── .env.example                # 环境变量模板
├── config/                     # 配置（*.json gitignored, *.example 版本控制）
├── data/                       # 运行时数据（全 gitignore）
├── src/iris/
│   ├── config/                 # 配置加载 + Pydantic v2 校验
│   ├── llm/                    # LLM Provider + 路由 + LLMService
│   ├── core/                   # 核心抽象（类型/锁/写保护/存储/Agent适配器）
│   ├── memory/                 # 记忆系统（5 子模块）
│   ├── ingest/                 # 数据源扫描/切块
│   ├── retrieval/              # 混合检索（BM25 + 向量 + RRF）
│   ├── qa/                     # 检索问答
│   ├── wiki/                   # Wiki 体系（16 文件，最大模块）
│   ├── analysis/               # 分析报告/思维导图
│   ├── evaluation/             # Wiki 深度评估
│   ├── complex_input/          # 多模态输入检测 + 三阶段流水线
│   ├── output/                 # 输出格式化 + Markdown→DOCX 转换
│   ├── app/cli/                # CLI 框架（45 命令）
│   ├── app/transcribe_meeting/ # 会议转录
│   ├── feishu/                 # 飞书文档/聊天提炼
│   └── trello/                 # Trello 看板
├── scripts/                    # CLI 入口 + 委托脚本
├── templates/                  # Prompt / Wiki 模板
├── tests/                      # 单元测试（169 用例）
├── .claude/skills/             # 项目级 Claude Code Skill（6 个）
└── memory/                     # Claude 工作记忆
```

---

## Claude Code Skill

| Skill | 功能 |
|------|------|
| `iris-wiki` | Wiki 发现→审核→生成 |
| `iris-feishu-import` | 飞书文档/聊天交互式导入 |
| `iris-meeting` | 音频转写→纪要→归档 |
| `iris-ask` | 知识库问答 + 多模态理解 |
| `iris-report` | 分析报告/思维导图/双周报 |
| `iris-health` | 知识库质量巡检 |

---

## 近期变更

### v3.11.1 (2026-07-03)
transcribe-meeting 三项修复：日期从文件名提取、时长从时间戳计算、尾注自动追加。

### v3.11.0 (2026-07-03)
新增 6 个项目级 Claude Code Skill，知识库操作从 CLI 扩展到对话式交互。

> 更早版本变更详见 [CHANGELOG.md](CHANGELOG.md)。
