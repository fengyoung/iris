# 多模型对抗游戏「谁是卧底」方案设计

> 模块：`src/iris/games/`（`undercover.py`）· 命令：`iris undercover-game` · 引入版本：v3.38.0

---

## 1. 背景与目标

`llm.json` 里配置了 11 个模型（base + adv，全矩阵多模态），但日常只有少量模型承担实际任务，其余长期处于降级链末端、几乎没有被真实调用过。本模块把**全部可用模型当作玩家**放进一局「谁是卧底」，用对抗对局把它们一次性全部拉到同一场景下：

- 给模型矩阵一个**可复现的横向对比场景**（同一网络、同一 RNG 序列、同一 prompt 契约）。
- 用「描述质量」而非跑分暴露差异——谁在多方博弈里措辞更稳、谁更容易被识别、谁开始胡编。
- 顺带为 LLM 层补上**按 `(role, model_id)` 精确定名调用**的能力（见第 5 节），这是本模块的工程副产品，也是它得以成立的前提。

---

## 2. 核心设计决策

### 2.1 玩家不知道自己是谁（与常见实现的关键差异）

常规「谁是卧底」里，卧底是**被告知**自己是卧底的。本实现刻意不做这件事：

- 平民与卧底拿到的 `_DESCRIBE_PROMPT` **逐字相同**，卧底看不到任何身份标记，只能从他人公开发言里推断自己是不是少数派。
- 因此 prompt 里对「如何伪装」的指导必须是**面向所有玩家的条件式指令**（「若你判断自己是卧底，可参考以下隐蔽手法……」），而不是发给卧底的分支文本。
- 身份判断写进**私有面**（`self_identity` / `self_confidence` / `self_reason`），且判定标准刻意保守：明确写出「用词不同、详略不同、观察角度不同都不等于图片不同」，要求「只有在主体元素几乎无人提及、且多数人反复提到的主体元素自己完全没看到」时才判卧底，本轮首位发言人**必须**填「不确定」——因为此时没有任何比较对象。误判自己是卧底会让模型说话含糊，反而更容易被投出去。

`test_describe_prompt_is_identical_for_spy_and_civilian` 守卫「同一份 prompt」这一点，任何试图按身份分支的改动都会让它失败。

### 2.2 顺序描述 + 并行投票

每轮拆成两阶段，串并行是刻意区分的：

| 阶段 | 并发 | 理由 |
|------|------|------|
| 描述 | **顺序** | 每人发言时可见本轮前面所有人的公开内容——这既是卧底校准措辞的前提，也是平民观察从众效应的前提 |
| 投票 | **并行** | 描述已全部公开，投票者之间没有新信息流动，串行只增加墙钟时间而不产生博弈价值 |

发言顺序在开局随机确定、**整局不变**，但每轮起点顺延一位（`order_mode=rotate`，可切 `fixed`）。起点在**完整基准序**上计算、之后再过滤死者——若改为在存活子集上算起点，每淘汰一人都会让全体相对位次漂移，轮转偏移量就失去意义了。

### 2.3 公私两面分层

单轮产出被切成两面，边界由 `SpeechRecord` 的字段顺序显式表达：

- **公开面**（进其他玩家 prompt）：`description`、`response`。
- **私有面**（只写复盘档案，**永不进他人 prompt**）：`observation_list`、`public_elements`、`withheld_elements`、`self_identity`、`self_confidence`、`self_reason`。

私有面之所以必须私有：身份自评若进公开面，卧底要么自曝、要么就得对身份说谎——而「不可编造、不可否认实际看到的元素」是本游戏对描述侧的硬约束，两者会直接冲突。`_render_private_history()` 只渲染该玩家**自己**的历史私有档案，`test_other_players_records_not_included` 守卫越界。

分工上：观察清单在公开描述**之前**完成（`test_private_section_precedes_public_in_format_spec`），避免模型为了配合后面写出的描述而回头删减清单——那份清单是复盘时核查「是否如实描述」的基准。

### 2.4 防「装死」成为严格优势策略

投票判据是「谁最与众不同」。若一个玩家**什么都不说**，它就永远不是最与众不同的那个——拒答会成为卧底的严格优势策略。

因此两种失败必须区别对待：

| 状态 | 占位符 | 是否进比对块 | 理由 |
|------|--------|:---:|------|
| `api_error` | `（未能获取描述，本轮弃权）` | ❌ 否 | 技术性故障，玩家客观上没发言，不构成判据（投票 prompt 中也显式声明「因系统故障未发言的玩家不参与比对」） |
| `malformed` | `（本轮发言格式不合规，未能提供有效描述）` | ✅ 是 | 玩家确实「发过言」，只是内容劣质——第 3 位玩家看到的是一条劣质发言而不是一个空洞，两者对后续推理的含义完全不同 |

