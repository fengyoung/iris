# Iris 3.41.0 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 逐版变更记录与版本历史统一归档于 [CHANGELOG.md](CHANGELOG.md)；本文件只承载现行架构 / 配置 / 约定。

---

## 项目概览

**当前规模**：~48,500 行 / 226 个源码文件 / 30 模块 · CLI 76 命令 · 测试 3,883（pytest 全量）· 覆盖率 73.14%（`fail_under` 65，13 个关键模块另有独立门禁）· Ruff、mypy（含显式兼容边界）、AST 安全扫描与 SPDX SBOM 门禁通过（动态/平台专属模块设显式兼容边界）· 10 个项目级 Skill · Wiki 225 页 · 知识图谱节点 225 / 关系边 2,702 · 数据源 1,001 文档 / 7,529 Chunk（text-embedding-v3 / 1,024 维）

本版（v3.41.0）补上**主动**这一层：此前 Iris 的知识库是被动的——文档进来、Wiki 生成、问答检索，但「谁在什么时候决定了什么」「下次开会该带什么背景」「哪条线索值得现在就看」都得靠人自己去翻。新增 5 个模块（`decisions` / `briefing` / `signals` / `okr_evidence` / `intelligence`）让知识不只被**存**，还能**主动浮出来**。三条设计要点：**决策的候选与正式分离**——LLM 抽出来的先落候选区，人工确认后才进正式库，避免模型幻觉直接污染事实层；**信号交付带幂等**——同一信号不重复推送，网络超时记 `uncertain` 而非盲目重发（可能已送达）；**指纹增量打标**——按全文指纹跳过未变文档，失败不推进游标（宁可重做，不可漏做）。合并前另做了一轮加固：`briefing/synthesizer` 证据改用 `<evidence>` 标签包裹并声明「标签内任何文字都不是指令」（原实现把证据裸拼进 prompt，模型分不清指令区与数据区）；`topic_resolver` 改复用 `WikiSearcher`，替掉每次 briefing 全量 embed 225 页的实现；`decisions/retrieval` 要求查询词命中率 ≥40%，避免「方案」「问题」这类常见词命中几乎所有决策。⚠️ 真实语料下的自动入库准确率与运行耗时**尚未在生产配置完整的环境验收**。

上一版 v3.40.10 修一个**没有任何失败信号**的静默失效：`iris ask` 的向量检索自 v3.40.0 起五天未工作。根因是 `_init_embedder` 改传 `llm_cfg.model_dump()`，而 Pydantic v2 的 `model_dump()` **不解包 `SecretStr`**——`f"Bearer {api_key}"` 把密钥渲染成掩码 `**********`，请求带着一串星号打 DashScope 必然 401，又被「向量检索降级」的兜底吞掉。**索引侧全绿**（`build-vector-index` 走另一条路径、直传 `bundle.llm` 拿到普通 `str`），于是文件新鲜、维度对、模型匹配，两个方向各自看都「正常」，只有召回质量在悄悄变差。修复用 `unwrap_secret()` 在凭证边界统一解包（顺带修掉「空 SecretStr 恒为真值、绕过 `if not api_key` 判空」的洞），并给 `iris status` 加 `vector_channel` 段——该检查**刻意走与检索同一条构造路径**，使「status 报健康」等价于「检索真能用」，而不是复述配置文件；否则这类 bug 依旧测不出来。

上一版 v3.40.9 把「敏感文档只保留 SOURCE」这条**此前只存在于工作记忆、代码零强制**的规则变成结构保证。判定收敛为唯一真相源 `src/iris/wiki/_sensitive.py`，接入 7 个下游消费点（候选发现 / 页面生成 / 批量生成 / 增量更新 / **检索器** / ASR 热词与替换词典）。两条设计纪律值得记住：**判定只匹配文档身份（文件名/标题），绝不匹配正文**——正文级匹配会误伤「模型绩效」44 篇、「色卡校准」31 篇；**过滤必须 fail-closed**——清理时曾实现「按分句剥离、保住同句的正常信息」，结果漏出 `000 元至 47,000 元/月（涨幅 11.90%）` 残片（敏感分句内部本身含逗号，切开后碎片不再含标记词），已回退为整句删除。检索器那一处是唯一能同时堵住 Wiki 证据与 `iris-ask` 问答的点。另修 `asr-audit` 长期读错目录（读 `output/asr-modify/` 而构建只写 `output/`）——**该审计此前一直报告 7 月的陈旧值，泄漏检测实际是失效的**。详见 CHANGELOG。

