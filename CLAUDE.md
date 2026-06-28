# Iris 3.4 — 项目执行说明

> 工作知识助手，从 Iris v2.7.1 重构升级而来。
> 个人知识库（Obsidian Wiki）重新设计 + 新增飞书团队知识库操作能力。

---

## 项目概览

### 三个实施步骤

| 步骤 | 内容 | 状态 |
|------|------|------|
| **步骤 1** | 最小化迁移：非知识库能力迁移（搭骨架） | ✅ 完成 |
| **步骤 2** | 本地知识库重构（新 Wiki 体系 + 能力重构） | ✅ 完成 |
| **步骤 3** | 飞书 → 本地知识库提炼 | ✅ 完成 |

### 当前规模

| 维度 | 数据 |
|------|------|
| 源代码 | 13,772 行，83 个文件，18 个模块 |
| CLI 命令 | 43 个 |
| 单元测试 | 97 个 |
| 数据源 | 594 个文档，3,402 个 Chunk |
| 向量索引 | 5,810 条，25 MB |
| Wiki 页面 | 30 页（领域 6 / 概念 7 / 项目 8 / 人物 9） |
| 成员周报 | 453 份（2025-2026） |

### 关键路径

```
Obsidian 仓库：.../WORKSPACE/
                ├── WIKI-ROOT/
                │   ├── SOURCE/          ← 数据源（8 类分层）
                │   │   ├── 01-目标管理/
                │   │   ├── 02-部门管理/
                │   │   ├── 03-方案报告/
                │   │   ├── 04-讨论思考/
                │   │   ├── 05-会议纪要/
                │   │   ├── 06-我的周报/
                │   │   ├── 07-成员周报/
                │   │   ├── 08-参考资料/
                │   │   └── v2-data/ → ../../v2-data (软链)
                │   └── LLM-WIKI/        ← Wiki 输出（4 种页面类型）
                │       ├── 01-领域/
                │       ├── 02-概念/
                │       ├── 03-项目/
                │       ├── 04-人物/
                │       ├── index.md
                │       └── changelog.md
                ├── @项目/
                ├── LLM-Wiki/             ← v2.7.1 旧 Wiki（保留不迁）
                └── v2-data/          ← v2.7.1 旧数据源
```

路径通过 `.env` 中的 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置。

---

## 步骤 1：最小化迁移 ✅

### 已迁移模块

| 模块 | 文件 | 说明 |
|------|------|------|
| 配置加载 | `src/iris/config/loader.py` | .env 加载、${VAR} 解析、.example 回退 |
| 密钥链 | `src/iris/config/secrets.py` | macOS Keychain 存取 API Key |
| LLM Provider | `src/iris/llm/` | 多 Provider 注册、降级链 |
| 模型路由 | `src/iris/llm/router.py` | 路由规则引擎 |
| 日志 | `src/iris/utils/logging.py` | 结构化 JSON 日志 |
| 核心抽象 | `src/iris/core/` | Protocol、文件锁、写入守卫、存储层 |
| CLI 框架 | `src/iris/app/cli/` | argparse 分发表、43 个命令 |
| 记忆系统 | `src/iris/memory/` | 画像、校正、工作上下文、会话 |
| Trello | `src/iris/trello/` | 看板 CRUD + LLM 汇总 |
| 图文处理 | `src/iris/complex_input/` | 双阶段图文处理 |
| 周报提取 | `scripts/extract_weekly_reports.py` | 飞书邮箱周报提取 |
| 滴滴行程 | `scripts/extract_didi_travel.py` | 滴滴行程提取 |
| 入口脚本 | `scripts/run_cli.py` | CLI 入口 |

### 裁剪要点

- WikiSearcher 导入和调用已去除
- Wiki 健康度检查已从 status/diagnose 中去除
- wiki_root 相关配置校验已去除

---

## 步骤 2：本地知识库重构 ✅

### Wiki 体系设计（基于 Karpathy LLM Wiki 模式）

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化的交叉链接 Markdown Wiki。

参考：https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

### 页面类型（4 种）

