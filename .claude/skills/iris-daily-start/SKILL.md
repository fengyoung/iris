---
name: iris-daily-start
version: 1.1.0
description: Iris 每日启动维护 — 记忆同步、扫描切块、向量索引、Wiki 自动更新、知识图谱刷新、LLM 用量概要、主动提醒。当用户需要执行日常维护、启动知识库更新、查看 LLM 用量、检查知识库异常信号时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py --call-source skill daily-start --help"
---

# Iris 每日启动维护

一键执行 Iris 知识库的完整日常维护管道，涵盖记忆、索引、Wiki、图谱、用量、提醒六大模块。

> 所有命令均在 iris3 项目根目录执行（cwd = 仓库根目录）。

## 执行命令

```bash
python3 scripts/run_cli.py --call-source skill daily-start
```

如需 JSON 格式输出（便于解析）：

```bash
python3 scripts/run_cli.py --call-source skill daily-start --pretty
```

## 管道流程（9 步 + 情报阶段）

```
1. 记忆同步与维护
   ├── 长期记忆同步（扫描 memory/ 目录）
   └── 自治维护（老化检测 / 冲突检测 / 合并）

2. 扫描 + 切块 + 向量索引
   ├── MarkdownScanner 扫描所有启用数据源
   ├── MarkdownChunker 增量切块（复用未变化的文档，相邻块 150 字重叠）
   └── 向量索引增量更新（仅当 embedding 启用时；模型变更时返回
       model_mismatch，需手动执行 build-vector-index --force-rebuild）

3. Wiki 自动维护
   ├── 自动发现候选主题（基于新增/变更文档数；过时判定优先按
   │   source_fingerprint 源文档指纹，源文档未变不重生成）
   ├── WikiGenerator.update_all_pages() 增量更新已有页面
   ├── PersonEnricher 飞书通讯录补充人物信息
   └── 知识图谱增量刷新（节点 + wikilink 边，零 LLM 成本）

4. Wiki 导航索引
   ├── WikiNavigationBuilder 重建 index.md
   └── 追加 changelog 条目

5. LLM 用量概要
   ├── 今日 / 本周 / 本月调用次数与 token 汇总
   └── 预算预警（超过 monthly_token_limit 时告警）

6. ASR 审计（零 LLM 成本）
   └── 读 output/asr-corrector-bak/ 最新产物，比对当前 Wiki 覆盖情况

7. ASR 资源更新（按需，约 14+ 次 LLM 调用）
   ├── 指纹增量：输入指纹（Wiki 页面 + data/asr_manual_hotwords.txt）与上次
   │   生成一致 → 整体跳过；只改注释/顺序不算变化
   ├── build-asr-prompt --mode all --deploy（重建热词/替换词典/提示词并部署）
   └── 重启 VocoType（热词表只在 App 启动时加载，不重启等于没部署）

8. 主动提醒（零 LLM 成本）
   ├── 栏目断供：SOURCE 分类目录超阈值无更新（默认 30 天，周报/会议类 10-14 天）
   ├── 周报缺失：活跃成员周报断档 ≥14 天
   └── 项目停滞：项目 Wiki 引用的源文档全部 ≥21 天未更新

10. 情报阶段（OKR 证据 + 主动信号 + 飞书日报）
   ├── OKR 证据打标（增量，独立失败不阻断维护）
   ├── 信号检测：KR 风险句 / OKR 证据缺口 / 决策复盘与停滞 /
   │   Wiki 脱节 / 未落文档话题 / 缺失实体 / 栏目断供 / 周报缺失 / 项目停滞
   ├── 按 kind 配额排序（避免单一类型霸榜）+ 跨日新鲜度（新增 / 持续 N 天）
   ├── 生成飞书日报：post 标题 + 分区正文 + 决策待审 + 维护回执
   └── 默认按天幂等发送（同日不重复）；`--dry-run` 只预览归档，
       `--no-notify` 关闭发送
```

### 通知开关与发送策略

| 参数 / 配置 | 作用 |
|------|------|
| `--dry-run` | 仅通知阶段：预览正文并归档，不发送；不更新跨日信号历史（其余维护照常执行） |
| `--no-notify` | 生成并归档日报正文，但不发送飞书 |
| `intelligence.notify_policy` | `daily`（默认，一天一张卡）或 `every_run`（内容变化即发） |
| `intelligence.enabled=false` | 同时关闭 OKR 证据积累与信号阶段 |