上一版 v3.40.7/v3.40.8 修核心流程正确性并补行为测试：`deep_eval` 的判词解析抽为 `_parse_accuracy_verdict` 做精确标签匹配，修掉 `"consistent" in "inconsistent"` 为真、**把「不一致」静默判成「一致」**的 bug；双周报缓存键从「取前 2000 字」改为全文 + 完整方向定义 + 文件元数据，修长文截断导致的缓存碰撞；Wiki 无效更新被拒时不再把未吸收的新证据写进指纹冒充已吸收；人物页批量更新新增「页面生成后新增的 `-{姓名}.md` 周报」检测（仅人物页启用，避免一份新文档导致全库重跑 LLM）。另新增 `TaskBudget`（时间/调用/token/费用四维上限）与 3 个模块的 70% 覆盖率门禁。

上一版 v3.40.6 把「谁是卧底」的卧底获胜条件改回**多数即胜**（卧底 ≥ 平民即胜，人数打平当场收局），回滚 v3.40.4 的严格多数。真正的改动不只是翻一个比较符：这条规则此前写在**三处**（主循环判词、checkpoint 恢复分支、手动步进的 `continuing` 互补式），且提示词里另有**两处**规则文本都向模型陈述了可推断事实——现新增 `_judge_survivors()` 收敛为唯一判据来源，「游戏继续 ⟺ 判词为空」由注释纪律变成结构保证。已知代价：3 人局博弈纵深归零，恒为 1 轮。

v3.40.5 把「谁是卧底」的**断点恢复真正接通**：v3.40.1 建立的恢复通道此前只存轮号、且引擎从未消费 `checkpoint.json`，恢复出的对局会缺历史上下文。现改为保存完整轮次记录（公开描述、私有档案、投票及重投记录）并由引擎实际接入续跑，新增 `resume_version=1` 拒绝无法安全恢复的旧断点；另把「检查并发槽位」与「注册会话」收进同一 `games_lock` 临界区，消除双击恢复起两个线程的竞态。

更早的 v3.40.4 把「谁是卧底」的胜负条件改为**严格多数**（卧底人数 > 平民人数才获胜，打平继续），并按上一局复盘的票理由证据校准投票判据——**主因是平民大批出局不是因为暴露，而是因为说得少**：那局出局的 7 人中 6 名是平民、票理由几乎全是「未提及型」，而唯一出局的卧底死于明确互斥矛盾，说明旧判据在惩罚发言简省而非识别身份差异。另有一处必须同步改的隐性连动（手动步进的 `continuing` 判定，漏改则打平轮不发 `round_waiting`、界面不报错但步进被静默跳过）。

v3.40.3 修「谁是卧底」的三处**静默失效**（功能照跑、界面不报错，只是结果不对）：合并遗留的 DOM 悬空引用使网页端开不了局；总结模型未接线使总结从未生成；投票解析器的整段兜底按 key 长度挑目标，使投票理由与投票对象对不上。另按一局真实复盘的证据校准了游戏策略 prompt。

上一版 v3.40.2 修「谁是卧底」Web 端的上传失败：v3.40.0 引入的 2000 万像素上限把正常照片拒了（5489×3659 = 2008 万像素，仅超出 0.42%），上限放宽到 6400 万——判据回到「会不会解压炸弹」而非「照片够不够小」。三类失败拆开报（格式 / 尺寸 / 超像素），并修掉 `IrisValueError` 因多继承 `ValueError` 而被自身 `except` 改写、导致「图片容器无效」分支永不可达的错误信息失真。新增上传归一化 `downscale_for_upload()`：长边封顶 2048px（实测 qwen3.8-max-zz 的图像 token 在该尺寸触顶，而 deepseek-flash-zz 本就由服务端归一化——**按模型而异**），只缩不放，每局图片上行 180MB → 24MB。实现中踩到 `page.rect ≠ 像素尺寸`（fitz 按 96 DPI 折算），已加回归测试。该模块此前零直接测试，现补 32 项。

上一版 v3.40.1 合并 `0916-beta`，为「谁是卧底」Web 补状态恢复与观战交互：新增对局状态查询与断点恢复接口（`checkpoint.json` 重建 `GameSession` 续跑）、配置默认值与未完成对局列表接口；手动步进加相位守卫（仅 `waiting` 态可推进，重复/过期请求返回 409）。合并冲突在 `web_server.py` / `undercover_web.html` 两处，取并集而非二选一——核验手法是比较「worktree 相对每一侧删了哪些行」+ 函数集合包含关系，由此发现并修掉融合中丢失的 `playerRowHtml` 定义（调用点在、定义没了）。另统一两侧各自引入的玩家编号约定，并修 `bb28f1e` 遗留的 4 个既有失败用例。