| 类型 | 目录 | 前缀 | 说明 |
|------|------|------|------|
| 领域 (domain) | `01-领域/` | `领域-` | 工作领域知识地图 |
| 概念 (concept) | `02-概念/` | `概念-` | 核心概念、术语、方法 |
| 项目 (project) | `03-项目/` | `项目-` | 具体项目知识沉淀 |
| 人物 (person) | `04-人物/` | `人物-` | 团队成员与组织知识（🆕 v3.1） |

### 页面模板

```markdown
---
title: 页面标题
type: domain|concept|project|person
status: review          # draft | stable | review
sync: false             # 是否允许推送到飞书
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - SOURCE/path/to/source.md
---

## 摘要
1-2 句概述。

## 正文
...

## 关联页面
[[领域-相关领域]]
[[概念-相关概念]]

## 参考来源
- SOURCE/path/to/source1.md
```

### Wiki 操作命令

| 命令 | 说明 |
|------|------|
| `discover-wiki` | 从 Chunk 中发现 Wiki 候选（4 种类型，分层排序） |
| `build-wiki` | 生成 Wiki 页面（单页/批量/审核模式） |
| `build-wiki-nav` | 维护 index.md 总索引 |
| `wiki-pipeline` | 发现→审核→生成全流程 |
| `wiki-lint` | 6 维健康检查（断链/孤立/草稿/过期/摘要/出链） |
| `wiki-lint --fix` | 自动修复 frontmatter、噪音链接、draft 状态 |
| `wiki-update` | 🆕 增量更新（daily-start 自动集成） |
| `build-asr-prompt` | 🆕 LLM 驱动：术语提取 → 批量误识别生成 → ASR 校正提示词（支持 --bump / --output-format） |

### 人物提取（🆕 v3.1）

- **数据源**：周报文件、会议纪要参会人、文档中的负责人标记
- **协作网络**：按同场会议频次统计
- **更新机制**：person 类型专用增量更新 prompt，追踪方向变化

### 索引质量

- 断链智能过滤：技术术语白名单（100+）+ 源文档引用 + 字符级模糊匹配，排除率 83%
- `wiki-lint` 6 维指标：frontmatter / 摘要 / 出链 / draft 状态 / 过期 / 断链

### 会议纪要路由（v3.2.1）

`transcribe-meeting --to-source` 现在支持 LLM 动态路由，自动判定纪要归档到 SOURCE 对应子目录：

| 路由目标 | 判定条件 | 命名格式 |
|---------|---------|---------|
| `05-会议纪要/` | 多人（≥3）正式会议，有决策/待办 | `YYYYMMDD-{type}-{topic}.md` |
| `04-讨论思考/` | 1对1/双人讨论（首要信号），即使有决策待办 | `YYYYMMDD-{type}-{topic}.md` |
| `03-方案报告/` | 产出正式方案或技术结论 | `YYYYMMDD-{topic}.md` |
| `08-参考资料/` | 外部学习资料、参会笔记 | `YYYYMMDD-{source}-{topic}.md` |

**配置驱动（开源安全）：** 路由规则存于 `config/meeting_routes.json`（gitignored），
代码零硬编码。`.example` 为脱敏占位。

**来源标识：** 纪要头部写入 `来源：原始文件名`，供后续排重和溯源。

---

## 步骤 3：飞书 → 本地知识库提炼 ✅

飞书文档/聊天记录 → 本地 Markdown → SOURCE 归档 → Wiki 自然吸收。

| 管道 | 状态 | 说明 |
|------|------|------|
| `feishu-doc-convert` | ✅ 完成 | 飞书文档转本地 Markdown（图片→Pic，补充元信息+作者，排重） |
| `chat-digest` | ✅ 完成 | 聊天记录 AI 提炼为结构化文档（主题/决策/待办/关联项目） |

**不包含**：本地 → 飞书发布（需求已取消）、团队知识查询（直接用飞书搜）。

### 使用方式

```bash
# 飞书文档转换
iris feishu-doc-convert --url <文档URL>              # 单篇文档
iris feishu-doc-convert --url <URL1> --url <URL2>     # 批量
iris feishu-doc-convert --from-config                 # 从配置文件读取
iris feishu-doc-convert --url <URL> --dry-run         # 预览路由结果

# 聊天记录提炼
iris chat-digest --group <群聊名> --range 5           # 指定群聊+天数
iris chat-digest --user <用户名> --range 3            # 指定用户单聊
iris chat-digest --interactive                        # 交互选择模式
iris chat-digest --from-config                        # 从配置文件读取
```

