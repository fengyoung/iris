# Iris 3.40.4

> 工作区待发布优化（2026-09-17）：谁是卧底完整轮次断点恢复、重复恢复保护、手动等待与页面恢复接线修复。使用方法与恢复边界见 [Web 使用说明](docs/undercover-web-ui.md)，验收证据见 [本轮报告](docs/undercover-beta-acceptance-20260917.md)。

工作知识助手 — 个人知识库（Obsidian Wiki）与飞书知识库集成。

## 最新动态

**v3.40.4 (2026-09-16)** - 谁是卧底：胜负条件改为严格多数 + 投票判据按复盘证据分级：
- ✅ **胜负条件对称化**：「卧底人数 ≥ 平民人数即卧底胜」→ **严格多数**（`>`），人数打平游戏继续、下一票直接决定胜负。原判定下卧底打平即胜、平民须清空卧底才能赢，两侧取胜成本不对等
- ✅ **一处必须同步改的隐性连动**：手动步进的 `continuing` 写的是「卧底**少于**平民」，只改胜负判定会漏掉它 → 打平轮**不发 `round_waiting`**，界面不报错、只是那一轮的手动步进被静默跳过。已补唯一守卫（实测旧代码下 3 人局得 `[]`、8 人局得 `[1,2,3]`）
- ✅ **投票判据分级**（本次主因）：上一局出局的 7 人中 **6 名是平民、票理由几乎全是「未提及型」**，而**唯一出局的卧底死于明确互斥矛盾**——旧文案恰恰把「未提及型」列为推荐判据，而复述共识元素是卧底最省力的伪装。现分**强证据**（对方写下的元素与你亲眼所见不能同时成立，一旦成立即投他）与**弱证据**（须过「无互斥矛盾 + 横向重合数明显最低 + 能说清为什么是他」三关）
- ✅ **「没有提到」不等于「没有看到」** + **自证不是复读**（用自己的话确认 2-3 项公共锚点）：薄描述不再等于可疑
- ✅ **否认型埋钩**：上一局卧底一句「我未见明显绿色招牌」被平民引用、投掉了另一名平民——现在明确「我没看到」不构成质疑理由，投票侧遇到否认也不改口、不转票
- ✅ 另补 **弃权的代价** 与 **「投中过卧底」不构成可信度证明**（上一局 3 名卧底靠投掉已暴露的同伴换取信任，一路潜伏到终局）
- ✅ 顺带修掉一条**假阳性回归钉**：`test_spies_win_at_parity` 的三条断言在新旧规则下都成立且没断言轮数，等于测不到它声称的规则
- ✅ 全量 3,598 → **3,609 通过**，协议版本 3.24（不变）

**上一版 v3.40.3 (2026-09-16)** - 谁是卧底：修三处静默失效 + 策略层按实战复盘校准：
- ✅ **「开始游戏」被合并遗留的悬空引用打断**：取了 main 的 HTML（裁判改成文本框）却保留 beta 的 JS，3 处 `getElementById('judge-role')` 指向不存在的元素，`startGame` 无 try 包裹 → 网页端自合并后根本开不了局
- ✅ **总结从未生成过**：前端改发 `referee_model` 后，后端仍只读 `req["summary_model"]` → `summary_model_id` 恒为空、复盘一律记 `skipped`。改为一律挂在裁判模型上，**取消的对局也出总结**
- ✅ **投票理由与投票对象对不上**：解析器兜底会扫整段输出并按 **key 字符串长度**挑目标，与模型真正投的人无关（且与自身文档字符串矛盾）。现在只在「投票：」行内解析，多候选时宁可弃权也不猜
- ✅ **两套玩家编号并存且会分叉**：历史用发言位次、prompt 用全局固定编号，而发言顺序开局即打乱——统一为 `N号=role/model_id`
- ✅ Web 交互三项：裁判默认官方 `deepseek-flash` 且与玩家互斥、参与模型全选/全取消、开局配置存 `localStorage` 下次自动恢复
- ✅ 客户端断开（刷新/取消 SSE）不再把几十行栈刷满终端
- ✅ 策略层按一局真实复盘校准：身份自评纳入「互斥属性冲突」、卧底看到真实差异时的出路、禁止整段复述、首发提示改为「给锚点」
- ✅ 全量 3,572 → **3,597 通过**，浏览器回归 9 → **22 项**，协议版本 3.24（不变）

**上一版 v3.40.2 (2026-09-16)** - 修「上传失败」：像素上限误伤正常照片 + 图片归一化：
- ✅ 像素上限 2000 万 → **6400 万**：原上限把一张 5489×3659（2008 万像素）的正常照片拒了，只超出 0.42%，且该图当天早些时候曾成功上传
- ✅ 三类失败**分开报**（格式不符 / 尺寸无效 / 超像素），不再合并成一句让用户无从判断该换图还是该改名
- ✅ 修一处错误信息失真：`IrisValueError` 多继承了 `ValueError`，使其自身在 `except (RuntimeError, ValueError)` 内被改写，「图片容器无效」分支永不可达
- ✅ 新增图片归一化：长边封顶 **2048px**，只缩不放；未超限的图原样返回不重新编码
- ✅ 归一化收益（本机实测）：2.82MB → 0.37MB，base64 单次 3.76MB → 0.50MB，**每局图片上行 180MB → 24MB**
- ✅ 补上该模块**首个直接测试**（此前零覆盖，故原上限误伤时无测试拦得住）；全量 3,540 → **3,572 通过**，协议版本 3.24（不变）

**上一版 v3.40.1 (2026-09-16)** - 谁是卧底 Web 状态恢复与观战交互优化（合并 0916-beta）：
- ✅ 新增对局状态查询与断点恢复接口：刷新页面可恢复观战，中断的对局可从 `checkpoint.json` 续跑
- ✅ 手动步进加相位守卫（仅 `waiting` 态可推进，重复/过期请求返回 409，不再被静默吞掉）
- ✅ 前端补历史检索、轮次手风琴展开保持、总结区 5 秒轮询、玩家 checkbox 选择与键盘可操作
- ✅ 新增浏览器回归 `tests/browser/undercover_assertions.js`（真实 DOM，不调模型；本版用它验证合并结果）
- ✅ 合并冲突取并集而非二选一；修融合中丢失的 `playerRowHtml` 定义，统一两侧各自的玩家编号约定
- ✅ 修 4 个既有失败用例（`bb28f1e` 引入增量校验后夹具未同步，合并前即红）；全量 **3,540 通过**，协议版本 3.24（不变）

**上一版 v3.40.0 (2026-09-16)** - 安全、数据一致性与检索可信度系统加固：
- ✅ 封闭游戏上传/复盘路径越界与符号链接通道，严格校验图片格式、像素、体积、请求来源和并发资源
- ✅ 增量 chunk 与向量索引加入完整快照、统一锁和乐观并发控制，拒绝陈旧发布及不兼容模型
- ✅ Embedding 严格验证返回索引/数量/维度/有限数值；语义命中补齐正文引用并采用对称 RRF
- ✅ LLM 缓存键纳入生成参数及配置指纹；游戏支持协作取消、多观察者广播和 SSE 断线续传
- ✅ 新增本地检索黄金集评测及关键模块覆盖率门禁；全量 **3,538 通过**，协议版本 3.24（不变）

**v3.39.2 (2026-09-16)** - sync-memory frontmatter 定界加固：修一处静默截断：
- ✅ CC 记忆文件的 frontmatter 里出现三连字符字面量时（如在 description 里举例 YAML 分隔符），旧的 `text.split("---", 2)` 会从该处截断——`type` 解析为空串、该条记忆**永远同步不到 Iris**，而同步照跑、退出码 0
- ✅ 这个洞已真实发生两次：`wiki-lint-fix-bug.md` 首次踩坑留下 6 行残骸（截断尾部 + 补 `metadata: type:` 补在了字面层正文里），本次同步核验时同类写法再次复现
- ✅ 新增 `_FRONTMATTER_RE` + `_split_frontmatter()`：闭分隔符须**独立成行**才算数，`_parse_frontmatter` / `_extract_body` 统一走它
- ✅ 顺带修正两处更早的误判：`---xyz`（整行并非分隔符）与缩进 `  ---`（YAML 块标量内容）；清理 6 个死代码常量
- ✅ 8 项防回归测试锁定的是「旧实现确实失败」的行为（反证：同一输入下旧逻辑 `type` 得空串、新实现得 `project`）
- ✅ 全量 **3,502 通过**；协议版本 3.24（不变）

**上一版 v3.39.1 (2026-09-15)** - SBOM 产品版本改读源码树：修一处无失败信号的错报：
- ✅ 归档的 SBOM 曾把 iris 声明成 **3.27.0**（源码树是 3.39.0）——editable 安装的 `.dist-info` 冻结在上次重装那一刻，改源码/升版本/提交/推送都不刷新它
- ✅ 危害在于**没有失败信号**：SBOM 照常生成、SPDX 结构合法、退出码 0，只有人工比对 `pyproject.toml` 才看得出
- ✅ 新增 `read_product_version()`：iris 自身读 `pyproject.toml`，第三方依赖仍读已安装元数据（各取其可信来源）
- ✅ 读不到版本时打印原因并返回 1 且**不写出文件**——宁可不生成，也不要生成一份说谎的发布制品
- ✅ 10 项测试以 monkeypatch 让元数据谎报 3.27.0、断言 SBOM 仍写 pyproject 版本（刻意让两来源冲突，只断言「版本正确」在重装后会假通过）
- ✅ 全量 **3,494 通过**；协议版本 3.24（不变）