`_render_prior_speeches()` 排除 `api_error` 发言者、保留 `malformed`，两项各有测试守卫。`_run_sequential()` 中单点异常也一律兜底为 `malformed` 而非 `api_error`，同一理由。

### 2.5 位次偏差可诊断

顺序发言下第 1 位掌握的本轮信息最少，描述天然最独特，而投票规则恰好是「谁最与众不同」。这是结构性的**位次惩罚**，所以：

- 投票 prompt 显式声明「发言位次本身不是判据」。
- `_eliminated_positions()` 逐轮记录被淘汰者的发言位次与身份，写入日志的 `eliminated_order_index`。某位次的淘汰占比若显著高于 1/N，即说明 prompt 里的约束没生效——省去每轮人工手算。

### 2.6 终止条件与诚实记账

- 卧底全出局 → `civilians` 胜；场上卧底人数 **>** 平民人数 → `spy` 胜。
  判定用**严格多数**：人数相同时游戏继续，下一票直接决定胜负。写成 `>=` 会让两侧的取胜
  成本不对等——卧底只要打平即胜，而平民必须清空卧底才能赢（单卧底时即退化为「存活 ≤2 且卧底存活」）。
  与之互补的 `continuing` 判定必须同步写成「卧底存活且不多于平民」，否则打平轮不发 `round_waiting`，
  Web 端手动步进模式下该轮会被静默跳过。
- `max_rounds` 默认 `max(人数×2, 4)` **强制封顶**：全员弃权（所有调用失败或被熔断）时无人淘汰、存活集合不变，会真实死循环。
- 达到上限仍未分出胜负记为 `stalemate`，并在 `errors` 里留下明细——**不谎报一方获胜**。
- 平票处理：至多 `_MAX_REVOTE_ROUNDS = 3` 次限定重投（每次把上次投票分布显式喂回 prompt，否则模型缺少「上次为何平票」的信息会反复平票）；重投耗尽仍未分胜负则在平票候选间**随机裁决**（显式记入日志），保证每轮必有人出局、对局能收敛。重投全员弃权时保留上一次有效投票，而不是把有效票丢成空票。
- 结算保存**生效投票**（`votes`）与**首投留存**（`initial_votes`）两套，重投不会覆盖首轮表态，复盘时可看立场变化。

### 2.7 投票判据的证据分级

§2.4 解决了「一句话不说」，但没解决它的近亲——**说得很简略**。两者在票理由上长得一样：都是「未提及某人人都提到的元素」。

一局 12 人局（8 平民 vs 4 卧底，卧底胜）的复盘数据：被淘汰的 7 人中 6 名平民，票理由**几乎全是「未提及型」**（「他没提到水面/旧楼屋顶/孤星/中央亮街」）；唯一出局的卧底死于**明确互斥矛盾**（他说「天空漆黑无星」，多人说「一颗亮星」）。**没有任何一名卧底死于未提及型证据**——复述共识元素是卧底最省力的伪装，而旧文案恰恰把这条判据列为推荐。

于是投票 prompt 改为证据分档：

| 档位 | 定义 | 用法 |
|------|------|------|
| **强证据**（优先） | 对方明确写下的元素与你亲眼所见**不能同时成立** | 一旦成立即投他，不必再比谁提到的元素多 |
| **弱证据**（兜底） | 对方没提到多数人反复确认的公共特征 | 须同时满足：无互斥矛盾、横向重合数明显最低、能说清为什么是他 |

配套三处：「没有提到」不等于「没有看到」（50 字上限、位次靠前者信息少、系统故障会少说）；**自证与复读的区分**（用自己的话确认 2-3 项公共特征是自证，照抄才是复读——薄描述不再等于可疑）；**否认型埋钩**（有人否认你亲眼看到的元素时不要改口，也不要转票）。

已知交换比：这保护了卧底最便宜的伪装，卧底会更难被抓。止损靠「薄描述不再等于可疑」与既有的裁判增量要求（第 2 轮起卧底必须持续吐新元素，而新元素只能取自它自己那张图）——净效果是把卧底暴露从中盘往后拖，而非免于暴露。

同源的另两条：**弃权的代价**（弃权等于把决定权让给别人，卧底票通常更集中；仅在两级证据都不成立时保留出口）与**「投中过卧底」不等于可信**（卧底会牺牲已暴露的同伴换信任，跟票成本为零——上一局 6/8/9 号第 3 轮集体投掉已暴露的 12 号后一路潜伏到终局）。