上一版 v3.40.0 为系统治理审查发现的 P0～P2 风险。游戏 Web 封闭上传与复盘路径越界、符号链接和跨站访问，限制请求体、图片像素、上传空间、连接及对局并发；SSE 改为有界广播日志，支持多观察者和断线续传，游戏引擎加入协作取消并保存部分复盘。数据链路方面，增量 chunk 改用完整源快照，chunk 与向量发布增加乐观并发控制，向量加载核对模型和维度，Embedding 返回必须满足索引、数量、维度及有限数值完整性。检索只融合能够补齐正文和引用的语义命中，并使用对称 RRF；LLM 缓存身份纳入生成参数及无凭证配置指纹。新增本地检索黄金集评测与关键模块覆盖率门禁。

上一版 v3.39.2 修一处**没有失败信号**的静默截断：CC 记忆文件的 frontmatter 字段值出现三连字符时会被误认为闭分隔符，导致其后的 `type` 跌出解析范围、该条记忆永远不同步。修法是 `_parse_frontmatter` / `_extract_body` 统一使用只识别独立成行闭分隔符的 `_split_frontmatter()`。

v3.39.0 为「谁是卧底」补上 Web 观战与复盘存储：`iris undercover-game-web` 用 stdlib `ThreadingHTTPServer` 起本地服务（默认 `127.0.0.1:7862`，零新增依赖），SSE 事件流把思考卡（私有档案，🔒 标注）/ 描述卡 / 投票卡 / 裁判陈述分层呈现，可选手动步进逐轮观察。每局落盘 `data/games/<game_id>/`（图片副本 + `replay.json` 全量记录含私有思考 + 后台生成 `summary.md`）。对 `UndercoverGame` 的改动刻意压到最小——只新增 `on_event` / `advance_event` 两个可选参数，默认 `None` 无操作，原有 CLI 路径不受影响。