**上一版 v3.39.0 (2026-09-15)** - 谁是卧底 Web 界面：游戏实时观战与复盘存储：
- ✅ 新增 `iris undercover-game-web`：stdlib `ThreadingHTTPServer` 本地服务（默认 `127.0.0.1:7862`），零新增依赖（[界面说明](docs/undercover-web-ui.md)）
- ✅ 实时观战：SSE 事件流分层展示思考卡（私有档案，🔒 标注）/ 描述卡 / 投票卡 / 裁判陈述，玩家色按 HSL 色相均匀分配
- ✅ 手动步进模式：每轮结束等待确认再进入下一轮；裁判模型前后端双重校验不得与参与玩家重复
- ✅ 对局持久化：每局落盘 `data/games/<game_id>/`（图片副本 + `replay.json` 全量记录 + 后台生成 `summary.md`）
- ✅ 事件系统对 `UndercoverGame` 改动最小：新增 `on_event` / `advance_event` 可选参数，默认 None 无操作，原有 CLI 向后兼容
- ✅ 全量 **3,484 通过**；协议版本 3.23→**3.24**（CLI 命令集 69→70）

**上一版 v3.38.2 (2026-09-15)** - CI workflow action 升级至 v7：消除 Node.js 20 废弃告警：
- ✅ `actions/checkout` v4→v7、`actions/setup-python` v5→v7、`actions/upload-artifact` v4→v7——三者入参均无变化
- ✅ `codecov/codecov-action` v4→v7，**`file` 改名 `files`**（v5 起删除旧名）——旧名不报错、只被静默忽略，表现为「CI 全绿但覆盖率不再上传」
- ✅ 先读各 `v*.0.0` 发布说明再逐个比对目标 tag 的 `action.yml`，最后才改；批量替换版本号会踩空 codecov 这处
- ✅ PR #1 真实 CI 验证：四 job 全绿，**告警数 1 → 0**（Node 20 告警消失）
- ✅ 仅改 CI，无产品/协议/数据变更；协议版本 3.23（不变）

**上一版 v3.38.1 (2026-09-15)** - 依赖下界收紧：requests / pytest / soupsieve：
- ✅ `requests>=2.31` → `>=2.33`（2.32.5 命中 PYSEC-2026-2275）
- ✅ `pytest>=7.0` → `>=9.0.3`（9.0.2 命中 PYSEC-2026-1845）
- ✅ `weekly` extra 补 `soupsieve>=2.8.4`——bs4 对它的下界（`>=1.6.1`）过松，抬 bs4 挡不住；2.8.3 有两项 High 通告（选择器 ReDoS / 内存放大）
- ✅ 裁定「下界不撒谎」：三处按我们的用法边界都够不到，但声明的最低可满足版本不应是已知漏洞版本
- ✅ 未收紧 `urllib3` / `idna`：二者是 requests 的传递依赖，为其声明下界等于谎报「直接依赖」；全新解析实测已拿到 2.7.0 / 3.19
- ✅ 无功能变更；协议版本 3.23（不变）

**上一版 v3.38.0 (2026-09-15)** - 多模型对抗游戏「谁是卧底」：新增 `games` 模块与 LLM 精确模型调用：
- ✅ 新增 `iris undercover-game`：把 `llm.json` 全部可用模型当玩家，随机抽 1–2 名卧底看图片 B，其余平民看图片 A（[方案设计](docs/undercover-game-design.md)）
- ✅ **玩家不知道自己是谁**：卧底与平民拿到的 prompt 逐字相同，只能从他人公开发言推断自己是否少数派（与常见实现的关键差异）
- ✅ 顺序描述（每轮起点顺延、后发言者可参考前面所有人）+ 并行投票；私有面（观察清单/身份自评）只进复盘档案，永不进他人 prompt
- ✅ 防「装死」：调用失败与格式不合规用不同占位符，后者留在比对块内——否则拒绝输出即可免疫投票
- ✅ LLM 层新增 `generate_as()` / `generate_multimodal_as()` 按 `(role, model_id)` 精确定名调用（既有 `force_model` 按 `model` 字段字符串匹配，同名复用时会误中），失败不降级
- ✅ 新增 122 项测试；全量 **3,484 通过**；协议版本 3.22→3.23（CLI 命令集 68→69）

**上一版 v3.37.6 (2026-09-13)** - 修复人物页「周报时间线」断档：
- ✅ 三层根因：`_chunk_to_hit` 未传递 BM25 分（下游在 0 分基线上排序，相关性序被抹掉）→ `search()` 同分兜底按路径升序致人名查询永远取**最旧** N 份 → 人名在周报正文出现 0 次，词法检索物理上召回不到
- ✅ 新增 `_doc_date_ord()` 同分日期降序兜底 + `LocalRetriever.latest_documents()` 按文档身份定向取最新若干份代表块（仅 `page_type == "person"` 生效）
- ✅ `WikiGenerator._collect_evidence()` 周报通道优先占槽；新增 21 项防回归测试
- ✅ 已知限制：`is_wiki_stale()` 只看已在指纹里的文档，新增周报仍需 `wiki-update --title` 手动推进

**项目规模**：~45,000 行代码 / 190 文件 / 28 模块 / 3,494 测试用例 / 69% 覆盖率

详见 [CHANGELOG.md](CHANGELOG.md)、[本轮工程优化记录](docs/optimization-three-phase-20260910.md) 和 [优化报告](optimization_report_20260909.md)。

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

# Iris 是仓库型应用；从其他目录启动时可显式指定仓库根目录
export IRIS_PROJECT_ROOT=/path/to/iris3

# 配置
cp .env.example .env
cp config/app.json.example config/app.json
cp config/llm.json.example config/llm.json
cp config/data_source.json.example config/data_source.json
# 编辑上述文件，填入 API Key 和路径
# llm.json 已预配置 DeepSeek (文本) + 百炼 Qwen (多模态) 双 Provider
# 可选：cp config/llm_pricing.json.example config/llm_pricing.json 后填入单价，启用 usage-stats --cost 成本估算

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
| 数据管道 | `scan-source`, `build-chunks`, `build-vector-index`, `frontmatter-batch` | 文档扫描 / 切块 / 向量索引 / 批量补全 YAML frontmatter（正则+LLM+wikilink+备份恢复） |
| 检索问答 | `search`, `ask` | 混合检索（BM25 全文 + 向量）+ LLM 问答（支持图文输入，图谱增强） |
| Wiki | `discover-wiki`, `build-wiki`, `wiki-update` | 发现 / 生成 / 增量更新 |
| 知识图谱 | `build-graph [--full] [--page]`, `graph-query --op ...` | 实体节点 + wikilink 边 + LLM 关系提取，增量更新；邻居/相关/路径/孤页/桥接/密度查询 |
| 质量保障 | `wiki-lint`, `wiki-lint --fix`, `deep-eval` | 结构检查 + 孤页检测 + 内容准确性/全面性校验 |
| 报告 | `build-report`, `build-mindmap`, `build-biweekly-report` | 专题报告 / 思维导图 / 双周报 |
| 会议 | `transcribe-meeting`, `batch-transcribe`, `build-asr-prompt` | 转录纪要 / 批量处理 / ASR 三段校正 |
| 飞书 | `feishu-doc-convert`, `chat-digest` | 文档转换 / 聊天记录提炼 |
| 记忆 | `memory-*`, `working-set`, `sync-memory` | 记忆管理 / 工作上下文 |
| 人物 | `enrich-persons` | 飞书通讯录自动补充人物 Wiki 的部门/邮箱信息 |
| 工具 | `process`, `trello`, `extract-weekly-reports`, `extract-travel-invoice` | 富媒体处理（图片/PDF/DOCX/视频）/ 看板 / 周报提取 / 行程单报销 |
| 用量 | `usage-stats [--by day/week/month/year] [--cost]` | LLM 调用/token 消耗统计（分模型 + 汇总，多粒度聚合，可选成本估算） |
| 提醒 | `reminders` | 主动提醒：栏目断供 / 成员周报缺失 / 项目停滞（零 LLM 成本，daily-start 已集成） |
| 系统 | `daily-start`, `check-config`, `status`, `diagnose`, `workspace` | 日常维护（含图谱增量刷新）/ 配置检查 / 工作空间查看 |
| ASR 校正 | `asr-corrector`, `asr-audit`, `asr-report` | vocotype 实时语音转写纠错润色（[使用指南](docs/asr-corrector-usage.md)） |
| 会议助理 | `meeting-live-assistant` | 实时 AI 会议参谋：本地麦克风转写（FunASR）+ 逐段提炼要点/风险/决策/建议提问 + 话题追踪 + 说话人区分 + 洞察推送 + 热键 + 按话题过程文档（[使用指南](docs/meeting-live-assistant-usage.md) · [方案设计](docs/meeting-live-assistant-design.md)） |
| 任务面板 | `task-panel` | Web 只读展示 iris 任务状态与进程：常驻守护 + 任务埋点 + 探测兜底（[使用指南](docs/task-panel-usage.md) · [方案设计](docs/task-panel-design.md)） |
| 多模型对抗 | `undercover-game --image-a A --image-b B` | 「谁是卧底」：全部可用模型当玩家，玩家不知自己身份，顺序描述 + 并行投票（[方案设计](docs/undercover-game-design.md)） |