### 关键设计

- **路由驱动**：文档用 `meeting_routes.json` 配置规则，聊天用 AI 萃取后的决策/待办判定
- **排重索引**：`data/dedup/feishu_doc_index.json` 和 `chat_digest_index.json`
- **图片存储**：`Pic/{stem}/feishu_xxx.png`（与 Obsidian 兼容）
- **开源安全**：全部关键词和描述均从 gitignored 配置文件动态读取

详细设计见 [[feishu-to-local-pipelines]]。

---

## 配置安全方案

四层配置体系，适合开源：

```
优先级：OS 环境变量 > .env 文件 > macOS Keychain
```

| 层 | 文件 | 版本控制 |
|----|------|---------|
| ① | `.env` | gitignored |
| ② | `config/*.json` | gitignored |
| ③ | `config/*.json.example` | 版本控制 |
| ④ | `data/` | 全 gitignore |

JSON 配置中使用 `${VAR_NAME}` 引用环境变量或 .env 中的值。

### 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件默认搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证（步骤 3） |

---

## 版本体系（三层解耦）

| 层 | 位置 | 格式 | 含义 | 当前值 |
|----|------|------|------|--------|
| **产品版本** | `pyproject.toml` | SemVer X.Y.Z | 软件发布版本 | 3.4.0 |
| **协议版本** | `src/iris/__init__.py` | MAJOR.MINOR | CLI 命令集 / agent-spec 格式 | 3.4 |
| **数据版本** | `config/*.json` | 各自独立 | 配置文件 Schema | 3.3 |

> 三层解耦：只有真正发生变化的层才递增版本号。

---

## 技术栈

- Python 3.9+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen 等）
- lark-cli（飞书接口层）
- macOS Keychain（可选密钥存储）

## 项目结构

```
iris3/
├── pyproject.toml          # 3.4.0，依赖：PyMuPDF / python-docx / numpy
├── README.md
├── CLAUDE.md               # 本文件
├── .env.example            # 环境变量模板
├── config/                 # 配置（*.json gitignored, *.example 版本控制）
├── data/                   # 运行时数据（全 gitignore）
├── src/iris/
│   ├── config/             # 步骤 1 — 配置加载
│   ├── llm/                # 步骤 1 — LLM Provider + 路由
│   ├── memory/             # 步骤 1 — 记忆系统
│   ├── core/               # 步骤 1 — 核心抽象（Protocol、锁、写保护、存储）
│   ├── app/cli/            # 步骤 1 — CLI 框架（41 命令）
│   ├── app/transcribe_meeting/ # 步骤 2 — 会议转录
│   ├── trello/             # 步骤 1 — Trello 集成
│   ├── complex_input/      # 步骤 1 — 图文处理
│   ├── utils/              # 步骤 1 — 工具（🆕 v3.4: validation 输入校验）
│   ├── ingest/             # 步骤 2 — 数据源扫描/切块
│   ├── retrieval/          # 步骤 2 — 混合检索
│   ├── qa/                 # 步骤 2 — 问答
│   ├── wiki/               # 步骤 2 — Wiki 体系（🆕 v3.4: context_loader, _constants; 🆕 v3.4.1: term_extractor）
│   ├── analysis/           # 步骤 2 — 报告/思维导图/双周报
│   ├── output/             # 步骤 1 — 输出格式化
│   └── feishu/             # 步骤 3 — 飞书文档/聊天提炼
│       ├── client.py       #   lark-cli 封装（文档/IM/图片/通讯录）
│       ├── _shared.py      #   共享工具（路径/排重/时间/标题清理）
│       ├── doc_convert.py  #   飞书文档→本地 Markdown + 路由归档
│       └── chat_digest.py  #   聊天记录 AI 提炼 + 结构化输出
├── scripts/                # CLI 入口 + 委托脚本
├── templates/              # Prompt / Wiki 模板
├── tests/                  # 单元测试（97 用例，包含 term_extractor 38 用例）
└── memory/                 # Claude 工作记忆
```

---

## 🆕 v3.4 变更（2026-06-27）