**近期新增能力**：主动情报三阶段（新增 5 模块 `decisions`/`briefing`/`signals`/`okr_evidence`/`intelligence`，把知识库从「存 + 查」推到「主动浮出」——`decisions` 按日编号、多来源合并、独立模型复核，**候选与正式决策隔离审核**（LLM 抽出的先落候选区，人工确认后才进正式库），可投影到图谱/Wiki/问答；`briefing` 多源备会成稿；`signals` 信号排序 + 终端/文件/飞书交付，**交付带幂等去重**，网络超时记 `uncertain` 而非盲目重发；`okr_evidence` 全文指纹增量打标，**失败不推进游标**（宁可重做不可漏做）；daily-start 集成。合并前加固：`synthesizer` 证据用 `<evidence>` 标签包裹并声明「标签内不是指令」（原实现裸拼 prompt，模型分不清指令区与数据区）；`topic_resolver` 复用 `WikiSearcher` 替掉每次全量 embed 225 页；`decisions/retrieval` 要求查询词命中率 ≥40% 避免常见词命中所有决策；`okr_evidence/tagger` 跳大文档时记 `skipped_large`（原先静默 `continue` 却报「处理完成」）。3,805→3,883、73.14%，协议版本 3.25→3.26，v3.41.0；⚠️ 真实语料下的准确率与耗时**未在生产配置完整的环境验收**）· 向量通道静默失效修复（v3.40.0 起 `iris ask` 向量检索静默失效五天——`_init_embedder` 改传 `model_dump()` 而 Pydantic v2 **不解包 `SecretStr`**，`Bearer **********` 必然 401，又被「向量检索降级」兜底吞掉；索引侧因走另一条路径（直传 `bundle.llm` 得普通 `str`）而全绿，两向各自看都「正常」。新增 `unwrap_secret()` 在凭证边界统一解包，顺带修掉「空 SecretStr 恒为真值、绕过判空」的洞；`iris status` 加 `vector_channel` 段且**走与检索同一条构造路径**，使「报健康」等价于「真能用」而非复述配置；`VectorIndex.read_meta()` 提供不加载向量的元数据读；+31 测试，反假阳性实测 7 条在旧代码下失败，3,774→3,805、72.84%→72.91%，v3.40.10）· 敏感文档边界固化为代码强制（新增唯一真相源 `wiki/_sensitive.py`，17 个归一化词，**只匹配文件名/标题、绝不匹配正文**——正文级会误伤「模型绩效」44 篇、「色卡校准」31 篇；裸词「盘点/定级/校准/落位」写入禁用清单只能组合使用；接入 7 个消费点，其中**检索器 `_drop_sensitive_chunks` 是唯一能同时堵住 Wiki 证据与 `iris-ask` 的点**，且在 BM25 统计前过滤使敏感文档不参与 idf；批量生成逐项降级 `refused_sensitive` 不中断整批；增量更新返回 `refused_sensitive` 而非 `error`——策略拒绝不是错误；**ASR 需两处内存过滤**，`--deploy` 读内存列表而非落盘文件，且 `_apply_asr_feedback` 会追加热词绕过 Phase 1；存量清理用 `scripts/purge_sensitive_wiki.py` 外科式删句不重建页面，**必须 fail-closed 整句删除**——分句级剥离曾漏出「000 元至 47,000 元/月（涨幅 11.90%）」残片已回退；同时清理 80 个 Wiki 页与现网 9 个敏感热词，3,687→3,774、覆盖率 72.78%→72.84%，v3.40.9）· 记忆冲突人工确认（纠正记录新增 `confirmed` 字段 + `memory-confirm` 命令，`detect_conflicts` 跳过已确认项；根因是判据纯按 `update_count >= min_count*2` 触发，而高计数可能来自 `last_source` 为「合并自: ...」的多概念合并而非反复纠正，v3.40.9）· `asr-audit` 目录修复（曾读 `output/asr-modify/` 而 `build-asr-prompt` 只写 `output/`，两份实现从未合并，致审计长期报告 7 月陈旧值、**泄漏检测失效**；修复后数字与直接复算吻合，v3.40.9）· 核心流程正确性与行为测试（`deep_eval` 判词解析抽 `_parse_accuracy_verdict` 精确匹配，修 `"consistent" in "inconsistent"` 为真致**「不一致」被静默判成「一致」**；双周报缓存键从「前 2000 字」改为全文+完整方向定义+文件元数据修碰撞；Wiki 无效更新被拒时不再把未吸收证据写进指纹；人物页新增「生成后新增周报」检测仅对人物页启用；新增 `TaskBudget` 四维上限与 3 模块 70% 覆盖率门禁，3,627→3,687，v3.40.7/3.40.8）· 谁是卧底获胜条件改回多数（卧底 ≥ 平民即胜、打平当场收局，回滚 v3.40.4 的严格多数；判词收敛为 `_judge_survivors()` 单点来源，消掉主循环/断点恢复/`continuing` 三份手写副本，「继续 ⟺ 判词为空」由纪律变结构；`continuing` 同步反向 `<`，方向反了会**多发**一次 `round_waiting` 让玩家为已结束的对局点步进；提示词描述侧与投票侧一并改「少于」，并补上投票侧此前全无的规则文本覆盖；已知代价 3 人局恒 1 轮、有纵深的最小人数是 4；关键用例均实测在旧代码下失败，3,621→3,627，v3.40.6）· 谁是卧底断点恢复补完（断点保存完整 `RoundRecord` 并**真正被引擎消费**——此前只存轮号、引擎从未接入，恢复出的对局缺历史上下文；新增 `resume_version=1` 拒旧断点与非法历史；恢复检查与注册同处 `games_lock` 消除双击起双线程的竞态，失败不消耗并发槽位、线程启动失败回滚；`auto_advance` 随对局持久化保留手动模式；轮次来源去重为只认事件 payload；页面恢复不再重置存活、种子统一 `Number`；全量 3,609→3,621、覆盖率 69.63%→70.00%，v3.40.5）· 谁是卧底胜负条件与投票判据校准（胜负判定 `spy_alive >= civ_alive` → `>`，打平继续、下一票定胜负；**必须同步改**手动步进的 `continuing`（`<`→`<=`），否则打平轮不发 `round_waiting`、Web 步进被静默跳过，已补唯一守卫测试；投票判据从「未提及型」改为**证据两级**——强证据=对方写下的元素与你亲眼所见不能同时成立，弱证据=未提及型须过三关才可用，并补「没有提到 ≠ 没有看到」「自证不是复读」「弃权的代价」「投中过卧底不等于可信」「否认型埋钩不构成质疑理由」；顺带修掉一条**假阳性回归钉**，3,598→3,609，v3.40.4）· 谁是卧底静默失效修复与策略校准（合并遗留的 `judge-role` 悬空引用致 `startGame` 抛错、网页端开不了局；总结未接线致从未生成，现一律挂裁判模型且取消局也出总结；`_parse_vote` 整段兜底按 key 长度挑目标 → 改为目标行内解析、多候选宁可弃权；两套玩家编号统一为 `N号=key`；裁判默认改官方 deepseek-flash 并与玩家互斥、全选/全取消、配置存 localStorage；客户端断开静默；策略层补「互斥属性冲突」判据、卧底看到真实差异的出路、禁止整段复述、首发提示改为给锚点，v3.40.3）· 谁是卧底上传边界修复与图片归一化（像素上限 2000 万→6400 万，判据回到「会不会解压炸弹」；三类失败拆开报；修 `IrisValueError` 被自身 `except` 改写致分支不可达；`downscale_for_upload()` 长边封顶 2048px 只缩不放，每局图片上行 180MB→24MB；`page.rect ≠ 像素尺寸` 回归；该模块首个直接测试 32 项，v3.40.2）· 谁是卧底 Web 状态恢复与观战交互（对局状态查询 `/api/game/<id>` + `checkpoint.json` 断点恢复 + 手动步进相位守卫 + 历史检索与总结轮询；合并 `0916-beta` 时冲突取并集，修融合中丢失的 `playerRowHtml` 定义、统一两侧玩家编号约定，并修 `bb28f1e` 遗留的 4 个既有失败用例；新增浏览器回归 `tests/browser/`，v3.40.1）· 安全/一致性/检索可信度系统加固（本地 Web 路径与资源边界、增量与向量乐观并发、Embedding 完整性、对称 RRF、缓存参数指纹、协作取消、SSE 广播续传、检索黄金集评测，v3.40.0）· sync-memory frontmatter 定界加固（`_FRONTMATTER_RE` + `_split_frontmatter()` 只认独立成行的闭分隔符，修字段值含三连字符致 frontmatter 静默截断、该条记忆永远不同步；顺带修 `---xyz` 与缩进三连字符两处误判、清理 6 个死代码常量，8 项防回归测试，v3.39.2）· SBOM 产品版本改读源码树（`read_product_version()` 从 `pyproject.toml` 取版本，修 editable 元数据冻结致 SBOM 静默错报；读不到版本返回 1 且不写文件，10 项测试，v3.39.1）· 谁是卧底 Web 界面（`undercover-game-web` stdlib 起服 + SSE 实时观战分层 + 手动步进 + 复盘持久化与 LLM 总结；`UndercoverGame` 仅加 `on_event`/`advance_event` 两个默认 None 的可选参数，v3.39.0）· CI workflow action 升至 v7（`checkout`/`setup-python`/`upload-artifact`/`codecov-action`，消除 Node 20 废弃告警；其中 codecov 的 `file`→`files` 改名是静默失效项，v3.38.2）· 依赖下界收紧（`requests>=2.33` / `pytest>=9.0.3` / `weekly` extra 补 `soupsieve>=2.8.4`；判据「下界不撒谎」，v3.38.1）· 多模型对抗游戏 `games/`（「谁是卧底」：玩家不知自身身份 + 顺序描述/并行投票 + 公私两面分层 + 防装死占位符区分 + `stalemate` 诚实记账；配套 `ModelManager.get_model_config()` 与 `generate_as()` / `generate_multimodal_as()` 精确模型调用，122 项测试，v3.38.0）· 人物页「周报时间线」修复（`_doc_date_ord` 同分日期降序兜底 + `latest_documents()` 文档定向取块 + `WikiGenerator._collect_evidence()` 周报通道优先占槽；21 项防回归测试，v3.37.6）· `orphans()` 双路径语义统一（新增 `_in_links` 入链集合 + `TestOrphansPathParity` 跨路径守卫，v3.37.5）· CI 门禁恢复（Python 3.11 兼容 + 依赖补全 + 测试收口；`setuptools` 下界 64→83 修 `PYSEC-2026-3447`；`llm.json.example` 追平模型矩阵并补 Anthropic 协议内联覆盖，v3.37.4）· 模型 max_tokens 硬编码修复（移除 `complex_input` Stage 2 的 `4096` 与 `feishu/image_analyzer` 的 `300`，配置输出上限恢复生效；附 4 个防回归测试，v3.37.3）· ASR-corrector 启动信息增强（`_print_startup_banner` 新增模型配置展示行，明确「强制指定，跳过路由」状态，v3.37.2）· ASR-corrector 强制指定模型（`_invoke_llm` 新增 `force_model="deepseek-flash"`，跳过 `asr_correction_go_base` 路由规则直连官方模型；`temperature`/`max_tokens` 提取为实例属性，v3.37.1）· 模型矩阵升级（`base_model` 默认 `claude-sonnet-5-zz` / `adv_model` 默认 `claude-fable-5-zz`，新增 Qwen 3.8/3.7/3.6 降级链与 GPT-5.6 Sol 兜底，全矩阵多模态，v3.37.0）· 路由规则扩充（周报提取走 adv，ASR 校正/误识别/热词走 base，v3.37.0）· Anthropic 多模态 API 集成（generate_multimodal 新增 Anthropic 分支，OpenAI→Anthropic 格式自动转换，v3.36.0）· 三阶段工程优化（SSRF 防护 + Keychain 原生写入 + CI 安全/供应链门禁，v3.35.0）· P1/P2 优化（复杂度重构 + 异常处理文档化，v3.34.2）· 知识图谱 LLM 关系提取修复（智能实体过滤 + max_tokens 8000，v3.34.1）· 三阶段质量优化（F401/C901 门禁、`IrisError` 统一异常体系、mypy 基线、corrector/live 模块拆分）· 工程可靠性治理（SQLite 生命周期、稳定 inode 文件锁、统一原子写、向量索引 generation 发布、跨进程 LLM 缓存治理）· 任务面板 `taskpanel/`（Web 只读 + TaskReporter 埋点 + 探测兜底 + 常驻守护）· 实时会议助理 `assistant/`（逐段提炼要点/风险/决策点 + 实时提示提问 + 过程文档）· YAML frontmatter 标准化注入（`core/frontmatter.py`）+ 批量补全（`frontmatter_batch.py`，正则+LLM+备份恢复）· wikilink 自动注入引擎（零 LLM 成本）· LLM 用量追踪（SQLite WAL + embedding 纳入）· LLM 响应缓存 + embedding 向量缓存（LRU+TTL）· LLM 熔断器（threshold=5 / reset 60s）· 记忆自动更新引擎（双通道）· 多 Agent 并发安全（FileLock + SQLite WAL + Agent 隔离）· ASR 实时校正引擎（Aho-Corasick + LLM 编辑助手 + 反馈反向优化）· CI/CD（Makefile / pre-commit / GitHub Actions）+ pip-audit · constraints.txt 可复现构建 · sync-memory 双向化（CC↔Iris 记忆互通 + 前向备注噪音治理，daily-start 自动双向，见 `scripts/sync_memory.py`）· `llm-bench`（LLM 通道/模型 连接速度 TTFT + 吞吐基准，字符口径规避中继 usage 虚高，引擎 `llm/benchmark.py`）· 双周报成稿 w35 定稿（总结段「总览 + 每判断点短段」反骨架、关键进展每方向 2-4 条价值门槛、`strategic_insights` 抽取纳入会议纪要、`--as-of` 历史周期复现）· Trello 客户端网络加固（urllib 网络失败指数重试 → curl 兜底传输 + DNS 负缓存 + `create_list` 参数顺序修复）