---

## 3. 对局流程

```
初始化
├─ 解析参与模型（显式指定 or llm.json 全部可用模型）
├─ 去重 → 截断 max_players → 校验人数 ≥3
├─ 抽卧底（≥8 人抽 2 名，否则 1 名；须 < 人数一半）
├─ shuffle 出整局不变的发言基准序   ← 必须在抽卧底之后，否则 RNG 消费序列变化、
│                                    固定 seed 的复现性失效
└─ 两张图片编码为 base64 data URL

每轮
├─ 阶段 1：顺序描述（存活玩家逐个调用 generate_multimodal_as）
│   每人 prompt = 规则 + 轮次/位次 + 历史公开记录 + 本轮前面所有人发言
│                + 自己的私有档案；图 = 卧底看 B、平民看 A
└─ 阶段 2：并行投票（存活玩家同时调用 generate_as）
    候选 = 全部存活玩家 → 计票 → 平票则限定重投（≤3 次）→ 随机裁决
    → 淘汰、记录 was_spy（仅复盘，不写入 prompt）

判定
├─ 卧底全出局 → civilians 胜，结束
├─ 卧底 > 平民 → spy 胜，结束
└─ 否则继续（含人数打平），直至 max_rounds 上限 → stalemate
```

---

## 4. 数据结构与输出

```
GameResult
├─ image_civilian / image_spy   两张图片路径
├─ players / spy_keys / spy_count
├─ rounds: [RoundRecord]
├─ winner                        civilians / spy / stalemate
├─ final_survivors / errors
├─ schema_version = 2
├─ speaking_order_base / order_mode / seed / max_rounds
└─ spy_key                      「首个卧底」兼容别名（双卧底请用 spy_keys）

RoundRecord
├─ descriptions / responses      speeches 的公开投影（dict 形态，兼容 v1 复盘脚本）
├─ votes / vote_reasons          最终**生效**的一次投票
├─ vote_tally
├─ initial_votes / initial_vote_reasons   首投留存
├─ eliminated / eliminated_was_spy / revote_rounds
├─ speaking_order / speeches / silent / malformed / elapsed_sec
└─ to_dict()

SpeechRecord
├─ 公开面：description / response
├─ 私有面：observation_list / public_elements / withheld_elements
│          self_identity / self_confidence / self_reason
└─ 运行态：order_index / status(ok|api_error|malformed|legacy_plain) / raw_text
```

**向后兼容**：`GameResult.to_dict()` 前 8 个键的键名与顺序与 v1 完全一致（旧复盘脚本零改动可用），v2 键追加在末尾。`RoundRecord` 的 `descriptions` / `responses` 保留 v1 的 dict 形态。`raw_text` 仅在 `status=malformed` 时留存，且截断到 `_RAW_TEXT_KEEP = 800` 字符。

---

## 5. LLM 层的支撑：精确模型调用

游戏要求「每个玩家必须是它自己」，既有的 `force_model` 路径不满足这个要求：

- `force_model` 走 `ModelManager.find_model_by_name()`，按 `model` 字段做**字符串匹配**。当同一 `model` 字段被多个 `model_id` 复用时（`base_model` 下 `deepseek-flash-zz` 与 `deepseek-flash` 的 `model` 字段都是 `"deepseek-flash"`），会误中字典顺序里排在前面的那个。
- 路由路径带**降级链**，一旦首选模型失败，玩家会被静默换成另一个模型——这直接破坏对抗本身。

本版因此新增一层精确调用：

```
LLMService.generate_as(role, model_id, prompt, ...)            # 文本
LLMService.generate_multimodal_as(role, model_id, parts, ...)  # 多模态
        ↓
EnvironmentConfiguredLLMProvider.generate_as / generate_multimodal_as
        ↓
ModelManager.get_model_config(role, model_id)   # 按 (role, model_id) 二元键精确查找
```

要点：

- **不做降级**：失败直接抛 `LLMProviderError`。熔断键取 `role/model_id`（含斜杠，与路由路径的模型键不冲突）。
- 多模态路径复用既有 `_call_openai_compatible_multimodal` / `_call_anthropic_multimodal`，并校验 `multimodal` 配置项——非多模态模型**直接拒绝**，而非静默退化为纯文本。
- 用量统计 `matched_rule="exact_model"`，可与其他路由来源区分。
- **不覆盖 `max_tokens`**，交给模型自身配置：推理型模型（如 `deepseek-flash-zz`）需要在预算内先跑完思维链再输出正文，硬编码小值会 `finish_reason=length` 而正文为空（v3.37.3 修过同类问题）。