### 新增模块
| 模块 | 文件 | 说明 |
|------|------|------|
| Wiki 常量 | `src/iris/wiki/_constants.py` | 页面类型单一数据源，消除 4 文件映射重复 |
| Wiki 上下文加载 | `src/iris/wiki/context_loader.py` | 统一 Wiki 页面加载，替换 4 处独立实现 |
| 输入校验 | `src/iris/utils/validation.py` | 安全类型转换、JSON 解析、必填字段校验 |

### Critical Bug 修复（6 项）
- XMind 导出：`zipfile.ZipIO()` → `io.BytesIO()`（原代码完全无法运行）
- BM25 检索算法重写：正确的 IDF 公式 + 语料库级统计量
- 向量索引矩阵缓存：避免每次 `search()` 重建 numpy 矩阵
- `query_plan` 参数接入：之前接收后从未使用
- Wiki 覆盖度统计字段修正：`document_path` → `relative_path`
- `_build_person_prompt` return 后死代码删除

### 第二轮审查修复（2 Critical + 3 High）
- `ModelManagerError` 未导入 → 异常路径 `NameError` 崩溃
- Trello DNS 全局 monkey-patch 线程安全：Lock + TTL
- 飞书客户端孤儿子进程清理：`subprocess.Popen` + `finally kill`
- frontmatter 损坏永久卡死修复 + `\r\n` 换行符兼容

### 检索质量改进
- BM25 评分：IDF 从 `log(doc_len/tf)` 修正为 `log((N-df)/df)`
- 向量索引：矩阵缓存避免每次搜索 O(n) 重建
- `query_plan` 权重调整：高优先级 focus_areas 提升标题/段落权重

### 健壮性增强
- `write_guard` 统一：用户自定义路径与内部关键目录合并处理
- `model_manager` 浅拷贝：防止就地修改共享配置字典
- 排重过滤器安全修复：`source_url` 为空时不再误删条目
- 飞书客户端进程生命周期管理：`Popen.kill()` + `communicate()`
- FTS5 初始化失败记录 warning 日志
- PDF 标题提取 `fitz` 缺失回退保护
- JSONL 输入解析错误提示（含行号）
- `\r\n` 换行符统一处理（frontmatter / YAML 解析兼容 Windows）
- 日志 10MB 自动归档 + set/frozenset 序列化

### 性能优化
- `update_all_pages` O(n²) → O(n)：预建 title→path 索引
- PDF 提取 `import re` 模块级迁移（热路径优化）
- Whisper MPS 加速检测（Apple Silicon）
- 提示词模板内存缓存
- DNS 缓存 TTL 机制（Trello）

### Wiki 常量统一
- 页面类型配置（目录/前缀/显示名）从 4 处硬编码收敛到 `_constants.py` 单一数据源
- `discovery.py` / `generator.py` / `navigation.py` / `searcher.py` 统一引用

---

## 🆕 v3.4.1 变更（2026-06-27 合并）

### build-asr-prompt 重写
`build-asr-prompt` 从 **Wiki 页面拼贴**改造为 **LLM 驱动的术语提炼 + 误识别生成**引擎：

| 维度 | 旧实现 | 新实现 |
|------|--------|--------|
| 核心逻辑 | 简单拼贴 Wiki 页面正文 | `wiki/term_extractor.py`（756 行）全流程 |
| 术语来源 | 无结构提取 | person/concept/project/domain 4 类型结构化提取 |
| 误识别生成 | 无 | base_model 一次批量调用生成常见误识别 |
| 版本管理 | 无 | 三段式（MAJOR.MINOR.PATCH）+ 内容指纹自动检测 |
| 输出格式 | 单一格式 | standard / compact 两种 render 格式 |
| CLI 参数 | 无版本控制 | `--bump auto\|major\|minor\|patch`、`--output-format` |

### 新增文件
| 文件 | 行数 | 说明 |
|------|------|------|
| `src/iris/wiki/term_extractor.py` | 756 | TermExtractor 术语提取器、LLM 批量误识别生成、prompt 渲染、版本管理 |
| `tests/test_term_extractor.py` | 432 | 38 个单元测试覆盖术语提取、LLM prompt 构建、响应解析、版本管理、渲染输出 |

### handler 重构
- `handle_build_asr_prompt` 从 80 行简化为 45 行：调用 TermExtractor 管线化流程
- 新增 `--bump`、`--output-format` CLI 参数