**关键路径**：

```
Obsidian 仓库：.../WORKSPACE/WIKI-ROOT/
├── SOURCE/   ← 数据源（9 类：01-目标管理 02-部门管理 03-方案报告 04-讨论思考
│                05-会议纪要 06-我的周报 07-成员周报 08-参考资料 09-工作简报；v2-data/ 软链）
└── LLM-WIKI/ ← Wiki 输出（01-领域 02-概念 03-项目 04-人物；index.md changelog.md）
```

路径通过 `.env` 中的 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置。

---

## 核心架构

### Wiki 体系

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化交叉链接 Markdown Wiki。

| 类型 | 目录 | 前缀 | 当前数量 |
|------|------|------|:---:|
| 领域 (domain) | `01-领域/` | `领域-` | 14 |
| 概念 (concept) | `02-概念/` | `概念-` | 11 |
| 项目 (project) | `03-项目/` | `项目-` | 21 |
| 人物 (person) | `04-人物/` | `人物-` | 400+ |

命令：`discover-wiki`（发现候选，4 类型分层排序）· `build-wiki`（单页/批量/审核）· `build-wiki-nav`（index.md）· `wiki-pipeline`（发现→审核→生成）· `wiki-lint [--fix]`（6 维检查/修复）· `wiki-update`（增量，daily-start 集成）· `enrich-persons`（通讯录补人物部门/邮箱）· `deep-eval`（引用准确性+全面性）· `build-asr-prompt`（热词→误识别→策略三段）。

