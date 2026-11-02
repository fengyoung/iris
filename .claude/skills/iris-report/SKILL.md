---
name: iris-report
version: 1.0.0
description: Iris 报告生成 — 分析报告、思维导图、双周报。当用户需要生成专题分析报告、创建思维导图、写双周报时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py --call-source skill build-report --help"
---

# Iris 报告生成

从知识库中检索相关内容，生成三种类型的报告产出。

> 所有命令均在 iris3 项目根目录执行（cwd = 仓库根目录）。

## 报告类型

### 1. 分析报告 (`build-report`)

```bash
python3 scripts/run_cli.py --call-source skill build-report --query "<主题>" [选项]
```

**选项**：
- `--top-k 10`：检索更多证据（默认 5）
- `--output-format md`：输出 Markdown（默认）
- `--output-format docx`：输出 Word 文档
- `--output-format standard`：标准格式，含摘要+正文+参考
- `--output-format compact`：紧凑格式
- `--output-file <路径>`：保存到指定文件
- `--two-stage`：两阶段生成（先大纲后正文，质量更高但更慢）

### 2. 思维导图 (`build-mindmap`)

```bash
python3 scripts/run_cli.py --call-source skill build-mindmap --query "<主题>" [选项]
```

**选项**：
- `--format mermaid`：输出 Mermaid 代码（可在 Markdown 中渲染）
- `--format xmind`：输出 XMind 文件（`.xmind`）
- `--format both`：两种都输出
- `--output-file <路径>`：保存到指定文件

### 3. 双周报 (`build-biweekly-report`)

```bash
python3 scripts/run_cli.py --call-source skill build-biweekly-report [选项]
```

**选项**：
- `--query "<补充说明>"`：额外的重点关注内容
- `--to-source`：归档到 `SOURCE/06-我的周报/YYYY/`（按年归档）
- `--output-file <路径>`：手动指定输出路径

## Claude 的工作流程

### 分析报告

**步骤 1：精炼查询**

用户的想法往往是模糊的（「帮我分析一下产品方向」），Claude 应该：
1. 追问 1-2 个关键问题明确范围
2. 将模糊需求转化为精准的检索查询

**步骤 2：选择格式**

| 用户说 | 选择 |
|--------|------|
| 「写个报告」 | `--output-format standard --output-file <路径>.md` |
| 「导出 Word」 | `--output-format docx --output-file <路径>.docx` |
| 「快速总结」 | `--output-format compact` |
| 「深度分析」 | `--two-stage --output-format standard` |

**步骤 3：执行并展示**

```bash
python3 scripts/run_cli.py --call-source skill build-report --query "<精炼后的查询>" --output-format standard
```

在对话中展示报告摘要，标注来源和关键发现。

### 思维导图

**步骤 1：确定格式**

- 如果用户想在对话中查看 → `--format mermaid`（Claude 可以渲染）
- 如果用户想导入 XMind → `--format xmind`
- 不确定 → `--format both`

**步骤 2：执行**

```bash
python3 scripts/run_cli.py --call-source skill build-mindmap --query "<查询>" --format both
```

Mermaid 格式的导图可以在对话中直接展示。

### 双周报

双周报自动整合近两周的成员周报、会议纪要、讨论思考，与最新 OP 方向对齐，生成结构化战略周报。

**步骤 1：检查数据源**

在生成前，先用 `--dry-run` 模式预览数据源覆盖情况：

```bash
python3 scripts/run_cli.py --call-source skill build-biweekly-report --dry-run
```

确认：
- 文件数量是否充足（通常应 ≥10 份）
- OP 方向是否正确解析（方向名和 scope_summary 是否合理）
- 时间窗口是否覆盖了最近两周

如果文件偏少（<5 份），提醒用户可能原因（假期、成员未发周报等）。

**步骤 2：执行生成**

```bash
python3 scripts/run_cli.py --call-source skill build-biweekly-report --to-source
```

**选项：**
- `--to-source`：归档到 `SOURCE/06-我的周报/YYYY/`（按年归档）（默认推荐）
- `--output-file <路径>`：手动指定输出路径
- `--query "<补充说明>"`：额外的重点关注内容
- `--style-from <文件名>`：从指定历史双周报学习写作风格（在 `06-我的周报/YYYY/` 中递归查找）
- `--dry-run`：预览模式，仅展示文件清单和方向匹配，不调用 LLM

**步骤 3：展示摘要并提醒修订**

在对话中展示：
1. 报告标题和时间周期
2. 每个方向的关键进展摘要（1-2 条）
3. 数据源文件统计（共 N 份文件）
4. 提醒用户报告已保存到 `SOURCE/06-我的周报/YYYY/`（按年归档），建议人工审阅修订

**步骤 4：发布（飞书文档 + 邮件，均需用户确认后执行）**

⚠️ 周期口径：周期为**自然周（周一～周日）**，由 `align_biweekly_period()` 把运行日回退到最近一个周日。归档文件名日期与周号均取**周期结束日**，不是运行日（如 11-02 周一运行 → 周期 `10.19~11.01`、w44、文件名 `20261101-`）。

**4a. 飞书文档（长期唯一载体）**