工程可靠性与运维约定见 [可靠性设计](docs/engineering-reliability-design.md) 和 [可靠性使用指南](docs/engineering-reliability-usage.md)。

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
├── 08-参考资料/
└── 09-工作简报/
```

## 模型配置

| 角色 | 默认模型 | 协议 | 能力 | 降级链（按优先级） |
|------|---------|------|------|--------|
| `base_model` | `claude-sonnet-5-zz` | Anthropic | 文本 + 图片 | → qwen3.8-flash-zz → deepseek-flash-zz → deepseek-flash |
| `adv_model` | `claude-fable-5-zz` | Anthropic | 文本 + 图片 | → qwen3.8-max-zz → qwen3.7-plus-zz → qwen3.6-plus-zz → qwen3.8-flash-bl → qwen3.7-plus-bl → gpt-5.6-sol-zz |

模型 ID 后缀标识所属通道：`-zz` = `zz_tokenhub` 中转（Anthropic 兼容接口 `/anthropic`、OpenAI 兼容接口 `/codex/v1`）、`-bl` = 百炼官方直连、无后缀 = DeepSeek 官方直连。全部模型均支持多模态输入。

路由规则（12 条）：用户显式指定模型 → 多模态输入 → 周报提取 → Prompt 生成 → 复杂分析 → Wiki 重建 → 问答 → ASR 校正/误识别/热词 → 文本兜底。

模板见 `config/llm.json.example`；本机生效配置为 `config/llm.json`（gitignored），停用模型归档于 `config/llm.models-archive.json` 备查。

## 技术栈

- Python 3.11+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 Qwen 多模态）
- Pydantic v2（配置类型安全校验）
- lark-cli（飞书接口，步骤 3）
- macOS Keychain（可选密钥存储）
- PyMuPDF / python-docx（PDF/DOCX 处理）
- ffmpeg（视频抽帧/抽音轨，视频处理必需）+ openai-whisper（音轨转写，可选）
- 3,538 个测试用例（pytest 全量），覆盖率 69.63%；Ruff、mypy（含显式兼容边界）、AST 安全扫描与 SPDX SBOM 门禁通过

## 开发环境

```bash
# 克隆并安装（含开发依赖）
git clone <repo-url> && cd iris3
pip install -e ".[dev]"

# 快速命令（Makefile）
make test            # 运行全部测试
make test-unit       # 运行 unit 标记测试
make test-integration # 运行 integration 标记测试
make test-cov        # 运行测试 + 覆盖率报告
make lint            # Ruff 代码检查
make lint-fix        # Ruff 自动修复
make format          # 代码格式化
make clean           # 清理缓存

# 或直接使用 pytest
python -m pytest tests/ -q
python -m pytest tests/ -q --cov=iris --cov-report=term

# 提交前检查（pre-commit）
pre-commit install   # 安装 Git hooks
pre-commit run --all-files  # 手动全量检查