### 飞书集成

`feishu-doc-convert`（飞书文档→本地 Markdown + 路由归档 + 排重）· `chat-digest`（聊天记录 AI 提炼为结构化文档）。

### 会议纪要路由

`transcribe-meeting --to-source` 自动判定归档目录：`05-会议纪要/`（多人 ≥3 正式会议）· `04-讨论思考/`（1对1/双人讨论，首要信号）· `03-方案报告/`（正式方案或技术结论）· `08-参考资料/`（外部学习资料）。路由规则存于 `config/meeting_routes.json`（gitignored），代码零硬编码。

### 记忆系统（6 子模块）

`long_term.py`（画像+概念纠正，写时自动压缩）· `session.py`（会话记忆）· `working.py`（工作上下文 Markdown）· `lifecycle.py`（老化/冲突检测/合并，默认自动老化）· `session_miner.py`（跨会话模式挖掘，自动晋升长期记忆）· `manager.py`（浏览/删除/导入/导出编排）。

**记忆自动更新引擎**（v3.19.14）：双通道架构（`MemoryUpdater` 正则快通道 + LLM 深通道）· 会话挖掘懒触发（Q&A 后 ≥24h + daily-start 兜底）· 自治生命周期（老化自动执行，纠正 ≥5 次自动确认，写入超标当场压缩）· 触发机制（Q&A 实时 → daily-start 每日 → 写时检查）。