- 冯扬唯一发布地：docx token `OMC9dvNcLoJhmkxgLRAclvTZnbh`（「OKR双周报-产研线-冯扬」，一人一年度一个文档不换）。
- 每期在「2026 年重点工作」一节之后插入新 H1（新在前）。**锚点必须是该节末块的 block-id，不能是 H1 block-id**。
- H1 只能是日期区间 `2026.10.19-2026.11.01`（用 `-`，与文档既有格式一致）。
- **文档版裁剪**：不带 frontmatter、不带 `*时间周期：…*` 行、不带文末 `---` 与 Iris footer、**删除所有 `（来源：…）`**。
- 写入后**必须回读校验**：逐段 diff 正文 + 核对 outline H1 总数每期 +1（未知 XML 标签会静默截断）。

```bash
lark-cli docs +fetch --doc OMC9dvNcLoJhmkxgLRAclvTZnbh --scope outline --max-depth 2 --detail with-ids
lark-cli docs +update --doc OMC9dvNcLoJhmkxgLRAclvTZnbh --command block_insert_after --block-id <节末块id> --content "$(cat output/wNN-doc.xml)"
```

**4b. 邮件（按期分发，先建草稿不发送）**

- 主题：`【双周报】-w{NN}-冯扬-{周期结束日}`，日期取周期结束日，不是发送日。
- To：`dataintelligence@zhuanzhuan.com`、`qc-tech@zhuanzhuan.com`
- Cc（6 人）：`lijincheng`、`wutingpeng`、`mayue05`、`linzhenying`、`shiyueqi`、`guoyubin`（均为 `@zhuanzhuan.com`）
- 流程：md → HTML 全文（**无附件**）→ 建草稿 → 用户审阅确认 → 发送 → 查投递状态。
- **邮件版保留来源**（与文档版相反），正文自带 Iris footer + 签名块，建草稿时加 `--no-signature` 防双签名。
- 版式以最近一期已发送邮件的 `body_html` 为准（取 SENT 里上期 `【双周报】` 邮件回读）。
- 发送前校验：源 HTML 与草稿回读 `body_html` 去空白后**全等**，且 md 每段文本无丢失。
- 发送后查 `send_status`，**只有 status=4 才算送达**；1/2/3/5/6 需报告异常。

```bash
lark-cli mail +lint-html --body-file ./output/wNN-mail.html --as user
lark-cli mail +draft-create --as user --to 'dataintelligence@zhuanzhuan.com' --to 'qc-tech@zhuanzhuan.com' \
  --cc 'lijincheng@zhuanzhuan.com' --cc 'wutingpeng@zhuanzhuan.com' --cc 'mayue05@zhuanzhuan.com' \
  --cc 'linzhenying@zhuanzhuan.com' --cc 'shiyueqi@zhuanzhuan.com' --cc 'guoyubin@zhuanzhuan.com' \
  --subject '【双周报】-w44-冯扬-20261101' --body-file ./output/w44-mail.html --no-signature
lark-cli mail user_mailbox.drafts send --params '{"user_mailbox_id":"me","draft_id":"<id>"}' --as user --yes
lark-cli mail user_mailbox.messages send_status --params '{"user_mailbox_id":"me","message_id":"<id>"}' --as user
```

**流水线说明：**

双周报生成采用五阶段流水线：
1. **OP 解析**：从 `01-目标管理/YYYY/`（按年归档）提取战略方向定义（缓存）
2. **文件过滤**：LLM 按方向语义判定文件相关性
3. **深度摘要**：高相关文件全文摘要（缓存）
4. **方向合成**：每方向独立合成章节，含战略分析 + 多期去重
5. **终稿审查**：组装 + 质量审查修订

**常见场景：**

| 场景 | 命令 |
|------|------|
| 标准生成 | `python3 scripts/run_cli.py --call-source skill build-biweekly-report --to-source` |
| 预览检查 | `python3 scripts/run_cli.py --call-source skill build-biweekly-report --dry-run` |
| 指定风格 | `python3 scripts/run_cli.py --call-source skill build-biweekly-report --to-source --style-from 20260621-双周报-w25-本人.md`（风格文件在 `06-我的周报/YYYY/` 子目录中，系统自动递归查找）|
| 手动输出 | `python3 scripts/run_cli.py --call-source skill build-biweekly-report --output-file output/双周报.md` |

## 常见场景

| 用户说 | Claude 操作 |
|--------|-------------|
| 「帮我写一份关于微服务的分析报告」 | `iris build-report --query "微服务架构 最佳实践 经验总结" --output-format standard` |
| 「画个产品架构的思维导图」 | `iris build-mindmap --query "产品架构 模块设计" --format mermaid` |
| 「这周的双周报」 | 先 `iris build-biweekly-report --dry-run` 预览，确认后 `iris build-biweekly-report --to-source` |
| 「看看这周有多少文件要处理」 | `iris build-biweekly-report --dry-run` |
| 「导出 Word 版本」 | 先生成 md，再用 `--output-format docx` 转换 |

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 检索结果太少 | 建议扩大检索范围或调整查询词 |
| 报告质量不高 | 建议使用 `--two-stage` 模式重新生成 |
| DOCX 转换失败 | 检查 `python-docx` 是否安装：`pip install python-docx` |
| XMind 生成失败 | 检查输出目录写权限 |