# 配置（参考快速开始章节）
cp config/*.json.example config/  # 然后编辑各 .json 填入实际值
```

### 项目结构

```
iris3/
├── src/iris/           # 28 模块
│   ├── app/cli/        # CLI 入口 + 69 个公开命令
│   ├── analysis/       # 分析服务（报告/思维导图/双周报）
│   ├── complex_input/  # 多模态三阶段（图片/PDF/DOCX/VIDEO）
│   ├── config/         # 配置加载 + Pydantic 校验
│   ├── core/           # 类型/锁/存储/Agent 适配
│   ├── evaluation/     # Wiki 深度评估
│   ├── feed/           # 信息汇聚管道（飞书→话题→简报）
│   ├── feishu/         # 飞书文档转换 + 聊天提炼
│   ├── games/          # 多模型对抗游戏（谁是卧底）
│   ├── ingest/         # 文档扫描 + 切块
│   ├── llm/            # Provider/路由/LLMService/用量统计
│   ├── memory/         # 记忆系统（6 子模块）
│   ├── output/         # 格式化 + DOCX 输出
│   ├── qa/             # 检索增强问答
│   ├── retrieval/      # BM25 + 向量 + RRF 混合检索
│   ├── trello/         # Trello 看板
│   ├── utils/          # 工具函数
│   └── wiki/           # Wiki 体系（最大模块，含图谱/ASR/反向引用）
│       └── asr/         #   ASR 提示词子系统（术语提取/热词/Prompt优化/版本管理）
├── scripts/            # CLI 入口 + 委托脚本
├── templates/          # Prompt / Wiki 模板
├── tests/              # 3,538 用例
│   ├── unit/           #   按 unit 标记运行；全项目 unit 2,336 项
│   ├── integration/    #   集成测试（248 用例）
│   └── （顶层）        #   按模块组织的端到端/回归测试；全项目 integration 1,202 项
├── config/             # *.json gitignored，*.example 版本控制
├── .github/workflows/  # CI 流水线（Python 3.11-3.13 矩阵）
├── Makefile            # 常用开发命令
├── Dockerfile          # 非 root CLI 运行镜像
└── pyproject.toml      # 项目配置 + pytest/coverage/ruff 设置
```

## 版本历史

> 逐版完整变更记录见 [CHANGELOG.md](CHANGELOG.md)；下表为按版本倒序的要点摘要。

| 版本 | 日期 | 要点 |
|------|------|------|
| **v3.40.4** | 2026-09-16 | **谁是卧底：胜负条件改为严格多数 + 投票判据按复盘证据分级**：两件事。① **胜负条件对称化**——原判定 `spy_alive >= civ_alive` 让 3v3 时卧底直接获胜，而平民必须清空卧底才能赢，两侧取胜成本不对等（单卧底时即退化为旧的「存活 ≤2 且卧底存活」）；改为 `>` 后人数打平游戏继续、下一票直接决定胜负。② **一处必须同步改的隐性连动**——手动步进的 `continuing` 写的是「卧底存活且**少于**平民」，与旧判定互补；只改 `>=`→`>` 而漏掉这处，打平轮就**不发 `round_waiting` 事件**，`web_server` 相位置不成 `waiting`、前端步进按钮不启用，对局照跑、界面不报错、只是那一轮的手动步进被静默跳过；已补 `test_round_waiting_emitted_at_parity_in_step_mode` 作为唯一守卫（旧代码下 3 人局得 `[]`、8 人局得 `[1,2,3]`，与预期的 `[1]`/`[1,2,3,4]` 不同，实测确认能抓住）。③ **策略层主因：投票判据在惩罚「发言简省」而非识别身份差异**——复盘一局 12 人局（8 平民 vs 4 卧底，卧底胜）的逐轮票理由：出局的 7 人中 **6 名是平民且票理由几乎全是「未提及型」**（11 号 4 票、10 号 5 票、末轮 1 号皆如此），**唯一出局的卧底死于明确互斥矛盾**（他说「天空漆黑无星」而多人说「一颗亮星」），**没有任何一名卧底死于未提及型证据**；旧文案却恰恰把它列为推荐判据（原文「而多数人提到的核心元素他完全没有提到」）——复述共识元素是卧底最省力的伪装，这条判据惩罚的正是做不到这一点的真同伴。现改为**证据分两档**：强证据 = 对方写下的元素与你亲眼所见不能同时成立（一旦成立即投他），弱证据 = 未提及型，须同时满足「无互斥矛盾 + 横向重合数明显最低 + 能说清为什么是他」。④ **「没有提到」不等于「没有看到」**（50 字上限/位次靠前信息少/系统故障会少说）+ **自证不是复读**（用自己的话确认 2-3 项公共锚点，与「禁止整段复述」并存且边界一致）。⑤ **否认型埋钩**——上一局卧底 6 号用一句「我未见明显绿色招牌」埋钩，平民 1 号引用它投掉了说「有绿色招牌字」的平民 7 号；现明确「我没看到」不构成质疑理由，投票侧遇到否认也不改口、不转票。⑥ 另补**弃权的代价**与**「投中过卧底」不构成可信度证明**（上一局 6/8/9 号第 3 轮集体投掉已暴露的 12 号后一路潜伏到终局）。⑦ **已知交换比不掩饰**：以上保护了卧底最便宜的伪装、卧底更难被抓，止损靠薄描述不再可疑与裁判增量要求（第 2 轮起卧底须持续吐新元素，而新元素只能取自它自己那张图）——净效果是把暴露从中盘往后拖而非免于暴露。⑧ 顺带修掉一条**假阳性回归钉**：`test_spies_win_at_parity` 三条断言在新旧规则下都成立且没断言轮数。全量 3,598 → **3,609 通过**（+11），协议版本 3.24（不变），数据版本与对局 `schema_version` 不变（Web 端与复盘层零改动） |
| **v3.40.3** | 2026-09-16 | **谁是卧底：修三处静默失效 + 策略层按实战复盘校准**：三个用户可见现象根因各不相同，但都属「功能照跑、界面不报错、只是结果不对」。① **「开始游戏」被合并遗留的悬空引用打断**——合并 0916-beta 时取了 main 的 HTML（裁判从下拉改成文本框 `referee-model-input`）却保留 beta 的 JS（3 处 `getElementById('judge-role')`），元素不存在即抛 `TypeError`，`startGame` 无 try 包裹，网页端自合并后根本开不了局；函数集合、全局变量、悬空函数调用三类检查都查不出这种「函数都在、引用的元素没了」。② **总结从未生成过**——前端在 `bb28f1e` 后改发 `referee_model`，后端 `_handle_game_start` 仍只读 `req["summary_model"]`，`summary_model_id` 恒空、复盘一律记 `summary_status=skipped`；改为总结挂在裁判模型上，断点恢复同样回落，**取消的对局也出总结**并把 `cancelled` 译成「部分复盘」。③ **投票理由与投票对象对不上**——`_parse_vote` 兜底会扫整段输出并按 **key 字符串长度**取最长者，而模型常在分析前言里逐个列 key，于是挑中谁只取决于长度；该实现还与自身文档字符串矛盾（文档写「计为弃权」），自 v3.38.0 即在。现在只在「投票：」行内解析（key → 编号 → 仅当全文恰好提到一个候选时才采纳散文式表达），多候选宁可弃权。④ **两套编号分叉**——描述 prompt 用 `player_number`、历史块用发言位次，而发言顺序开局即 `shuffle`、每轮顺延，**第 1 轮起就不一致**；全部引用点统一为 `N号=role/model_id`，解析器相应支持编号。⑤ **Web 交互三项**：裁判默认官方 `deepseek-flash` 并与玩家互斥（前端禁用+后端硬校验，原先只校验 `summary_model` 没校验 `referee_model`）、参与模型全选/全取消、开局配置存 `localStorage`。⑥ 客户端断开（刷新/取消 SSE 触发 RST）不再把几十行栈刷满终端。⑦ **策略层按实战复盘校准**：一局 4 人局中卧底主体元素全对得上、只有服饰与多数人互斥，旧口径只认「主体元素几乎无人提及」→ 自评**平民 96%** → 按平民目标当众纠正他人 → 一轮出局；据此补「互斥属性冲突」判据、给卧底一条「看到真实差异既不指出也不附和」的出路、禁止整段复述（实测有描述与他人 93% 相似）、首发提示从「少说」改成「给可核对锚点」。按用户选择，「禁止任何信号」原样保留。全量 3,572 → **3,597 通过**，浏览器回归 9 → **22 项**，协议版本 3.24（不变） |
| **v3.40.2** | 2026-09-16 | **修「上传失败」：像素上限误伤正常照片 + 图片归一化**：用户报告谁是卧底 Web 端多次上传失败，根因是 v3.40.0 引入的 **2000 万像素上限**把一张 5489×3659（2008 万像素）的正常照片拒了——只超出 0.42%，且该图当天早些时候曾成功上传。上限改为按「会不会解压炸弹」定而非「照片够不够小」：2000 万 → **6400 万像素**，覆盖 48MP 手机与 61MP 全画幅，而解码炸弹动辄数亿像素；仍保持「解码前从文件头判定」。**三类失败分开报**（格式不符 / 尺寸无效 / 超像素），原先合并成一句「图片格式不匹配、尺寸无效或超过 2000 万像素」，用户只能看到「上传失败」而无法判断该换图还是该改名。顺带修一处**错误信息失真**：`IrisValueError` 多继承了 `ValueError`，而 `validate_image` 的 `except (RuntimeError, ValueError)` 就套在 `raise IrisValueError('图片容器无效')` 外层——该分支被自己的异常处理器捕获改写为「图片数据损坏」而**永不可达**。新增图片归一化 `downscale_for_upload()`：上传时把长边封顶 **2048px**，只缩不放（未超限的原样返回不重编码）；放在暂存前，故预览 / 模型输入 / 复盘三处是同一张图。尺寸取 2048 系**实测**：`deepseek-flash-zz` 对 20MP 与 1568px 给出完全相同的 977 token（服务端已归一化），而 `qwen3.8-max-zz` 随尺寸增长并在长边 2048px 触顶（2048 与 3072 同为 2538 token）——**按模型而异，不能假定服务端一定会缩**。收益：2.82MB → 0.37MB，base64 单次 3.76MB → 0.50MB，**每局图片上行 180MB → 24MB**。实现中踩到并修掉 `page.rect ≠ 像素尺寸`（fitz 按 96 DPI 折算，比值随声明 DPI 为 0.75 或 1.0）：按像素算缩放会让 3000px 的图只缩到 1536px，相机图恰好比值为 1.0 故未暴露、截图 PNG 必中。`fitz` 只能编码 png/jpg，bmp/gif/webp 缩放时转 PNG 且扩展名随内容变更。**补上该模块首个直接测试**（此前零覆盖）32 项。全量 3,540 → **3,572 通过**，协议版本 3.24（不变） |
| **v3.40.1** | 2026-09-16 | **谁是卧底 Web 状态恢复与观战交互优化**（合并 0916-beta）：新增对局状态查询 `/api/game/<id>`（`phase`/`round_no`/`replay_ready`/`sequence`）与断点恢复 `/api/game/<id>/resume`（读 `checkpoint.json` 重建 `GameSession` 续跑，已完成 400 / 无断点 404 / 并发超限 429），另有未完成对局列表与配置默认值接口；手动步进加相位守卫——仅 `waiting` 态可推进，重复或过期请求返回 409 而非被静默吞掉（`undercover.py` 的 `round_waiting` 事件与前端「等待继续」三处对齐）。前端补历史对局按模型/ID/种子搜索、轮次手风琴展开保持、总结区 5 秒轮询（区分生成中/已完成/失败/未启用）、玩家 checkbox 选择与键盘可操作；新增浏览器回归 `tests/browser/undercover_assertions.js`（真实 DOM，不调模型）。**合并冲突取并集而非二选一**：两侧都大幅改写了 `web_server.py` 与 `undercover_web.html`，核验手法是比较「worktree 相对每一侧删了哪些行」并提取函数集合做包含关系检查——比通读 diff 更能定位静默丢失；由此修掉融合中丢失的 `playerRowHtml` 定义（调用点在、定义没了，玩家行添加即 `ReferenceError`，任一侧都不缺、是合并新造的错误）。玩家编号两侧各自独立引入（main 的 `{n}号` 用 `player_number` 全局固定编号、淘汰后不漂移；beta 的 `P{i+1}` 是位置号），统一为前者并把筛选下拉框与浏览器断言一并对齐。另修 4 个**合并前即红**的既有失败用例：`bb28f1e` 引入裁判增量校验后夹具把所有 `generate_as` 调用一律记为 `vote`，修法按「只能投给以下候选之一：」标记区分相位。全量 3,540 通过，浏览器回归 9 项 PASS，协议版本 3.24（不变） |
| **v3.40.0** | 2026-09-16 | **安全、数据一致性与检索可信度系统加固**：封闭游戏 Web 路径越界、符号链接、跨站与资源耗尽通道；SSE 改为多订阅者广播并支持续传；游戏支持协作取消及部分复盘。增量 chunk 与向量索引加入完整快照、统一锁和乐观并发控制；Embedding 严格验证响应完整性；向量检索补齐正文引用并使用对称 RRF；LLM 缓存纳入生成参数和配置指纹。新增本地检索黄金集评测及关键模块覆盖率门禁。全量 3,538 通过，总覆盖率 69.63%，协议版本 3.24（不变） |
| **v3.39.2** | 2026-09-16 | **sync-memory frontmatter 定界加固**：修一处静默截断——CC 记忆文件的 frontmatter 里出现三连字符字面量时，`_parse_frontmatter` / `_extract_body` 的 `text.split("---", 2)` 会从该处截断 frontmatter，其后的 `type` 等字段跌出解析范围、`type` 解析为空串、`_classify` 判为「无 type」而跳过，**该条记忆永远同步不到 Iris，而同步照跑、退出码 0**。这个洞已真实发生两次：`wiki-lint-fix-bug.md` 首次踩坑留下 6 行残骸（截断尾部 + 有人试图补 `metadata: type:` 却补在了字面层的正文里），本次记忆同步核验时同类写法再次复现，靠比对 `_parse_frontmatter` 的实际返回值才发现。修法：新增 `_FRONTMATTER_RE`（`\A---[ \t]*\n(.*?)^---[ \t]*$`）与 `_split_frontmatter()`，闭分隔符须**独立成行**才算数，上述两个函数统一走它；无 frontmatter 返回 `None`、空 frontmatter 返回空串以区分二者。顺带修正 `---xyz`（整行并非分隔符）与缩进 `  ---`（YAML 块标量内容）两处更早的误判，并清理 6 个死代码常量。行为契约零变化（既有 90 项测试全通过：`_extract_body` 仍保留闭分隔符后的换行、正文三连字符原样保留）。8 项防回归测试锁定的是「旧实现确实失败」的行为——反证脚本以同一输入跑旧逻辑得空串、新实现得 `project`；只断言「新实现正确」在旧实现下也可能假通过。全量 3,502 通过。协议版本 3.24（不变） |
| **v3.39.1** | 2026-09-15 | **SBOM 产品版本改读源码树**：按 `RELEASE_CHECKLIST` 补跑 v3.39.0 门禁时，发现归档的 SBOM 把 iris 声明成 **3.27.0**（源码树是 3.39.0）。根因是「用安装状态代替源码事实」——`generate_sbom.py` 用 `importlib.metadata` 取全部包版本，这对第三方依赖是对的（`.dist-info` 由 pip 写入，与落盘代码一致），但对 iris 自身是错的：本机 editable 安装的 `.dist-info` 冻结在**上次重装那一刻**，改源码/升版本/提交/推送都不刷新它（`pip show iris` 实测停在 3.27.0，即上次重装时的版本）。**危害在于没有失败信号**：SBOM 照常生成、SPDX 结构合法、退出码 0，而它是供应链审计与漏洞追踪的输入，声明错版本意味着后续按 SBOM 排查会对错版本的代码，只有人工比对 `pyproject.toml` 才看得出。修法：新增 `read_product_version()` 从 `pyproject.toml` 读 `project.version`，iris 自身走这条路、第三方仍走已安装元数据（各取其可信来源），用 tomllib 维持「只依赖标准库」承诺；读不到版本时打印原因并返回 1 且**不写出文件**——静默退回旧元数据等于保留原 bug，发布制品宁可不生成也不要生成一份说谎的。10 项测试的核心是 monkeypatch 让元数据谎报 3.27.0、断言 SBOM 仍写 pyproject 版本（刻意让两来源冲突，只断言「版本正确」在重装后会假通过）。顺带如实固定 SPDXID 的既有形态（`"-".join()` 逐**字符**拼接，`iris-9.8.7` → `i-r-i-s---9---8---7`），避免日后改动连带改 ID 格式使新旧 SBOM 无法按 ID 对比。未改 mypy 对该脚本的 4 处既有报错：HEAD 同样存在且门禁范围是 `mypy src/iris`，`scripts/` 从不在内。全量 3,494 通过。协议版本 3.24（不变） |
| **v3.39.0** | 2026-09-15 | **谁是卧底 Web 界面**：为多模型对抗游戏补上 Web 观战与复盘存储。`iris undercover-game-web` 用 stdlib `ThreadingHTTPServer` 起本地服务（默认 `127.0.0.1:7862`，与 taskpanel 一致、零新增依赖）；SSE 事件流把思考卡（私有档案，🔒 标注）/ 描述卡 / 投票卡 / 裁判陈述分层呈现，玩家色按 HSL 色相均匀分配、淘汰状态实时同步；可选手动步进，每轮结束等确认再继续。裁判模型前后端双重校验不得与参与玩家重复。每局完成后落盘 `data/games/<game_id>/`（图片副本 + `replay.json` 全量记录含私有思考 + 后台生成 `summary.md`，含转折点分析与各模型表现点评）。对 `UndercoverGame` 的改动刻意压到最小——只新增 `on_event` / `advance_event` 两个可选参数并在 7 个关键节点推事件，默认 `None` 无操作，原有 CLI 路径不受影响。全量 3,484 通过。**协议版本 3.23→3.24（CLI 命令集 69→70）** |
| **v3.38.2** | 2026-09-15 | **CI workflow action 升级至 v7**：GitHub 持续告警 `Node.js 20 is deprecated`（action 被强制跑在 Node 24 上），本次把 `ci.yml` 用到的四个 action 全部升到最新 major——`actions/checkout` v4→v7（2 处）、`actions/setup-python` v5→v7（2 处）、`actions/upload-artifact` v4→v7、`codecov/codecov-action` v4→v7。前三者入参无变化（`setup-python` v7 删的是我们没用到的 `pip-install`）；**只有 codecov 这处会坏**——v5 起 `file` 已删除、改名 `files`，而旧名**不报错、只被静默忽略**，表现为「CI 全绿但覆盖率不再上传」，是个不会自己暴露的洞，已一并改正并写进注释。方法上先取各 `v*.0.0` 发布说明读破坏性变更、再逐个拉目标 tag 的 `action.yml` 比对入参，最后才改。PR #1 真实 CI 验证四 job 全绿（macos-smoke 14s / 3.11 2m39s / 3.12 2m41s / 3.13 2m34s），**告警数由 1 条降为 0 条**。仅改 CI，无产品/协议/数据变更。协议版本 3.23（不变） |
| **v3.38.1** | 2026-09-15 | **依赖下界收紧**：v3.38.0 发布时核查「声明的版本边界是否允许已知漏洞版本」，发现三处过松——① `requests>=2.31` 允许 2.32.5（`PYSEC-2026-2275`，`extract_zipped_paths()` 可预测临时文件名）→ `>=2.33`；② `pytest>=7.0` 允许 9.0.2（`PYSEC-2026-1845`，`/tmp/pytest-of-{user}` 可预测目录）→ `>=9.0.3`；③ `weekly` extra 补 `soupsieve>=2.8.4`——`soupsieve` 是 bs4 的解析引擎而 bs4 对它的下界（`>=1.6.1`）过松，**抬 bs4 挡不住**，2.8.3 命中两项 High 通告（选择器 ReDoS：300 字节即可挂起 3 秒以上；逗号列表内存放大：500 KB 输入约 244 MB 分配），是三处里唯一无法用抬直接依赖表达的。**刻意未收紧 `urllib3` / `idna`**：二者为 requests 传递依赖、我们未直接 import，为其声明下界等于向包元数据谎报「直接依赖」；全新解析实测已拿到 2.7.0 / 3.19（均已修复）。判据是「下界不撒谎」而非「实际是否被触发」，同 v3.37.3 对 setuptools 的处理。另修正审计方法：直接对共享环境跑 pip-audit 报出的 61 条混入了 conda / yt-dlp / 无关个人项目，不反映项目闭包。全量 3,484 通过。协议版本 3.23（不变） |
| **v3.38.0** | 2026-09-15 | **多模型对抗游戏「谁是卧底」**：新增 `games` 模块与 `iris undercover-game` 命令，把 `llm.json` 全部可用模型当玩家。关键设计是**玩家不知道自己是谁**——卧底与平民 prompt 逐字相同，只能从他人发言推断自己是否少数派。每轮顺序描述（起点顺延，后发言者可参考本轮前面全部公开发言）+ 并行投票；私有面（观察清单/身份自评）只进复盘档案、永不进他人 prompt（否则卧底要么自曝、要么必须对身份说谎，与「不可编造」硬约束冲突）。防「装死」：调用失败与格式不合规用不同占位符，后者留在比对块内，否则拒绝输出即可免疫投票。配套为 LLM 层新增 `generate_as()` / `generate_multimodal_as()`，按 `(role, model_id)` 精确定名调用——既有 `force_model` 按 `model` 字段字符串匹配，同名复用时会误中，且降级会把玩家悄悄换成别的模型；精确调用失败不降级直接抛错。新增 122 项测试，全量 3,484 通过。**协议版本 3.22→3.23（CLI 命令集 68→69）** |
| **v3.37.6** | 2026-09-13 | **修复人物页「周报时间线」断档**：174 个人物页仅 27 页引用过成员周报、2026-09 周报在人物页 0 引用。三层根因——① `_chunk_to_hit` 把 `score` 硬编码为 0.0（真实分只写在 `explanation` 字符串里），下游 `_boost_hits_for_answerability` 在 0 分基线上做加法并重排，把 BM25 相关性序**完全抹掉**、退化为按路径字母序；② `search()` 排序键 `(-score, relative_path)` 在同分时按路径升序兜底，而人名查询下同一人各份周报 BM25 分完全并列、周报路径字典序即时间升序 → `top_k` 永远取**最旧**的 N 份；③ 人名在周报**正文中出现 0 次**，词法检索在任何排序策略下都召回不到正文。修复：新增 `_doc_date_ord()`（解析文件名前缀日期，含月日校验）使同分按日期降序兜底且无日期者不插队；新增 `LocalRetriever.latest_documents()` 按文档身份定向取最新若干份的代表块（剔 frontmatter、取最长正文块），仅 `page_type == "person"` 生效；`WikiGenerator._collect_evidence()` 周报通道优先占证据槽。新增 21 项防回归测试。**已知限制**：`is_wiki_stale()` 只检查已在 `source_fingerprint` 里的文档，新增周报永不在指纹里，daily-start 路径仍会判「指纹新鲜」而跳过人物页，需 `wiki-update --title <姓名>` 生效。协议版本 3.22（不变） |
| **v3.37.5** | 2026-09-13 | **统一 `orphans()` 双路径语义**：`_GraphEngine.orphans()` 的 networkx 与纯 Python 回退分支此前语义不一致（networkx 分支额外要求「节点在图内」），同一份代码在装 / 不装 networkx 时结果不同。现统一为「`all_node_ids` 中零入链者，含完全无边的节点」——调用方 `WikiGraph.find_orphans()` 传的是全部页面 id，无边页面恰是最该被发现的孤立页。回退分支改用新增的 `_in_links` 集合判定，未触碰同时服务 `neighbors` / `bridges` / `degree_stats` 的 `_adjacency`。新增 `TestOrphansPathParity` 跨路径守卫 4 项。全量 3,338 通过，协议版本 3.22（不变） |
| **v3.37.4** | 2026-09-13 | **CI 门禁恢复**：修复 CI 自 v3.36.0 起持续失败的四类根因——① Python 3.11 兼容（`assistant/models.py` 改用 `typing_extensions.TypedDict`，pydantic 在 <3.12 拒收 `typing.TypedDict`，`MeetingState` 构建即抛 `PydanticUserError`）；② 运行依赖补全（`requests` / `typing_extensions` / `setuptools`）；③ `llm/benchmark.py` 4 处 mypy 类型错误；④ 测试依赖收口（`[dev]` 补入 `networkx` / `pyahocorasick` / `pytest-asyncio`，音频测试改注入桩模块）。另修 `setuptools` 下界 64→83（`PYSEC-2026-3447`），`llm.json.example` 追平模型矩阵并补 Anthropic 协议内联覆盖。CI 四 job 全绿（3.11/3.12/3.13 + macOS smoke）。协议版本 3.22（不变） |
| **v3.37.3** | 2026-09-11 | 修复模型 `max_tokens` 被调用方硬编码静默覆盖：`complex_input` Stage 2（图片/PDF/视频）的 `max_tokens=4096` 与 `feishu/image_analyzer` 的 `max_tokens=300` 会覆盖 `llm.json` 中的模型配置，导致输出被钉死且不报错（`claude-fable-5` 11 次多模态调用中 10 次输出恰为 4096）；移除四处硬编码并新增 4 个防回归测试，配置输出上限恢复生效。协议版本 3.22（不变） |
| **v3.37.2** | 2026-09-11 | ASR-corrector 启动信息增强：`_print_startup_banner` 新增 LLM 模型配置展示行（模型名 / temperature / max_tokens / timeout），明确「强制指定，跳过路由」状态。协议版本 3.22（不变） |
| **v3.37.1** | 2026-09-11 | ASR-corrector 强制指定模型：`_invoke_llm` 新增 `force_model="deepseek-flash"` 跳过路由直连官方模型；`temperature` / `max_tokens` 由硬编码提取为实例属性。协议版本 3.22（不变） |
| **v3.37.0** | 2026-09-11 | 模型矩阵升级：`base_model` 默认 → `claude-sonnet-5-zz`、`adv_model` 默认 → `claude-fable-5-zz`（均走 zz_tokenhub Anthropic 兼容接口）；规模 4 → 11 个模型（base 2→4、adv 2→7），新增 Qwen 3.8 / 3.7 / 3.6 系列与 GPT-5.6 Sol 末位兜底，全矩阵统一多模态；路由规则 8 → 12 条（新增周报提取走 adv、ASR 校正/误识别/热词走 base）。协议版本 3.22（不变） |
| **v3.36.0** | 2026-09-10 | Anthropic 多模态集成 + 三阶段工程优化合并发布：`generate_multimodal` 新增 Anthropic 分支（OpenAI `image_url` → Anthropic `image.source.base64` 格式转换），endpoint 修正 `/v1/messages`；飞书图片下载 SSRF 防护、Keychain 原生写入、PID/锁并发加固；CI 增加 AST 安全扫描、SPDX SBOM 与关键模块覆盖率门禁。全量 3,330 通过，协议版本 3.22、app 数据版本 3.7 均不变。 |
| **v3.35.0** | 2026-09-10 | 三阶段工程优化：飞书远程图片下载 HTTPS/公网 IP 固定连接 + MIME/20 MiB 校验防 SSRF；Keychain 原生写入避免密钥进入子进程参数；ProcessRegistry 加锁修复 PID 竞态、锁文件权限收紧；开启 mypy `check_untyped_defs`，CI 增加安全扫描与 SBOM。全量 3,326 通过，协议版本 3.22（不变） |
| **v3.34.3** | 2026-09-09 | Anthropic 多模态 API 集成：新增 `_call_anthropic_multimodal`（120 行）与格式转换，共享统一降级链/Token 统计/缓存/熔断器；新增 4 用例与配置指南。全量 3,321 通过，协议版本 3.22（不变） |
| **v3.34.2** | 2026-09-09 | P1/P2 优化：重构 3 个高复杂度函数（19/19/17 → 流水线架构），提取 12 个可复用辅助函数；新增 `docs/EXCEPTION_HANDLING.md` 异常处理最佳实践。全量 3,317 通过，协议版本 3.22（不变） |
| **v3.34.1** | 2026-09-09 | 知识图谱 LLM 关系提取修复：`max_tokens` 2000→8000 + 智能实体过滤（只传正文提及实体，≤100 个），解决 `finish_reason=length` 全量重建失败；提取 1,609 条 LLM 关系边。协议版本 3.22（不变） |
| **v3.34.0** | 2026-09-08 | 工程安全与质量发布：Trello 凭证不再暴露于进程参数，写入守卫取消已有文件绕过，日志敏感字段脱敏；统一原子写入，Docker 改为非 root CLI 镜像；CI 增加 wheel 构建、严格 mypy 与 macOS CLI smoke；新增发布检查清单。全量 3,317 通过，协议版本 3.22、app 数据版本 3.7 均不变。 |
| **v3.33.3** | 2026-09-08 | 例行发版，无功能变更；产品版本 3.33.2→3.33.3。协议版本 3.22（不变） |
| **v3.33.2** | 2026-09-08 | Trello `create_list` 参数顺序 bug 修复：签名 `(name, board_id)` → `(board_id, name)`，与调用方位置传参对齐，done 归档建列表 name/idBoard 互换致 400 的链路恢复。回归 +2 unit，全量 3,315 通过。协议版本 3.22（不变） |
| **v3.33.1** | 2026-09-07 | Trello 客户端网络加固：urllib 网络失败指数重试（≤2 次）→ curl 兜底传输、DNS 负缓存防 dig 反复阻塞、请求超时 30s→15s、网络层错误与 HTTP 错误分层（仅网络层可重试）。净 +5 unit，全量 3,313 通过。协议版本 3.22（不变） |
| **v3.33.0** | 2026-09-07 | 双周报 `build-biweekly-report` 成稿风格 w35 定稿：总结段「总览 + 每判断点短段」反固定骨架、关键进展每方向 2-4 条价值门槛、`strategic_insights` 抽取纳入会议纪要、low 素材隔离、Stage4 辅助段剔除等确定性后处理；新增 `--as-of` 历史周期复现。净 +11 unit，全量 3,308 通过。协议版本 3.22（不变） |
| **v3.32.1** | 2026-09-06 | 明文 API Key 检测误报修复：`.env` 探测改词段匹配（TOKENHUB_BASE_URL 渠道名不再被 `TOKEN` 子串误判）+ URL/keychain/`${VAR}` 值形态门禁；回归 +1，全量 3,297 通过。协议版本 3.22（不变） |
| **v3.32.0** | 2026-09-06 | 新增 `iris llm-bench`：LLM 通道/模型 连接速度（流式首可见 TTFT）+ 吞吐基准（字符口径，规避 tokenhub 中继 usage 虚高）；引擎 `llm/benchmark.py` + 纯函数单测 10 项，全量 3,296 通过。协议版本 3.21→3.22（命令集 67→68） |
| **v3.31.0** | 2026-09-03 | sync-memory 双向化：新增 Iris→CC 反向通道（Iris 运行期自主纠正 → CC 记忆文件 + MEMORY.md 物化，五级覆盖判定防重复）+ 前向收紧（reference/无标记 feedback 不再隐式灌 Iris 备注）+ daily-start 自动双向 + CLI `--forward-only`；`test_sync_memory.py` 至 90 项，全量 3,286 通过。协议版本 3.21（不变） |
| **v3.30.1** | 2026-09-03 | 主线合并 0902-alpha → main：并入 v3.30.0 三阶段质量优化（F401/C901 门禁、`IrisError` 统一异常体系、mypy 基线、corrector/live 拆分），补齐 v3.29.2/v3.29.3 修复；源码零冲突，全量 3,274 通过。协议版本 3.21（不变） |
| **v3.30.0** | 2026-09-03 | 三阶段质量优化：开源冲刺（F401 门禁 + 395 导入清理 + 测试数据脱敏）→ 代码质量（C901≤20 重构 11 函数、corrector/live 拆分、print→logger）→ 长期改进（`IrisError` 异常体系 19 挂接、mypy 基线 193、专项单测 +257）；覆盖率 68%。协议版本 3.21（不变） |
| **v3.29.3** | 2026-09-03 | deep-eval 准确性校验修复：引用解析（列表符号/行号范围/反引号）+ `lookup_relevant` 证据召回兜底 + 评估模板双花括号填充根因修复。协议版本 3.21（不变） |
| **v3.29.2** | 2026-09-02 | 提醒引擎停滞误判修复：数字点号变体还原（30→3.0）+ frontmatter title 词段补充 + 调薪完结项目入 ignore；停滞信号 6→1。协议版本 3.21（不变） |
| **v3.29.1** | 2026-09-02 | Wiki 增量更新指纹预检补全：`update_all_pages` 复用 `is_wiki_stale`，源文档未变零 LLM 跳过（241 页全跳过 ~5s），根治 daily-start 卡死。协议版本 3.21（不变） |
| **v3.29.0** | 2026-09-01 | 飞书消息图片理解沉淀：`MessageImageAnalyzer` 下载→多模态→描述 + 按 image_key 跨管道缓存；feed/chat-digest 双管道接入；`image_understanding` 配置。协议版本 3.21（不变） |
| **v3.28.5** | 2026-09-01 | LLM 调用统一到 LLMService：消除 7 处 `get_provider()` 绕过响应缓存的残留路径。协议版本 3.21（不变） |
| **v3.28.4** | 2026-09-01 | 提醒引擎停滞判定三重兜底（指纹→SOURCE 同名→内容级）+ `project_stall_ignore` + Wiki 链接示例占位符根因清零。协议版本 3.21（不变） |
| **v3.28.3** | 2026-09-01 | 开源前信息安全复审：全库二次脱敏（姓名/指标/内部项目名泛化）+ git 邮箱 noreply + CI 最小权限。协议版本 3.21（不变） |
| **v3.28.2** | 2026-09-01 | batch-transcribe 批量会议纪要修复：补全 `run_batch`（音视频分流/单文件容错/埋点）+ handler 补传 `--to-source`。协议版本 3.21（不变） |
| **v3.28.1** | 2026-08-30 | 双线修复：LLM 思考文本污染（content 空抛错，Stage4b 回退组装稿）+ 深度审查批次 1 数据止损（6 P0 + 4 P1，回归 +26）。协议版本 3.21（不变） |
| **v3.28.0** | 2026-08-26 | 工程可靠性治理：SQLite 生命周期 / 稳定 inode 锁 / 统一原子写 / 向量索引 generation 发布 / 跨进程 LLM 缓存治理。协议版本 3.21，app 3.6 |
| **v3.27.2** | 2026-08-24 | LLM 配置修复（`find_model_by_name` Pydantic 兼容 + adv 视觉模型新默认）+ iris-feishu-import 批量用法修正。协议版本 3.20（不变） |
| **v3.27.1** | 2026-08-17 | 双周报写作风格固化：Stage 3 总结段逐项目「目标→思考→决策→下一步」+ 关键进展项目级聚合。协议版本 3.20（不变） |
| **v3.27.0** | 2026-08-16 | 任务面板 `iris task-panel`：Web 只读 + 常驻守护（launchd）+ TaskReporter 埋点 + 探测兜底 + 首批 5 命令接入。协议版本 3.20 |
| **v3.26.2** | 2026-08-12 | meeting-live-assistant 面板双主题视觉方案：`_theme.py` 双套 ANSI 配色 + 语义色贯穿 + `assistant.panel_theme` 配置。协议版本 3.19（不变） |
| **v3.26.1** | 2026-08-12 | meeting-live-assistant 深度审查后全量优化：29 项四阶段（P0 可用性 / P1 体验 / P2 能力 / P3 工程）+ 3 项评估修复。协议版本 3.19（不变） |
| **v3.26.0** | 2026-08-12 | meeting-live-assistant 升级「实时 AI 会议参谋」：四层 12 项能力 + 说话人区分 + VAD 修复 + 降级链 deadline 治理。协议版本 3.19（不变） |
| **v3.24.2** | 2026-08-11 | asr-corrector 写回修正：full 模式单次输出（消除两次写回闪烁）+ 恢复逐字符 Delete 删除。协议版本 3.18（不变） |
| **v3.24.0** | 2026-08-11 | assistant × asr-corrector 全面优化：写回快照校验 / 反馈反向优化管线时序 / 替换词典交叉冲突防护 / 预取原子化 / 热键 Event 化 / LLM deadline。协议版本 3.18 |
| **v3.23.3** | 2026-08-10 | meeting-live-assistant 全量优化：双段流水线并行（关键路径 25s→15s）/ 短段门控 / 退出路径加固 / AI 会议总结 / 检索 deadline 根治。协议版本 3.17 |
| **v3.23.2** | 2026-08-10 | wiki-update 备份文件全链路过滤：`*.bak.1.md` 被 5 处计入统一按 `.bak.` 过滤。协议版本 3.16（不变） |
| **v3.23.1** | 2026-08-10 | 遗留修复 + 使用指南：asr-corrector Ctrl+C（Python 3.13 SIGINT）+ verify_hotkey_inject 入版本控制 + 会议助理使用指南。协议版本 3.16（不变） |
| **v3.23.0** | 2026-08-10 | 实时会议助理 `meeting-live-assistant`：按住热键逐段转写→ASR 校正→检索→LLM 分析→面板提示 + 过程文档；积压丢弃 + 运行时互斥。协议版本 3.16 |
| **v3.22.5** | 2026-08-10 | ASR 校正引擎热键门控修复：CGEventTap 失效降级（内容特征判定兜底）+ 监听窗口挂钩热键按住时长（1 分钟长语音不再超时跳过）。协议版本 3.15（不变） |
| **v3.22.4** | 2026-08-10 | 周报提取主题日期不一致自动标注：`_subject_date_mismatch_note` 检测主题日期 ≠ 发送日期（复制标题忘改日期），邮件信息栏自动加 ⚠️ 标注，+4 测试。协议版本 3.15（不变） |
| **v3.22.3** | 2026-08-07 | 知识库全面体检修复：索引死数据清除（chunker 全量重建误追加，chunk 10,290→5,939 / 覆盖率 100%）+ 图谱 LLM 边清零修复（恢复 591 条，增量不再丢）+ deep-eval CLI 参数补齐 + 回归测试 +4。协议版本 3.15（不变） |
| **v3.22.2** | 2026-08-04 | wikilink 注入残留清理：过时注释修正（chat_digest/extract_weekly_reports）+ wiki_root 死参数链删除（10 处）。协议版本 3.15（不变） |
| **v3.22.1** | 2026-08-03 | Wiki 发现噪音过滤（周报模板章节标题）+ 知识图谱全量重建边去重修复（LLM 边退化）。协议版本 3.15（不变） |
| **v3.22.0** | 2026-08-02 | 合并 0802-alpha：开源信息泄露治理全库脱敏 — 真实 open_id 配置化 / 团队名单与 dept_op_keyword 默认值清空（app.json 驱动，null 防御）/ 人名·邮箱·OKR·项目名·业务指标全库泛化 / 测试断言同步（61 文件）。协议版本 3.15（不变） |
| **v3.21.1** | 2026-08-02 | SOURCE 归档适配修复：双周报日期前缀 + 会议纪要/风格源递归查找 + feed 简报 `resolve_source_archive_path` + 死代码清理 + 5 Skill 路径更新（18 处）。协议版本 3.15（不变） |
| **v3.21.0** | 2026-08-02 | 批量 frontmatter 补全命令 `frontmatter-batch`（正则+LLM+wikilink+备份恢复，9 类目录字段映射）+ wikilink 注入收敛 + 周报按月归档 + 双周报 frontmatter 注入，+65 测试。协议版本 3.15 |
| **v3.20.1** | 2026-07-30 | deep_eval chunk 摘要路径配置化：`main_source` 硬编码改为 `default_source` 动态加载。协议版本 3.14（不变） |
| **v3.20.0** | 2026-07-30 | iris-feed 文档提取（Step 5）：飞书文档链接自动转换为本地 Markdown 并关联到简报，+37 测试。协议版本 3.14 |
| **v3.19.26** | 2026-07-29 | 检索与时效性四项优化：chunk 重叠 / Wiki source_fingerprint 指纹追踪 / 向量索引模型守卫 + --force-rebuild / 主动提醒引擎 reminders；修复 PDF 切块 0 chunk、hash 索引不更新、RRF 配置未生效。协议版本 3.13，+54 测试 |
| **v3.19.25** | 2026-07-28 | iris-feed 简报质量跃升：两阶段 LLM + 去截断 + Prompt 重写 + 结构化输出增强。合并 0728-beta：输入截断保护 + 死代码清理（7 文件/+725/-268） |
| **v3.19.24** | 2026-07-28 | 全量质量加固第二轮：P0 Dockerfile/CI/路径 + P1 feed +177/SecretStr/pre-commit + P2 logger.exception/导入统一/覆盖率合并（16 文件） |
| **v3.19.23** | 2026-07-28 | 全量代码质量加固：P0 修复 9 项 / ASR 存根删除 / 工具去重 / ConfigBundle 迁移 / feed 测试 +16 / deep_eval 并发化 / BM25 可配置 / 死代码清理（27 文件/+263/-225） |
| **v3.19.22** | 2026-07-27 | iris-feed OKR 语义匹配 + LLM deadline 实时超时控制 + ASR 独立熔断器（9 文件/199 行） |
| **v3.19.21** | 2026-07-27 | 信息汇聚管道 iris-feed：飞书聊天→话题检测→简报生成（11 文件/9 CLI/飞书 Bot 推送），协议版本 3.11→3.12 |
| **v3.19.20** | 2026-07-24 | ASR 反馈反向优化引擎：feedback.jsonl 驱动词典自动进化（僵尸规则/LLM发现提升/热词补充），build-asr-prompt 集成，+31 测试 |
| **v3.19.19** | 2026-07-23 | 测试覆盖全面优化：P1 _graph_engine 61→97% + asr/formatter 59→98% + navigation 67→70%，新增 3 测试文件 +90 用例（1,858→1,948），覆盖率 58.17→59% |
| **v3.19.18** | 2026-07-23 | 知识库质量全面加固 + 代码质量全面加固：wiki-pipeline 检测修复 / LLM 语义关系提取(986边) / 断链零出链双清零 / 向量索引构建 / 用量追踪 / DRY 消除 / pip-audit / lint 清零 |
| **v3.19.17** | 2026-07-22 | SOURCE 目录按月/年归档：9 目录 3 级策略（yearly/monthly/flat），自动生成年月子目录，740 文件搬迁 |
| **v3.19.16** | 2026-07-22 | 合并 0722-alpha：多 Agent 并发安全（FileLock 推广至 6 处 RMW + SQLite WAL + Agent 记忆隔离 + 进程注册表）+ 新增 iris-okr-check Skill |
| **v3.19.15** | 2026-07-22 | 多 Agent 并发安全三层防护体系（P0 FileLock 推广 + SQLite WAL / P1 缓存锁+Agent 隔离+TOCTOU / P2 进程注册表+JSONL 锁），3 轮审查 7 修复 |
| **v3.19.14** | 2026-07-22 | 记忆自动更新引擎：Phase 1 LLM 双通道提取 + Phase 2 会话模式挖掘 + Phase 3 全自治生命周期，29 新增测试 |
| **v3.19.13** | 2026-07-21 | ASR shutdown SIGINT 保护：清理流程统一信号屏蔽（finally 块），防止二次 Ctrl+C 中断 hotkey monitor 线程 join + executor 关闭 |
| **v3.19.12** | 2026-07-21 | ASR 引擎：LLM 思考模式关闭 + 路由路径 extra_body 修复 + 上下文 A/B 对比模式（`--context-ab`） |
| **v3.19.11** | 2026-07-21 | 五大方向优化：+76 测试 / LLM 统一网关(extra_body+use_cache) / MemoryCache 通用缓存 / God Class 拆解 / Wiki shim 废弃化（19 文件，+887/-64 行，1,829 测试） |
| **v3.19.10** | 2026-07-21 | ASR 引擎质量加固（P0~P3）：protected_terms 截断 / 热键校验 / 超时+参数化 / 预检查 / Aho-Corasick 优化 / worker 动态 / 重试（8 文件，+313/-114 行） |
| **v3.19.9** | 2026-07-20 | 双周报流水线质量加固（P0~P1）：Stage 1 全空兜底 / owner-map 注入 / 缓存校验 + Stage 3 子方向覆盖重构（全覆盖 / ≤3条/子方向 / ~50字）+ Stage 2/4b 增强 + brief 优先级排序 + 超时补跑（9 文件，+295 行） |
| **v3.19.8** | 2026-07-20 | 检测路径全面改进（P0~P2）：4 Bug 修复（死代码 / 正则贪婪截断 / 字符串表达式未赋值 / 缺失异常处理）+ 6 设计缺陷修复（代码正则补全 / 路径归一化双端一致 / 泛型类型修正 / 槽位效率去重 / RANGE_PATTERN search 化）+ 新建 test_text_detector 等 +79 测试（1,753，100 文件） |
| **v3.19.7** | 2026-07-19 | 全面质量加固（P0~P2 七项）：`_wiki.py` 静默异常补日志 / embedding 向量 LRU 缓存 / `corrector.py` 拆分（`_clipboard_io.py` + `_text_detector.py`）/ LLM `_CircuitBreaker` 熔断器 / +109 测试（1,674，99 文件） |
| **v3.19.6** | 2026-07-19 | ASR 校正引擎加固：max_mappings 990→2000 配置化 / 替换词典热加载 / 手动热词合并机制 |
| **v3.19.5** | 2026-07-19 | 全面质量加固：Stage 4 拆分 / `_TEAM_OKR_PATTERN` 配置化 / Stage 3 顺序后置校验 / ASR 动态推断示例 / 超时修复 / +30 测试（407 通过） |
| **v3.19.4** | 2026-07-19 | 双周报生成逻辑优化：方向标题精简化 / ≤4 条关键进展 / 来源按时间最新 / 严格 KR 顺序 + OP 文档选择修复 + 配置加载占位符误报修复 |
| \*\*v3.19.3\*\* | 2026-07-19 | 交互体验：build-asr-prompt 实时进度输出（ProgressTracker + Phase 2 逐批进度 + 耗时汇总） |
| \*\*v3.19.2\*\* | 2026-07-19 | ASR Phase 1 基础设施：反馈解析修复、模式枚举 API、daily-start ASR 审计 |\
| \*\*v3.19.1\*\* | 2026-07-19 | ASR 代码质量加固：JSONL 反馈格式统一、热词去重修正、死代码清理、剪贴板等待策略改进、常量复用 |\
| \*\*v3.19.0\*\* | 2026-07-19 | ASR 实时校正引擎：iris-asr-corrector 常驻守护进程，剪贴板监听 vocotype ASR 输出双重校正，一键部署，自动反馈闭环 |\
| \*\*v3.18.9\*\* | 2026-07-17 | 代码质量加固：内存系统并发安全（FileLock）、向量索引模型变更检测、`.env` 行尾注释剥离、Stage2 多模态 `max_tokens` 控制、lark-cli fallback、Wiki 证据阈值配置化 |
| **v3.18.8** | 2026-07-17 | 性能优化：PersonEnricher 飞书 API 频率限制修复（预先过滤已丰富页面 + 自适应批间延迟 + 批次大小调低） |
| **v3.18.7** | 2026-07-17 | 工程优化：CI/CD 基础设施（Makefile/CI/pre-commit/Dockerfile）+ 测试分层重组（unit/integration，1,467→1,513，覆盖率 60.42%）+ Wiki 模块重构（graph.py 751→215 行、ASR 子包物理隔离）；ruff F821/E741/F402 零错误 |
| **v3.18.6** | 2026-07-17 | 开源脱敏补充清理：内部标识符通用化 + 第三方服务引用脱敏 + CHANGELOG 反向泄露修复；测试 1,467 通过 |
| **v3.18.5** | 2026-07-17 | 新增 `iris-daily-start` Skill（每日启动维护一键触发）；Skill 8 个 |
| **v3.18.4** | 2026-07-17 | 代码质量优化：正确性修复（`_rrf_fuse` + 裸 except）+ 技术债清理 + graph.py 拆分（973→747 行）+ 测试 1,439→1,467（llm/service 97%，session 100%） |
| **v3.18.3** | 2026-07-17 | 全面测试补充：+216 用例（CLI 集成/纯函数/数据类），覆盖率 53%→60%；1,439 通过 |
| **v3.18.2** | 2026-07-16 | 文档版本同步 + 测试补充 + watcher 去抖修复；测试 1291 通过 54% |
| **v3.18.1** | 2026-07-16 | 全栈代码质量优化：P0 异常处理加固（15文件）+ P1 共享线程池/ConfigBundle Pydantic v2/deep_eval拆分 + P2 内存LRU缓存/BM25统计缓存 + P3 飞书退避优化/警告过滤；测试 1223→1291 | 
| **v3.18.0** | 2026-07-15 | 开源脱敏全面清理（方案B）：源码/测试/文档/模板敏感信息移除 + Git 历史全文重写（92 commits）+ CONTRIBUTING.md + SECURITY.md；测试 1223 通过 |
| **v3.17.1** | 2026-07-15 | SOURCE 新增「工作简报」栏目 + `--incremental` argparse 冲突修复 + 全维度 Wiki/索引/图谱评估与自动修复（断裂链接 -10、孤立节点 16→4、wikilink 边 +15、草稿发布）；测试 1223 通过 |
| **v3.17.0** | 2026-07-15 | 代码质量全面优化（P0-P3）：全局变量安全加固 + 异常精细化 + 模板加载统一 + ThreadPool 超时 + LRU 缓存驱逐 + 大文件拆分（service 1232→805行）+ graph LLM 缓存接入；测试 912→1223（+311），覆盖率阈值 50%→53% |
| **v3.16.0** | 2026-07-15 | 全栈优化 P0-P3：结构化日志 + async/await + 多工作空间 + 文件监听 + Prompt 外部化 + Wiki 引用校验 + LLM 缓存 + 增量 Chunk + NetworkX 图谱 + Config 迁移；测试 754→912（+158），覆盖率 49%→50.37%，CLI 48→51 |
| **v3.14.1** | 2026-07-15 | 代码质量全面优化：CLI handlers 拆分（1480→80+4 子模块）+ analysis 重构 + 测试 687→754 + pytest-cov 49% + except Exception 审查 + 文档完善 |
| **v3.14.0** | 2026-07-15 | 全面优化：测试 554→687 + 用量成本估算（usage-stats --cost + 价格表 + daily-start 概要/预算预警）+ 图谱查询命令 graph-query + VIDEO 多模态（ffmpeg 抽帧 + Whisper 转写）|
| **v3.13.0** | 2026-07-14 | LLM 用量统计（SQLite 记录调用/token，分模型 + 汇总，日/周/月/年聚合）+ usage-stats 命令；554 测试 |
| **v3.12.1** | 2026-07-14 | 图谱刷新单次 Wiki 扫描 + 持久 _out_edges 索引 + QA 图谱惰性缓存 + _parse_triples JSON 数组兼容 + pytest 工具配置；538 测试 |
| **v3.12.0** | 2026-07-14 | 知识图谱（节点+wikilink边+LLM关系提取）+ PDF/DOCX多模态 + 反向引用索引 + 代码审查14项修复；持续集成 532 测试 |
| **v3.11.17** | 2026-07-13 | .env→Keychain 密钥迁移 + secrets-list 修复 + 去重安全提醒修复；持续集成 461 测试 |
| **v3.11.16** | 2026-07-13 | P0 安全加固（Memory 原子写入 / LLMQueryPlanner 实现 / API Key 提醒）+ HTTP/Chunk 去重 + 测试 460 |
| **v3.11.15** | 2026-07-10 | Trello Python 3.13 SSL 兼容修复 + env 变量解析；持续集成 397 测试 |
| **v3.11.14** | 2026-07-10 | 新增 iris-process Skill（富媒体路由+三阶段流水线）；Stage 3 模板 bug 修复；iris-ask 职责边界清晰化，397 测试 |
| **v3.11.13** | 2026-07-10 | 开源脱敏清理：源码/测试/模板内部信息移除，`_SUB_AREA_KEYWORDS` 用户自定义，`.gitignore` 补全，397 测试 |
| **v3.11.12** | 2026-07-09 | extract-travel-invoice PDF 文字直接提取 + 转置表格输出；wiki-lint --fix 噪音链接正则修复（避免误删 frontmatter），397 测试 |
| **v3.11.11** | 2026-07-09 | extract-travel-invoice 代码审查 8 项修复，397 测试 |
| **v3.11.10** | 2026-07-09 | extract-weekly-reports 扫描漏人修复：folder list → 跨全文件夹 search + 白名单预筛 + 撤回/重复去重，命中人数大幅提升，384→397 测试 |
| **v3.11.9** | 2026-07-08 | 安全加固（开源准备）+ 工程质量 6 项 + 测试补全，315→384 测试 |
| **v3.11.8** | 2026-07-08 | build-asr-prompt 性能与质量优化：Phase 1/2 并发化 + Phase 3 校正策略强化，315 测试 |
| **v3.11.7** | 2026-07-07 | analysis/service.py 职责拆分：数据层/缓存层独立模块 + 38 测试用例，315 测试 |
| **v3.11.6** | 2026-07-07 | 全项目深度优化：19 项安全/bug/性能修复 + 8 测试文件 +58 用例，277 测试 |
| **v3.11.5** | 2026-07-07 | 代码审查优化：12 项修复（UTC 时区/preview 注入/OP 缓存/ModelManagerError），219 测试 |
| **v3.11.4** | 2026-07-07 | build-biweekly-report 流水线修复：多期去重 + 跨方向路由 + Stage 1 缓存 |
| **v3.11.3** | 2026-07-05 | build-biweekly-report 全面重构：文件级时间窗口 + 引用简化 |
| **v3.11.2** | 2026-07-03 | force_model 参数 + 纪要翻新 + 人物歧义处理，169 测试 |
| **v3.11.1** | 2026-07-03 | transcribe-meeting 修复：会议日期/时长/尾注，169 测试 |
| **v3.11.0** | 2026-07-03 | Claude Code Skill 体系：6 个项目级 Skill，169 测试 |
| **v3.10.2** | 2026-07-02 | feishu-doc-convert 改进：文件名使用飞书创建时间/作者，169 测试 |
| **v3.10.0** | 2026-07-01 | 全面代码优化 + 新模块引入（记忆 5 子模块 / 输出格式化 / 全局常量），169 测试 |
| **v3.9.0** | 2026-06-30 | 人物 Wiki 飞书通讯录丰富 + 人物发现规则增强，169 测试 |
| **v3.8.0** | 2026-06-29 | 复杂输入三阶段重构 + LLMService 统一入口 + 8 路由规则，161 测试 |
| **v3.7.0** | 2026-06-29 | iris2 迁移：Pydantic v2 配置校验 + Wiki 深度评估，161 测试 |
| **v3.6.0** | 2026-06-29 | 全面审查：6 Critical + 14 High 修复，5 项架构重构，138 测试 |
| v3.5.0 | 2026-06-29 | build-asr-prompt 三段 LLM Pipeline（热词 + 误识别 + Prompt） |
| v3.4.0 | 2026-06-27 | 代码审查修复（6 Critical Bug）+ BM25 重写 + 性能优化 |
| v3.3.0 | 2026-06 | 飞书 → 本地知识库提炼，步骤 3 完成 |
| v3.2.0 | 2026-06 | 步骤 2 完成，Wiki 体系上线 |
| v3.0.0 | 2026-05 | 项目初始化（从 Iris v2.7.1 重构） |