### 知识图谱

三层架构叠加于 Wiki 体系：① 节点（frontmatter 全量构建，零 LLM）→ ② 反向引用边（`[[wikilink]]` → `linked_to`，零 LLM）→ ③ LLM 关系边（负责/使用/属于/…，批量增量）。

CLI：`build-graph [--full] [--page <title>]` · `graph-query --op <neighbors|related|path|orphans|bridges|density>`；集成 `daily-start` 自动维护链。

### 复杂输入三阶段流水线

```
Stage 1 (base model) → 动态生成多模态分析指令
Stage 2 (adv model)  → 图片/PDF/DOCX/VIDEO 多模态理解
Stage 3 (base model) → 整合润色输出
```

PDF=PyMuPDF 提取文字 + 逐页渲染；DOCX=python-docx 段落+表格文字；VIDEO=ffmpeg 均匀抽帧 + Whisper 音轨转写（依赖缺失优雅降级）。

---

## 配置体系

优先级：**OS 环境变量 > `.env` > macOS Keychain**。分层：`.env`（gitignored）· `config/*.json`（gitignored）· `config/*.json.example`（版本控制）· `data/`（全 gitignore）。

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证 |
| `IRIS_AGENT_ID` | 多 Agent 隔离标识（可选，默认 "default"） |
| `IRIS_PROJECT_ROOT` | 从仓库外启动时显式指定 Iris 项目根目录 |

---

## 版本体系（三层解耦）

| 层 | 位置 | 当前值 | 含义 |
|------|------|:---:|------|
| **产品版本** | `pyproject.toml` | 3.41.0 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.26 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | app 3.8（其余独立演进） | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

Python 3.11+ · LLM API 双协议（OpenAI 兼容 / Anthropic Messages，经 zz_tokenhub 中转与百炼、DeepSeek 官方直连）· Pydantic v2（配置校验）· lark-cli（飞书接口层）· PyMuPDF / python-docx（文档处理）· macOS Keychain（可选密钥存储）。

---

## 开发约定

- **长任务埋点规则（v3.27.0 起）**：新增长任务/常驻命令（分钟级以上）必须评估接入 `taskpanel.TaskReporter` 埋点——启动注册、关键阶段 `report_phase()`、结束写终态；不接需说明理由（如探测兜底即可）。
- **持久化规则（v3.28.0 起）**：共享状态读-改-写必须在 `FileLock` 临界区内完成，`.lock` 释放后必须保留；单文件 `atomic_write_text/bytes/json`，多文件制品 generation 目录写全后原子切换指针。
- **资源生命周期规则（v3.28.0 起）**：SQLite 等持久资源必须显式 `close()` 或使用上下文管理器；不得依赖垃圾回收释放文件描述符。
- **异常规则（v3.30.0 起）**：新增自定义异常必须继承 `iris.core.exceptions` 的 `IrisRuntimeError`（外部依赖/运行期失败）或 `IrisValueError`（输入/配置不合法）；需要额外标准库父类时用多继承 `class X(IrisError, PermissionError)`。调用方捕获优先 `except IrisError`。
- **异常处理边界规则（v3.34.2 起）**：**关键路径**（配置加载、索引构建、文件处理）必须使用具体异常类型（`json.JSONDecodeError`、`OSError` 等），便于诊断；**辅助功能**（用量统计、缓存、可选依赖）失败不影响主流程，可使用 `except Exception` 静默；降级策略需日志告知用户；静默失败需注释说明原因。详见 `docs/EXCEPTION_HANDLING.md`。
- **复杂度规则（v3.30.0 起）**：ruff C901 门禁 `max-complexity = 20`，新增/修改函数超限 CI 直接失败；拆分手法优先「分阶段私有方法」「表驱动分派」「状态对象」，不要靠 `# noqa: C901` 绕过。
- **导入规则（v3.30.0 起）**：F401 已启用；仅包 `__init__.py` 与 `app/cli/handlers.py`（facade）允许 re-export 未使用导入；其余模块需要保留给外部导入的符号必须显式 `__all__` 或在真正的定义处导入。`iris.core.exceptions` 零依赖，底层模块（config/utils）只从它导入，不得 `from iris.core import ...`（会经 `core/__init__` 触发循环导入）。
- **敏感文档规则（v3.40.7 起）**：调薪方案、人员盘点/评估过程记录、绩效评价、Leader 盘点类文档**只保留 SOURCE 原件**，不得进入任何下游衍生制品（Wiki 页面、检索结果、ASR 热词与替换词典）。判定唯一真相源为 `src/iris/wiki/_sensitive.py`（零依赖，仅匹配文件名/标题，**绝不匹配正文**——裸词「盘点」「定级」「校准」误伤率高，只能以组合词使用）。新增下游消费者必须接入该模块的谓词；修改词表须同步更新 `tests/unit/test_wiki_sensitive.py` 的真实语料反例。存量清理用 `scripts/purge_sensitive_wiki.py`（默认 dry-run）。

