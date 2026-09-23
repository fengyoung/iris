# 主动情报能力使用说明

## 配置

在 `config/app.json` 中增加 `intelligence`（示例见 `config/app.json.example`）：

```json
{
  "intelligence": {
    "enabled": true,
    "okr_threshold": 0.65,
    "max_documents": 100,
    "max_prompt_chars": 48000,
    "okr_source": "01-目标管理/2026/当前周期个人OKR.md",
    "okr_cycle_id": "",
    "feishu_user_id": "ou_实际收件人标识"
  }
}
```

- `okr_source` 是 SOURCE 相对路径，必须显式指定，避免把个人 OKR 与部门 OKR 混淆。也可以设置飞书 `okr_cycle_id`，优先读取该周期。原生飞书周期通过 `lark-cli okr +cycle-detail --as user` 读取。
- 本地 OKR 支持现有标题格式 `## O1：目标`、`### KR1：关键结果`，沿用 `O1-KR1` 标识。路径隔离周期，变更 KR 全文、向量模型或阈值会重打标。
- `feishu_user_id` 支持回退环境变量 `IRIS_BOT_USER_ID`。发送使用当前 lark-cli 配置的机器人身份，因此部署前必须核对当前 profile 对应 Iris 应用。
- `max_documents` 限制每天增量处理量，未处理文档留待下一次执行，不会被游标跳过。正文超过 180,000 字的文档保留为待处理，不截断后冒充处理成功。
- 决策提取及情报包有 `max_prompt_chars` 预算，超过预算明确拒绝或跳过。未配置 SOURCE、Embedding 或飞书时会报告对应缺项，不影响已经完成的 daily-start 维护。
- 新增 LLM 路由写入实际使用的 `config/llm.json.example` 的 `routing.rules`，不是设计稿中的独立 `llm_routes.json`。调用同时携带角色选择，兼容现有配置中的角色路由。

## 决策库

```bash
iris decisions add --interactive
iris decisions add --input-file decision.json
iris decisions list --status open --owner 张三 --days 30
iris decisions search "双路方案"
iris decisions show DEC-20260921-0001
iris decisions update DEC-20260921-0001 --status implemented
iris decisions report --since 2026-07-01 --kr KR2 --output decisions.md
iris decisions export-wiki
```

`add` 表示人工确认录入，JSON 字段包括 `title`、`outcome`、`decided_at`，可提供 `owners`、`sources` 等。没有会议来源的人工决策仍可保存、列表和报告；问答引用通道要求可核验的 SOURCE 原文。

决策日期记录事实发生时间；DEC 编号使用本地入库日期，按日递增。相同日期、归一化结论相同的决策合并来源，不覆盖旧事实。措辞差异较大的近似决策不做激进去重；与已有同标题决策冲突时转人工审核，防止误合并或错误取代。

归档到 SOURCE 的 `transcribe-meeting` 结果会自动进入提取与复核；临时目录纪要不创建虚假的 SOURCE 引用，归档后才可进入此流程。提取最多两次尝试；复核失败转人工审核。纪要末尾分别展示正式记录与待审核数量。

```bash
iris decisions pending
iris decisions approve CAN-候选编号
iris decisions approve CAN-候选编号 --patch '{"owners":["张三"]}'
iris decisions reject CAN-候选编号 --reason "仍在讨论"
```

待审核记录不进入正式查询、图谱或情报包。拒绝不是取消已生效决策；审核状态与执行状态分开。

## 会前情报包

```bash
iris briefing --topic "Q4质检策略" --participants "张三,李四" --days 60 --date 2026-09-25 --output briefing.md
```

默认回溯 45 天，未指定输出路径时写入 `data/briefings/<主题哈希>-<会议日期>.md`，以安全文件名避免主题文本构成路径。会议日期只用于会议标记，证据检索截止本地今天。

复用混合检索，当前 SOURCE 再核验原文，按日期降序排列。只调用一次增强模型进行最终合成；现有检索模块自身的查询规划/Embedding 仍可能产生调用。参与人无记录时保留空结果，不制造动态。历史问题未检索到关闭证据时标记“待确认”，不直接断言仍未解决。

## 每日信号与 OKR

```bash
iris okr-evidence tag
iris okr-evidence list
iris okr-check --days 14 --output okr-check.md
iris signals --dry-run
iris signals
iris daily-start
```

`daily-start` 先积累 OKR 证据，再生成最多五条排序后的信号。信号同时展示在终端、保存到 `data/signals/`，发送给配置的飞书收件人。`signals --dry-run` 只显示与落盘，不发送。

相同日期、内容和收件人的摘要只发送一次。网络超时可能已经送达，因此记为 `uncertain`，不会盲目重发；在 `data/signals/delivery` 当前 generation 的索引中核验状态和错误后处理。通知失败不会丢失本地摘要。

feed 在正常收集时写入最小话题缓存，dry-run 不写。多群话题缺少标题匹配只表示候选文档缺口，不等于确认没有资料。新人名检测使用明确的 `[[人物-姓名]]` 引用，概念缺页要求至少两份文档引用，避免从普通中文段落猜姓名。

OKR 证据以完整内容指纹追踪，修改、删除、敏感来源或 KR 改动会使旧证据失效。每个 KR 有效证据不足三份时补充检索，然后合成小结。日志不足只表示文档覆盖不足，不代表实际工作停滞。

鲜度分只惩罚近期活跃且 Wiki 未跟上的文档流；无近期文档活动时返回正常，不触发提醒。

## 持久化与恢复

新模块使用 `schema_version=1`。决策、候选、计数器、证据日志和向量缓存通过 generation 目录写全后切换 `CURRENT`。不要把根目录 `index.json` 当作当前索引；当前索引位于 `generations/<CURRENT内容>/index.json`。

```bash
iris decisions migrate
iris okr-evidence migrate
```

迁移旧根目录索引前保存备份，未知高版本拒绝读写。迁移或发布失败不改变 CURRENT，因此旧快照仍可读取。JSONL 使用周期和 KR 的哈希文件名防路径注入；原始标识保留在记录内。所有读改写都在稳定 `FileLock` 内执行，锁文件不会删除。

## 验证边界

自动入库 95% 准确率、真实情报包 90 秒和 OKR 检查提速 50% 是实测验收目标，不是代码测试能证明的结果。需要在配置完整的环境准备经人工标注的非敏感真实纪要，记录自动通过/误判/待审核比例，并测量日志冷启动与积累后的检查耗时。