---

## 6. 命令行用法

```bash
# 全部可用模型参战，随机种子（不可复现）
python scripts/run_cli.py undercover-game --image-a path/a.png --image-b path/b.png

# 固定种子复现同一局（卧底归属与发言顺序一并复现）+ 落盘复盘档案
python scripts/run_cli.py undercover-game \
    --image-a a.png --image-b b.png \
    --seed 20260915 --output-file data/undercover/w38.json --pretty

# 控成本：只让前 6 个模型上场，最多 6 轮
python scripts/run_cli.py undercover-game \
    --image-a a.png --image-b b.png --max-players 6 --max-rounds 6

# 指定参赛模型（role/model_id，逗号分隔）
python scripts/run_cli.py undercover-game \
    --image-a a.png --image-b b.png \
    --game-models base_model/deepseek-flash,adv_model/qwen3.8-max-zz
```

| 参数 | 说明 |
|------|------|
| `--image-a` / `--image-b` | 平民图 / 卧底图，**必填**；两者相同直接拒绝（无差异可辨，游戏失去意义） |
| `--game-models` | 参赛模型 `role/model_id` 逗号分隔；默认全部可用模型。逐项校验并 warning 跳过非法项，去重后 <3 报错 |
| `--max-players` | 人数上限（0=全部），按配置优先级截断。**去重之后**才截断，否则重复项会占掉名额 |
| `--seed` | 随机种子（0=随机）。固定后可复现卧底归属与发言顺序 |
| `--max-rounds` | 轮次上限（0=按人数自动推导） |
| `--order-mode` | `rotate`（每轮起点顺延，默认）/ `fixed`（整局固定） |
| `--output-file` | 对局 JSON 落盘路径（原子写） |
| `--pretty` | 格式化输出 |

---

## 7. 成本与调参

单轮调用数为 `2N`（N 名玩家：N 次多模态描述 + N 次文本投票），轮次上限默认 `max(2N, 4)`，故**最坏情况约 4N² 次调用**——11 个模型全上场时上限约 484 次。描述阶段的 prompt 还随轮次累积历史，单次成本同步上升。

控成本手段：

- `--max-players 6`：最坏约 144 次调用，且 6 人时只抽 1 名卧底。
- `--max-rounds`：直接封顶轮次。
- `--game-models`：手动挑选参战模型。

建议先用小规模（4–6 人、4–6 轮）跑通并固定 `--seed`，确认识别效果后再放开。

---

## 8. 测试

共 122 项新增用例（本版基线 3,362 → 3,484）：

| 文件 | 项数 | 覆盖 |
|------|:---:|------|
| `tests/unit/test_games_undercover_pure.py` | 63 | 纯逻辑：身份/置信度/观察清单解析、发言分段与回退、私有面不外泄、prompt 契约 12 项（同一份 prompt / 禁止编造与否认 / 禁止联络 / 借用共享词须在己方图中成立 / 私有段先于公开段 / 投票 prompt 不暴露身份自评 / 双卧底胜负条件）、schema 兼容 |
| `tests/test_games_undercover.py` | 46 | 运行期：投票解析与歧义取最长键、投票行优先于正文、逐项兜底、平票重投、淘汰判定、RNG 复现性 |
| `tests/test_provider_fallback.py` | 9 | 精确调用：精确命中、缺模型报错、失败不降级、非多模态拒绝、`get_model_config` 精确匹配与同名不冲突 |
| `tests/test_llm_service.py` | 4 | 透传与异常传播 |

---

## 9. 已知限制与后续

- **无实时观战**：整局一次性跑完才输出，中途不可干预。若要观察单轮过程，需接入 `taskpanel` 埋点（当前**未接**——单局时长与模型数相关，尚未观察到分钟级稳定耗时，暂以 `--output-file` 落盘替代）。
- **成本未做硬预算**：只有轮次与人数上限，没有 token/费用熔断。放开全部模型跑满时最坏约 484 次调用，需人工用 `--max-players` 控制。
- **无跨局统计**：单局结果各自落盘，没有「同一模型在多局中的存活率/被投出率」汇总。要横向对比模型表现需自行聚合 `--output-file` 产物。
- **图片对称性靠人工**：`--image-a` / `--image-b` 的差异度直接决定对局质量，差异过小会退化成纯嘴炮、差异过大会变成一眼出局。程序只校验「不是同一张」，不评估差异度。
- **身份推断未做校准**：`self_confidence` 只是模型自报值，未与「是否真是卧底」做统计校核（`self_identity` 与 `is_spy` 的一致性可以作为后续复盘指标）。