---

## 项目结构

```
iris3/
├── src/iris/          # 30 模块（见下）
├── scripts/           # CLI 入口 + 委托脚本
├── templates/         # Prompt / Wiki 模板
├── tests/             # 3,883 用例（pytest 全量，conftest 自动打标记）
├── config/            # *.json gitignored，*.example 版本控制
├── data/              # 运行时数据（全 gitignore）
├── .claude/skills/    # 项目级 Skill（10 个）
├── .github/workflows/ # CI 流水线（Python 3.11-3.13）
├── memory/            # Claude 工作记忆
└── Makefile · pyproject.toml · README · CLAUDE · CHANGELOG.md
```

**src/iris 模块**：`config`（加载+Pydantic 校验）· `llm`（Provider/路由/LLMService/用量统计/`benchmark.py` 连接吞吐基准）· `core`（类型/锁/写保护/存储/Agent 适配/共享线程池/`exceptions.py` 统一异常基类）· `memory`（记忆 6 子模块：含 `session_miner.py`）· `qa`（检索问答+图谱注入+`memory_updater.py` 双通道记忆提取）· `ingest`（扫描/切块）· `retrieval`（BM25+向量+RRF+BM25缓存）· `wiki`（Wiki 体系 + backlink/graph + ASR 校正引擎，最大模块；`wiki/asr/` 含 corrector/_hotkey/_trie/_diff/_clipboard_io/_text_detector/coverage/feedback/prompt_optimizer 等 14 子模块）· `feed`（飞书聊天记录→话题检测→简报生成，11 文件 / 9 命令）· `analysis`（报告/思维导图）· `evaluation`（Wiki 深度评估 + 引用解析）· `complex_input`（多模态三阶段）· `output`（格式化+DOCX）· `assistant`（实时 AI 会议参谋：本地 ASR+校正+检索+批量分析+话题/说话人+洞察推送+面板/文档，14 文件；`live.py` 编排 + `_audio_capture.py` 合并缓冲 + `_batch_processor.py` 批处理纯逻辑）· `games`（多模型对抗游戏「谁是卧底」：`undercover.py` 编排 + 顺序描述/并行投票 + 精确模型调用 + `web_server.py` SSE 观战 / `replay_store.py` 复盘存储，2 命令）· **`decisions`**（决策库：`extractor.py` LLM 抽取 + `schema.py` 校验 + `store.py` 候选/正式隔离 + `graph.py` 图谱投影 + `wiki.py` 页面导出 + `retrieval.py` 问答接入，1 命令组）· **`briefing`**（会前情报包：`topic_resolver.py` 主题解析 + `doc_retriever.py` / `person_retriever.py` 多源召回 + `open_issue_detector.py` 待办识别 + `synthesizer.py` 成稿）· **`signals`**（主动信号：`detector.py` 检测 + `ranker.py` 排序 + `delivery.py` 终端/文件/飞书交付含幂等去重 + `feed_cache.py`）· **`okr_evidence`**（OKR 证据：`tagger.py` 全文指纹增量打标 + `kr_vector_index.py` KR 向量 + `service.py` / `store.py`）· **`intelligence`**（共享底座：`context.py` 文档读取与本地时间边界 + `storage.py` 快照）· `app/cli`（76 命令）· `app/transcribe_meeting`（会议转录）· `feishu`（文档/聊天提炼）· `utils`（paths.py / shared.py）· `trello`（看板）· `taskpanel`（任务埋点 + Web 只读展示 + 常驻守护，7 文件）。

---

## Claude Code Skill（10 个项目级）

`iris-daily-start`（每日启动维护）· `iris-wiki`（发现→审核→生成）· `iris-feishu-import`（飞书文档/聊天导入）· `iris-feed`（群聊话题检测→简报）· `iris-meeting`（转写→纪要→归档）· `iris-ask`（问答）· `iris-process`（图片/PDF/DOCX/视频）· `iris-report`（分析报告/思维导图/双周报）· `iris-health`（质量巡检）· `iris-okr-check`（OKR 双周逐项检查）。