## 使用场景

| 场景 | 说明 |
|------|------|
| **每日启动** | 每天开始工作时跑一次，确保知识库最新 |
| **知识库刷新** | 新增了一批数据源文档后，手动触发全量刷新 |
| **用量检查** | 查看今日/本周/本月的 LLM 调用量和 token 消耗 |
| **前置依赖** | 运行 `iris-wiki` 前如果缺 chunk 索引，先跑此命令 |

## 与 iris-wiki 的关系

`daily-start` 是 `iris-wiki` 的**前置依赖**和**互补工具**：

- `daily-start`：全自动管道，更新已有 Wiki、刷新索引和图谱，不生成新页面
- `iris-wiki`：交互式策展，发现候选 → 人工审核 → 生成新 Wiki 页面

**典型工作流**：
1. 新增数据源文档 → 先跑 `daily-start` 刷新索引
2. 再跑 `iris-wiki` 的 `wiki-pipeline` 发现新候选并生成页面

## 输出解读

执行完成后，关注以下关键字段：

- `scan[N].document_count` — 各数据源扫描到的文档数
- `chunks[N].rebuilt_documents` — 有变更需重新切块的文档数（>0 意味着有新内容）
- `chunks[N].cleaned_documents` — 被删除的源文档数（>0 同样触发 Wiki 发现，即使没有任何重建）
- `wiki_discover.candidates` — 自动发现的 Wiki 候选数（有候选时建议后续跑 iris-wiki）
- `wiki_update.status` — Wiki 增量更新是否成功
- `usage_summary.budget_warning` — 如果出现，说明本月 token 已超预算
- `vector_index.status` — 为 `model_mismatch` 时表示 embedding 模型已变更，
  需手动执行 `build-vector-index --force-rebuild` 全量重建
- `reminders.signals` — 主动提醒信号列表（栏目断供/周报缺失/项目停滞），
  有信号时应向用户逐条汇报（这些信号现在也会并入飞书日报）
- `intelligence.signals.counts` — 本次日报的信号数 / 待审决策数 / AI 已通过数 / 孤儿候选数
- `intelligence.signals.delivery.status` — 飞书发送结果：
  - `sent` — 已发送；`duplicate` — 当日已发送，未重复
  - `uncertain` — 发送结果不确定（网络超时等），不会盲目重发，需人工核验
  - `not_configured` — 未配置 `intelligence.feishu_user_id`
  - `dry_run` — `--dry-run` 预览；`disabled` — `--no-notify` 关闭
- `intelligence.signals.errors` — 独立检测器失败原因（Wiki 鲜度 / OKR / 提醒采集）
- `asr_audit.status` — ASR 热词覆盖审计；`needs_update` 为 true 时 `issues`
  列出覆盖缺口，`warnings` 列出待手工清理的噪音/超长词（两者处置方式不同）
- `asr_refresh.status` — ASR 资源更新结果，四种取值：
  - `skipped` — 输入指纹未变（Wiki 页面与手动热词都没动），未重建（**这是常态，不是异常**）
  - `ok` — 已重建、已部署、VocoType 已重启（或未在运行，下次启动生效）
  - `deploy_only` — 已部署但 **VocoType 未重启，新热词表尚未生效**；
    必须向用户显式报告 `warning`，并给出备份路径
  - `error` — 生成/部署失败，`reason` 说明原因；不影响其余维护结果

## 关键路径

- 项目根目录：`scripts/run_cli.py` 所在目录
- 数据源目录：由 `.env` 中 `${IRIS_WORK_DOCS_DIR}` 配置
- Wiki 输出目录：由 `.env` 中 `${IRIS_WIKI_ROOT}` 配置
- 用量数据库：`data/usage.db`

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 找不到 .env 配置 | 确认当前在 iris3 项目根目录，检查 `.env` 文件存在 |
| 扫描无结果 | 检查 `${IRIS_WORK_DOCS_DIR}` 路径是否正确，数据源目录是否有文件 |
| embedding 跳过 | 正常行为 — 如未配置 embedding 提供商会自动跳过向量索引 |
| Wiki update 跳过 | 无 chunk 数据或 wiki_root 未配置时会跳过，不影响其他步骤 |
| 人物丰富失败 | 静默失败，不影响主流程 — 可能是飞书 token 过期或网络问题 |
| 用量 DB 为空 | 尚未发生任何 LLM 调用，显示 `{"status": "empty"}` |
