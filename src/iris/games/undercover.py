"""多模型对抗游戏：谁是卧底（Undercover）。

利用 LLMService 全部可用模型作为玩家，随机抽 1-2 名卧底看图片 B，其余平民看图片 A。
**玩家事先都不知道自己的身份**，只能根据每一轮的公开发言推断自己是不是卧底。

每轮流程：

1. **顺序描述**——发言顺序在开局随机确定、整局不变，但每轮起点顺延一位。
   每个人发言时都能看到本轮前面所有人的公开内容，据此校准自己的措辞。
2. **并行投票**——描述全部公开后收集投票（此时无新信息流动，并行不损博弈价值）。
   得票最高者淘汰，平票则在候选间限定重投。

玩家的单轮产出分两层：

- **公开面**（`description` / `response`）：进入其他玩家的 prompt，必须真实、不可编造。
- **私有面**（`observation_list` 等）：只写入复盘档案，永不进他人 prompt。
  之所以要私有，是因为身份自评若公开，卧底要么自曝、要么就得对身份说谎——而
  「不可编造或说谎」是本游戏对图片描述侧的硬约束。

胜负：所有卧底被投出 → 平民胜；场上卧底人数不少于平民人数 → 卧底胜。
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from iris.config.loader import ConfigBundle
from iris.core.exceptions import IrisRuntimeError
from iris.core.thread_pool import shared_pool
from iris.llm import LLMProviderError
from iris.llm.service import LLMService
from iris.utils.llm_parsing import try_parse_json

logger = logging.getLogger(__name__)


class _GameCancelled(IrisRuntimeError):
    """内部协作取消信号。"""



class UndercoverGameError(IrisRuntimeError):
    """游戏运行期错误。"""


# ── 模块常量 ──────────────────────────────────────────────────────

#: 参与人数达到该阈值时抽 2 名卧底，否则 1 名。
_DOUBLE_SPY_MIN_PLAYERS = 8

#: 调用失败（LLMProviderError）时的公开占位符——保留原文以兼容既有复盘脚本。
_DESCRIBE_FAILED_PLACEHOLDER = "（未能获取描述，本轮弃权）"

#: 模型返回了内容但解析不出公开描述时的占位符。
#: 刻意与上面区分：技术性沉默不进比对块，而「发言格式不合规」必须留在比对块里，
#: 否则「装死」会成为卧底的严格优势策略（沉默者永远不是最与众不同的那个）。
_SPEECH_MALFORMED_PLACEHOLDER = "（本轮发言格式不合规，未能提供有效描述）"

#: 无分节标记的短文本按整段描述处理的上限（兼容只返回一句话的旧调用约定与测试桩）。
_MAX_DESCRIPTION_CHARS = 120

#: prompt 里对观察清单条数的要求值（仅用于文档化，不强制截断）。
_MAX_OBSERVATION_ITEMS = 12

#: 解析时保留的观察清单条数上限——防止模型无视格式约束吐出超长清单。
_MAX_OBSERVATION_KEEP = 50

#: status=malformed 时留存的原始文本长度上限。
_RAW_TEXT_KEEP = 800

_ORDER_MODES = ("rotate", "fixed")

_IDENTITY_LABELS = {"civilian": "平民", "spy": "卧底", "uncertain": "不确定"}
_LABEL_TO_IDENTITY = {v: k for k, v in _IDENTITY_LABELS.items()}


# ── 数据模型 ──────────────────────────────────────────────────────


@dataclass
class GamePlayer:
    """游戏玩家。alive 字段在对局过程中会被淘汰逻辑修改，故非 frozen。"""

    role: str           # "base_model" / "adv_model"
    model_id: str
    key: str            # "role/model_id"
    is_spy: bool
    display_name: str = ""
    alive: bool = True
    player_number: int = 0  # 全局固定编号（1-indexed），对局期间不变


@dataclass
class SpeechRecord:
    """单个玩家在一轮内的完整产物：公开面 + 私有档案。

    `description` / `response` 会进入其他玩家的 prompt；其余字段（`observation_list`
    起）**永远不进他人 prompt**，只写复盘档案。
    """

    key: str
    order_index: int = 0                                    # 本轮内 0-based 位次
    # ── 公开面 ──
    description: str = ""
    response: str = ""
    challenge: str = ""  # 对某位玩家的质疑（可为空），第2轮起可用
    # ── 私有面 ──
    observation_list: List[str] = field(default_factory=list)    # 完整观察清单
    public_elements: List[str] = field(default_factory=list)     # 自标 (公开)
    withheld_elements: List[str] = field(default_factory=list)   # 自标 (保留)
    self_identity: str = "uncertain"     # civilian / spy / uncertain
    self_confidence: int = 0             # 0-100
    self_reason: str = ""
    # ── 运行态 ──
    status: str = "ok"                   # ok / api_error / malformed / legacy_plain
    raw_text: str = ""                   # 仅 status=malformed 时留存

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RoundRecord:
    """单轮记录。

    `votes` / `vote_reasons` / `vote_tally` 保存**最终生效**的一次投票
    （平票重投后即为末次重投），淘汰判定基于它们；首投结果另存于
    `initial_votes` / `initial_vote_reasons`，避免重投把首轮表态覆盖丢失。

    `descriptions` / `responses` 是 `speeches` 的公开投影，保留原有 dict 形态，
    让 v1 的复盘脚本无需改动。
    """

    round_no: int
    descriptions: Dict[str, str] = field(default_factory=dict)
    votes: Dict[str, str] = field(default_factory=dict)
    vote_reasons: Dict[str, str] = field(default_factory=dict)
    vote_tally: Dict[str, int] = field(default_factory=dict)
    initial_votes: Dict[str, str] = field(default_factory=dict)
    initial_vote_reasons: Dict[str, str] = field(default_factory=dict)
    eliminated: Optional[str] = None
    revote_rounds: List[Dict[str, Any]] = field(default_factory=list)
    # ── v2 追加 ──
    speaking_order: List[str] = field(default_factory=list)   # 本轮实际发言顺序（已剔除死者）
    speeches: List[SpeechRecord] = field(default_factory=list)
    responses: Dict[str, str] = field(default_factory=dict)   # 公开投影，仅含非空回应
    silent: List[str] = field(default_factory=list)           # api_error
    malformed: List[str] = field(default_factory=list)        # 解析失败
    eliminated_was_spy: bool = False                          # 仅供复盘分析，不写入 prompt
    elapsed_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class _VoteOutcome:
    """投票阶段产物（含首投与重投轨迹）。"""

    votes: Dict[str, str]
    reasons: Dict[str, str]
    initial_votes: Dict[str, str]
    initial_reasons: Dict[str, str]
    revote_rounds: List[Dict[str, Any]]


@dataclass
class GameResult:
    """完整对局结果。"""

    image_civilian: str
    image_spy: str
    players: List[str]
    rounds: List[RoundRecord] = field(default_factory=list)
    winner: str = ""              # "civilians" / "spy" / "stalemate"
    final_survivors: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # ── v2 追加 ──
    spy_keys: List[str] = field(default_factory=list)
    spy_count: int = 0
    schema_version: int = 2
    speaking_order_base: List[str] = field(default_factory=list)
    order_mode: str = "rotate"
    seed: Optional[int] = None
    max_rounds: int = 0

    @property
    def spy_key(self) -> str:
        """兼容别名：只返回**首个**卧底。双卧底场景请改用 `spy_keys`。"""
        return self.spy_keys[0] if self.spy_keys else ""

    def to_dict(self) -> Dict[str, Any]:
        # 前 8 个键的键名与顺序与 v1 完全一致（旧复盘脚本零改动可用），v2 键追加在末尾。
        return {
            "image_civilian": self.image_civilian,
            "image_spy": self.image_spy,
            "spy_key": self.spy_key,
            "players": self.players,
            "rounds": [r.to_dict() for r in self.rounds],
            "winner": self.winner,
            "final_survivors": self.final_survivors,
            "errors": self.errors,
            "schema_version": self.schema_version,
            "spy_keys": self.spy_keys,
            "spy_count": self.spy_count,
            "speaking_order_base": self.speaking_order_base,
            "order_mode": self.order_mode,
            "seed": self.seed,
            "max_rounds": self.max_rounds,
        }


# ── Prompt 模板 ──────────────────────────────────────────────────

_DESCRIBE_PROMPT = """你正在参与「谁是卧底」游戏。

【规则】
- 全部 {total_players} 名玩家中，有 {spy_count} 人看到的图片与其他人不同，这些人是「卧底」；其余人看到同一张图片。
- 没有人知道自己的身份，包括你。你只能根据每一轮其他人的发言来推断自己是不是卧底。
- 每轮所有人依次发言，发言顺序在开局确定；发言完毕后全体投票，得票最多者被淘汰出局。
- 所有卧底都被投出，则平民获胜；场上卧底人数不少于平民人数，则卧底获胜。
- 只要游戏在淘汰后继续进行，就说明场上仍有卧底存活。

【你的目标由你推断出的身份决定】
你事先不知道自己是哪一类，但你的行动必须服从当下的判断：
- 若你判断自己属于多数派（平民）：目标是把卧底找出来投出去。说清楚你看到的独特元素，
  帮同伴缩小范围；发现别人描述里的具体矛盾时直接指出来。
- 若你判断自己属于少数派（卧底）：目标是活到最后。让别人无法从你的发言里认出你与多数派
  不同——这是本局你唯一的目标，赢下每一轮投票不如活到最后一轮。
- 判断不确定时，按多数派的方式说话（如实描述、给出信息）。含糊其辞本身就是异常信号，
  会把你直接标成最与众不同的那个。

【允许的策略】
- 可以隐瞒你词语的某些属性（略去某些元素不说）
- 可以有意让描述方向偏离真相（措辞模糊、使用上位词），但不能描述你图片里根本不存在的元素
- 可以对其他玩家的描述提出质疑，例如指出某人描述的元素与你的观察有矛盾
- 所有其他玩家的发言（包括他们的质疑）都可以作为你的参考

【增量发言要求（第2轮起强制执行）】
{incremental_requirement}

【首发提示（仅本局第一位发言者适用）】
{opening_note}

【本轮情况】
轮次：第 {round_no} 轮
你的标识：{player_number}号={player_key}
你在本轮发言顺序中排第 {speaking_position} 位（本轮共 {alive_count} 人发言）

【此前轮次的完整公开记录】
{history}

【本轮已发言玩家的公开内容】
{prior_speeches}

【你往轮留下的私有档案（只有你自己能看到）】
{own_private_history}

【发言要求】
第一阶段，先写私有部分。这部分其他玩家永远看不到，只写入复盘档案：
1. 【观察清单】逐条列出你在这张图里能辨认出的全部元素，尽量完整，其中包括你认为可能只有自己才看到的元素。
   每条不超过 12 字，总数不超过 12 条。
   每条末尾用 (公开) 或 (保留) 标注你本轮是否打算把它说出去。
   这一阶段必须在你写公开描述之前完成，不要为了配合后面的描述而删减。
2. 【身份自评】把你自己上面这份清单，与其他玩家已经公开的内容做比较，判断你属于哪一类：
   - 平民：你看到的核心元素与多数人的公开内容一致
   - 卧底：你的清单与多数人的公开内容存在系统性矛盾
   - 不确定：证据不足
   同时给出 0 到 100 的置信度，并用一句话说明依据。
   重要：用词不同、详略不同、观察角度不同、关注点不同，都不等于图片不同，绝大多数差异来自表述习惯。
   只有下面任一条成立时，才应判定为卧底：
   - 主体矛盾：你看到的主体元素几乎无人提及，同时多数人反复提到的主体元素你完全没有看到；
   - 属性冲突：多数人反复提到某个具体属性 A，而你明确看到的是与 A 互斥的 B。
     例如他们都说「圆领、无翻领」，而你看到「有翻领和纽扣」——即使人物、背景等主体元素
     完全一致，局部的关键属性冲突同样是身份证据，不要因为「主体元素都对得上」就排除自己。
     冲突的属性越具体、提到 A 的人越多，你属于少数派的把握就越大。
   如果本轮已有别人描述的正是你看到的主体元素，而那不是大多数人的内容，你们很可能同属少数派——但这只说明你们看到同一张图，不能据此推断自己是平民。
   如果你是本轮第一位发言的人，你没有任何比较对象，必须填「不确定」。
   宁可填「不确定」。误判自己是卧底会让你说话含糊，反而更容易被淘汰。

第二阶段，再写公开部分，其他玩家会看到这些内容：
3. 【公开描述】不超过 50 字。
   - 必须真实：你写下的每一个元素，都必须能在你自己那张图里指认出来。
     可以比清单更笼统（例如用上位词代替具体物件），但不可以更具体，也不可以写图里根本没有的东西。
   - 允许取舍（略去某些元素）、概括、模糊化，不必把观察清单里的元素全部说出。
   - 若你判断自己是卧底，你的目标是在不违反上一条的前提下尽量不被识别出来。可以参考的隐蔽手法：
     取舍：略去别人没提到、而你独有的元素；
     概括：用上位词代替具体物件；
     借用共享词：优先使用别人已经用过、并且在你自己的图里同样成立的词；
     措辞模糊化：不给出可被单独核查的具体名词；
     转移视线：把讨论引向他人描述中真实存在的差异（必须真实，不得凭空指控或编造）。
   - 若你判断自己是卧底，留意哪些玩家的公开内容与你自己那张图相符——他们很可能是同类。
     不要把质疑或投票指向你推断出的同类（那等于替多数派清场），把注意力引向其他人的真实差异。
     识别只能靠「他的描述与我看到的一致」这一条推断；严禁用任何方式向对方示意、试探或回应信号，
     也不得因为对方可能是同类就放弃上面关于真实性与禁止编造的要求。
   - 不得整段复述他人已公开的内容。借用共享词是允许的，但你的描述里至少要包含一项
     你自己独立观察到的元素——整段照抄等于这一轮没发言，既没帮到同伴，也让「复读」成了
     零成本的隐身术。
   - 严禁为了隐藏而写下你图中并不存在的元素；严禁否认你实际看到的元素。
   - 注意「借用共享词」的边界：只有当这个词在你自己的图里也成立时才能用。
     若别人提到的元素你图里没有，你就不提它，但绝不能附和。
   - 严禁在公开内容中透露或暗示你是平民还是卧底，也严禁试图与其他玩家建立任何联络或约定。
     即使你已推断出谁是同类，也不能发出任何信号。
4. 【回应】一句话，针对本轮在你之前发言的某位玩家，或对上一轮某玩家的质疑/说法作出回应。
   若你本轮第一位发言，可以回应上一轮的任何玩家；若此前完全没有记录，写「无」。
   - 回应必须是关于图片内容的具体信息（你自己看到的某个元素），不能写成「同意某某」这类表态。
   - 可以质疑对方的描述（例如指出矛盾或提出疑问），质疑必须基于你自己真实看到的内容。
   - 若对方提到的元素你图里没有，可以不回应他，但禁止附和，例如谎称自己也看到了。
   - 若你判断自己是卧底，而对方的描述与你自己那张图确实对不上：
     既不要指出这个差异，也不要附和。改为只回应双方都成立的中性元素（人物、场景、光线类别），
     把差异留在私有档案里，等下一轮再决定是否透露。
     当众指出真实差异等于宣布自己是少数派——这是最快被淘汰的失分方式，
     哪怕你说的每个字都属实、质疑也合乎规则。
   - 若你判断自己是卧底，上面关于真实性、禁止编造、禁止联络的要求同样适用。
5. 【质疑】（可选）对某位玩家的描述提出质疑，不超过 30 字。若无质疑，写「无」。
   质疑必须基于描述内容的具体矛盾，不能仅凭语气或风格。
   若你判断自己是卧底：只在这个矛盾连你自己也认同时才质疑（即对方说的与多数人、
   也包括你自己都不符）。若那个矛盾恰好是你与多数人不同的地方，写「无」——
   质疑它就是把自己的破绽端到台面上。

【输出格式】严格按下面的格式输出，不要在格式之外添加任何文字：
【观察清单】
- 元素一 (公开)
- 元素二 (保留)
【身份自评】平民 或 卧底 或 不确定
【置信度】0 到 100 的整数
【判断依据】一句话
【公开描述】不超过 50 字的描述
【回应】一句话，没有则写「无」
【质疑】针对某玩家的质疑，没有则写「无」"""

_REFEREE_INCREMENT_PROMPT = """你是「谁是卧底」游戏的裁判，负责判断玩家本轮的发言是否包含增量信息。

【玩家标识】{player_number}号={player_key}
【当前轮次】第 {round_no} 轮

【该玩家过往各轮的公开描述及质疑】
{prior_descriptions}

【该玩家本轮的新发言】
公开描述：{new_description}
质疑：{new_challenge}

【判定标准】
增量信息是指以下任意一项：
1. 本轮公开描述中提到了之前各轮从未提及的新元素、新角度、新视角；
2. 本轮质疑中对某玩家的具体描述内容提出了有据可查的质疑（非空洞的「我不确信」）。

以下情况视为无增量：
- 描述内容与过往各轮高度重复（相似度超过 80%，即使换了说法）；
- 质疑为空或等同于「无」；
- 描述极为模糊，不含任何可辨认的元素。

请先逐条对照，再给出判断：

输出格式（严格遵守）：
增量：是 或 否
理由：一句话（不超过50字）"""

_VOTE_PROMPT = """你正在参与「谁是卧底」游戏。全部 {total_players} 名玩家中有 {spy_count} 人看到的图片与其他人不同，这些人是卧底；其余人看到同一张图片。没有人知道自己的身份，包括你。

以下是本局到目前为止的完整公开记录：
{history}

当前存活玩家：{alive_list}
本轮因系统故障未能发言的玩家：{silent_list}

【你往轮留下的私有档案（只有你自己能看到）】
{own_private_history}

请投出你的一票。你的目标同样由你推断出的身份决定：

若你判断自己属于多数派（平民）——目标是投出卧底。判断时请注意：
- 依据必须是描述文本中具体元素的差异，例如某位玩家反复提到别人都没提到的元素，而多数人提到的核心元素他完全没有提到。
- 用词习惯、详略、语气、关注点差异都不是判据。
- 发言位次本身不是判据。本轮靠前发言的人掌握的信息更少，描述更容易显得独特，这是结构性的，不要因此投给他。
- 因系统故障未发言的玩家不参与比对，不作为判据。
- 不要因为别人已经投了某人就跟随。理由必须引用具体的元素差异。

若你判断自己属于少数派（卧底）——目标是活到最后，这一票服务于这个目标：
- 不要投给你推断出的同类（描述与你自己的图相符的人），那等于替多数派清场。
- 投给一位公开描述中共同元素较少的其他玩家以转移视线，优先选择能造成平票的目标，为自己争取时间。不要浪费票。
- 识别同类只能靠公开描述与你自己所见的吻合程度，不得用任何方式向对方示意或试探。

共同要求：
- 被淘汰者的身份不会当场公布；只要游戏没有结束，就说明场上仍有卧底存活。
- 无论你判断自己是什么身份，都不要在理由中透露或暗示你的身份判断，也不要试图与其他玩家建立联络。

请按以下格式输出：
投票：<玩家标识>
理由：<不超过100字，必须引用具体的元素差异>"""


# ── 渲染：history / 轮内前置块 / 私有档案 ────────────────────────


def _format_history(
    rounds: Sequence[RoundRecord],
    current_round: int,
    numbers: Optional[Mapping[str, int]] = None,
    *,
    current_speeches: Optional[List[SpeechRecord]] = None,
) -> str:
    """格式化历史记录文本，供 prompt 拼接。

    Args:
        rounds: 已完成的轮次记录
        current_round: 当前轮次编号（同时用于「历史截止」与「当前块标题」两个语义，
            避免旧版传 `round_no - 1` 导致当前块被标成「第0轮」）
        current_speeches: 当前轮的发言（投票阶段传入；描述阶段为空）
    """
    if not rounds and not current_speeches:
        return "（本局暂无历史记录，这是第一轮）"
    lines: List[str] = []
    for record in rounds:
        if record.round_no >= current_round:
            break
        _render_round(lines, record, numbers)
    if current_speeches:
        _render_speeches_block(
            lines,
            current_round,
            [s.key for s in current_speeches],
            {s.key: s.description for s in current_speeches},
            {s.key: s.response for s in current_speeches if s.response},
            [s.key for s in current_speeches if s.status == "api_error"],
            numbers,
        )
    return "\n".join(lines)


def _render_speeches_block(
    lines: List[str],
    round_no: int,
    order: Sequence[str],
    descriptions: Dict[str, str],
    responses: Dict[str, str],
    silent: Sequence[str],
    numbers: Optional[Mapping[str, int]] = None,
) -> None:
    """渲染一轮的描述 + 未发言块（历史轮与当前轮共用同一套语义）。

    行首的 `N.` 是**本轮发言位次**（轮转后每轮不同，是游戏机制的一部分），
    紧随其后的 `N号=key` 是**全局固定编号**——两者含义不同，都保留。
    """
    lines.append(f"[第{round_no}轮描述]")
    silent_set = set(silent)
    for index, key in enumerate(order):
        # 技术性沉默的玩家不进比对块——判别规则是「谁最与众不同」，而一句
        # 「未能获取描述，本轮弃权」是 100% 与众不同的，会把他们变成误伤目标。
        # 位次编号仍按真实序号走，不因跳过而重排。
        if key in silent_set:
            continue
        lines.append(f"  {index + 1}. {_player_label(key, numbers)}: {descriptions.get(key, '')}")
        response = responses.get(key, "")
        if response:
            lines.append(f"     └ 回应: {response}")
    if silent:
        labels = ", ".join(_player_label(k, numbers) for k in silent)
        lines.append(
            f"[第{round_no}轮未发言] {labels}"
            f"（系统故障导致，与图片内容无关，不参与比对、不作为判据）"
        )


def _render_round(
    lines: List[str],
    record: RoundRecord,
    numbers: Optional[Mapping[str, int]] = None,
) -> None:
    """渲染一个已完成的轮次（描述 → 未发言 → 投票 → 结果）。"""
    # 顺序发言后位次是语义信息，不能再依赖 dict 插入序；无 speeches 时回落 v1 的 dict。
    _render_speeches_block(
        lines,
        record.round_no,
        record.speaking_order or list(record.descriptions.keys()),
        record.descriptions,
        record.responses,
        record.silent,
        numbers,
    )
    if record.votes:
        lines.append(f"[第{record.round_no}轮投票]")
        for key, target in record.votes.items():
            lines.append(
                f"  {_player_label(key, numbers)} 投给 {_player_label(target, numbers)}，"
                f"理由：{record.vote_reasons.get(key, '')}"
            )
    if record.eliminated:
        # 刻意不公布被淘汰者身份（无论他是不是卧底）——「场上仍有卧底存活」这个事实
        # 已经蕴含在游戏继续这一观察里，由模型自行推断。
        lines.append(
            f"[第{record.round_no}轮结果] {_player_label(record.eliminated, numbers)} "
            f"被淘汰出局，场上仍有卧底存活"
        )


def _render_speech_line(
    lines: List[str],
    speech: SpeechRecord,
    numbers: Optional[Mapping[str, int]] = None,
) -> None:
    """渲染单条发言的公开面（描述 + 回应 + 质疑）。"""
    lines.append(
        f"  {speech.order_index + 1}. {_player_label(speech.key, numbers)}: {speech.description}"
    )
    if speech.response:
        lines.append(f"     └ 回应: {speech.response}")
    if speech.challenge:
        lines.append(f"     └ 质疑: {speech.challenge}")


def _render_prior_speeches(
    speeches: Sequence[SpeechRecord],
    numbers: Optional[Mapping[str, int]] = None,
) -> str:
    """渲染「本轮已发言玩家」块。

    只含 `status != "api_error"` 的发言：技术性沉默不该以「本轮弃权」的刺眼形态
    出现在轮内比对块里——那正是要修的缺陷在轮内的翻版。
    """
    visible = [s for s in speeches if s.status != "api_error"]
    if not visible:
        return "（本轮前面还没有人给出可参考的有效发言）"
    lines: List[str] = []
    for speech in visible:
        _render_speech_line(lines, speech, numbers)
    return "\n".join(lines)


def _render_private_speech(speech: SpeechRecord) -> str:
    """渲染单条发言的私有档案。"""
    lines: List[str] = []
    if speech.observation_list:
        lines.append(f"  你的观察清单: {' / '.join(speech.observation_list)}")
    if speech.public_elements:
        lines.append(f"  你当时打算公开: {', '.join(speech.public_elements)}")
    if speech.withheld_elements:
        lines.append(f"  你当时打算保留: {', '.join(speech.withheld_elements)}")
    lines.append(
        f"  你的身份自评: {_IDENTITY_LABELS.get(speech.self_identity, '不确定')}"
        f"（置信度 {speech.self_confidence}）"
    )
    if speech.self_reason:
        lines.append(f"  你的判断依据: {speech.self_reason}")
    lines.append(f"  你公开的描述: {speech.description}")
    return "\n".join(lines)


def _render_private_history(
    prior_rounds: Sequence[RoundRecord],
    player_key: str,
    current_speeches: Optional[Sequence[SpeechRecord]] = None,
) -> str:
    """渲染该玩家自己的往轮私有档案。

    这是需求「通过每轮的对话判断自己身份」能成立的唯一数据来源：没有它，
    第 2 轮的玩家不记得自己上一轮是怎么判断的。
    """
    blocks: List[str] = []
    for record in prior_rounds:
        speech = next((s for s in record.speeches if s.key == player_key), None)
        if speech is not None:
            blocks.append(f"[第{record.round_no}轮 你的档案]\n{_render_private_speech(speech)}")
    if current_speeches:
        speech = next(
            (s for s in current_speeches if s.key == player_key and s.status != "api_error"),
            None,
        )
        if speech is not None:
            blocks.append(f"[本轮（尚未投票）你的档案]\n{_render_private_speech(speech)}")
    if not blocks:
        return "（暂无：这是你的第一轮发言，没有可对比的历史）"
    return "\n".join(blocks)


def _format_tie_note(
    attempt: int,
    tied: List[str],
    previous_votes: Dict[str, str],
    numbers: Optional[Mapping[str, int]] = None,
) -> str:
    """构造平票重投的补充说明。

    重投时上一次投票尚未写入 RoundRecord，模型看不到「谁投了谁」，
    只被告知候选范围会倾向重复同样的判断、导致重投仍然平票。
    这里把上一次投票分布显式喂回去。
    """
    lines = [f"（第 {attempt} 次平票重投）"]
    if previous_votes:
        lines.append("上一次投票分布（出现平票，需要重新表态）：")
        lines.extend(
            f"  {_player_label(pid, numbers)} 投给 {_player_label(target, numbers)}"
            for pid, target in previous_votes.items()
        )
    tied_labels = ", ".join(_player_label(k, numbers) for k in tied)
    lines.append(f"平票候选：{tied_labels}")
    lines.append(f"本次只能投给候选范围内的玩家：{tied_labels}")
    return "\n".join(lines)


# ── 解析：投票 / 发言分节 / 身份自评 ──────────────────────────────


def _player_label(key: str, numbers: Optional[Mapping[str, int]] = None) -> str:
    """统一的玩家标识：`N号=key`。

    早先历史块用**发言位次**编号（每轮轮转后变化），描述 prompt 用
    `player_number`（全局固定），两套编号并存——第 1 轮恰好重合，第 2 轮起
    分叉，模型说「4号」时无从判断指哪一个。所有引用点统一成 `N号=key` 后，
    模型写编号或写 key 都能被精确解析。
    """
    number = (numbers or {}).get(key)
    return f"{number}号={key}" if number else key


def _parse_vote(
    text: str,
    valid_keys: List[str],
    numbers: Optional[Mapping[str, int]] = None,
) -> tuple:
    """解析投票输出，返回 (target_key_or_None, reason)。

    模型可能不严格遵守格式，做宽松匹配：
      1. 「投票：」行里命中候选 key（长的优先，避免 key 互为前缀时误判）
      2. 「投票：」行里出现「N号」，按 numbers（{key: player_number}）反查
      3. 整段输出里**恰好**提到一个候选 key 时采纳（模型改用散文表达投票意图）
      4. 其余情况返回 (None, 原始文本前100字)，计为弃权

    第 3 条的「恰好一个」是硬约束：早先只要正文里出现 key 就按**长度**取最长的那个，
    而模型常在分析前言里逐个列出各玩家 key——于是挑中谁取决于字符串长度，与它真正
    投的人无关，表现为「投票理由通篇论证甲、投票对象却是乙」。多个候选时无从判断
    意图，宁可记弃权，也不猜。
    """
    reason = ""
    target_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("投票") and ("：" in stripped or ":" in stripped):
            target_line = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif stripped.startswith("理由") and ("：" in stripped or ":" in stripped):
            reason = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()

    if target_line:
        # 长 key 优先匹配：key 之间可能互为前缀（如 base_model/m1 与 base_model/m10），
        # 若按原顺序做 `in` 判断，投给 m10 会被误判成 m1。
        for key in sorted(valid_keys, key=len, reverse=True):
            if key in target_line:
                return key, reason[:100]
        by_number = {n: k for k, n in (numbers or {}).items() if k in valid_keys}
        # 号大者优先：「14号」不该被「4号」抢走（(?<!\d) 已挡住前导数字，排序再兜一层）
        for number in sorted(by_number, reverse=True):
            if re.search(rf"(?<!\d){number}\s*号", target_line):
                return by_number[number], reason[:100]

    hits = _distinct_key_hits(text, valid_keys)
    if len(hits) == 1:
        return hits[0], reason[:100]

    return None, (reason or text.strip())[:100]


def _distinct_key_hits(text: str, valid_keys: Sequence[str]) -> List[str]:
    """按最长优先做**不重叠**匹配，返回文本中出现的候选 key。

    不重叠是关键：`base_model/m1` 是 `base_model/m10` 的子串，若各自独立做
    `in` 判断，提到 m10 会把 m1 也算成命中，把「恰好一个」误判成两个。
    """
    hits: List[str] = []
    remaining = text
    for key in sorted(valid_keys, key=len, reverse=True):
        if key in remaining:
            hits.append(key)
            remaining = remaining.replace(key, "\x00")
    return hits


#: 发言的七个分节标题（新增「质疑」）。模型可能改写标题措辞，用宽松正则只要求标题里含关键词。
_SECTION_RE = re.compile(r"【\s*(观察清单|身份自评|置信度|判断依据|公开描述|回应|质疑)\s*】")

#: 观察清单条目行前缀（- / * / • / 1. / 1、 / 1) ）
_OBSERVATION_PREFIX_RE = re.compile(r"^(?:[-*•·]|\d+[.、)）])\s*")

#: 观察清单条目尾部的 (公开) / (保留) 标注
_OBSERVATION_LABEL_RE = re.compile(r"[（(]\s*(公开|保留)\s*[)）]\s*$")

#: 否定式「不是卧底」——但必须排除「是不是卧底」：那里的「不是卧底」是疑问结构的
#: 一部分（"我不确定自己是不是卧底"），不是否定判断，误判会把不确定读成平民。
_IDENTITY_NEGATED_SPY_RE = re.compile(r"(?<!是)(?:不可能是|不是|并非|非)\s*卧底")

#: 兜底 token 匹配，同样排除紧邻的否定前缀，避免把「不是卧底」判成 spy。
_IDENTITY_TOKEN_RE = re.compile(r"(?<!不是)(?<!非)(卧底|平民|不确定)")

#: 「无回应」的各种写法
_NO_RESPONSE_TOKENS = {"无", "（无）", "(无)", "none", "-", "—", "没有", ""}


def _split_sections(text: str) -> Dict[str, str]:
    """按 【标题】 把模型输出切成 {标题: 正文}。同名分节取首次出现。"""
    matches = list(_SECTION_RE.finditer(text))
    sections: Dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.setdefault(match.group(1), text[match.end():end].strip())
    return sections


def _parse_identity(section: str) -> str:
    """把身份自评分节解析为 civilian / spy / uncertain。

    分级匹配，任何一步失败都退回 `uncertain`——安全默认：宁可不确定，
    也不误判成 spy 触发无谓的隐藏行为。
    """
    text = (section or "").strip()
    if not text:
        return "uncertain"
    if text in _LABEL_TO_IDENTITY:
        return _LABEL_TO_IDENTITY[text]
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line in _LABEL_TO_IDENTITY:
        return _LABEL_TO_IDENTITY[first_line]
    if _IDENTITY_NEGATED_SPY_RE.search(text):
        return "civilian"
    match = _IDENTITY_TOKEN_RE.search(text)
    if match:
        return _LABEL_TO_IDENTITY[match.group(1)]
    return "uncertain"


def _parse_confidence(section: str) -> int:
    """解析置信度并夹到 0-100。"""
    match = re.search(r"\d+", section or "")
    if not match:
        return 0
    return max(0, min(100, int(match.group())))


def _parse_observation_list(body: str) -> Tuple[List[str], List[str], List[str]]:
    """解析观察清单，返回 (全部元素, 自标公开, 自标保留)。"""
    items: List[str] = []
    public: List[str] = []
    withheld: List[str] = []
    for raw_line in (body or "").splitlines():
        element = _OBSERVATION_PREFIX_RE.sub("", raw_line.strip()).strip()
        if not element:
            continue
        match = _OBSERVATION_LABEL_RE.search(element)
        if match:
            element = element[:match.start()].strip()
        if not element:
            continue
        items.append(element)
        if match and match.group(1) == "公开":
            public.append(element)
        elif match and match.group(1) == "保留":
            withheld.append(element)
        if len(items) >= _MAX_OBSERVATION_KEEP:
            break
    return items, public, withheld


def _one_line(text: str) -> str:
    """折叠空白，把多行文本压成一行。"""
    return " ".join((text or "").split())


def _normalize_response(text: str) -> str:
    """归一化「回应」字段：「无」等写法统一成空串。"""
    line = _one_line(text)
    if line.lower() in _NO_RESPONSE_TOKENS:
        return ""
    return line[:200]


def _first_str(payload: Dict[str, Any], *keys: str) -> str:
    """从 JSON 对象里按别名顺序取第一个非空字符串值。"""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _line_prefixed(text: str, *prefixes: str) -> str:
    """从形如「描述：xxx」的行里取值（JSON 兜底之后的启发式末路）。"""
    for line in (text or "").splitlines():
        stripped = line.strip()
        for prefix in prefixes:
            for separator in ("：", ":"):
                if stripped.startswith(prefix + separator):
                    return stripped.split(separator, 1)[1].strip()
    return ""


def _parse_speech(text: str, key: str, order_index: int) -> SpeechRecord:
    """解析一次发言，四级降级：分节 → JSON → 行前缀 → 短文本兜底。

    **安全红线**：全部失败时绝不把 `text` 原文当作公开描述——模型可能把私有推理
    整段吐出来，直接当描述发布等于把观察清单泄漏给全场。
    """
    raw = (text or "").strip()
    record = SpeechRecord(key=key, order_index=order_index)

    # 1. 分节解析（主路径）
    sections = _split_sections(raw)
    if sections:
        (
            record.observation_list,
            record.public_elements,
            record.withheld_elements,
        ) = _parse_observation_list(sections.get("观察清单", ""))
        record.self_identity = _parse_identity(sections.get("身份自评", ""))
        record.self_confidence = _parse_confidence(sections.get("置信度", ""))
        record.self_reason = _one_line(sections.get("判断依据", ""))[:200]
        record.response = _normalize_response(sections.get("回应", ""))
        challenge_raw = _one_line(sections.get("质疑", ""))
        record.challenge = "" if challenge_raw.lower() in _NO_RESPONSE_TOKENS else challenge_raw[:200]
        description = _one_line(sections.get("公开描述", ""))
        if description:
            record.description = description[:300]
            record.status = "ok"
            return record

    # 2. JSON 兜底（try_parse_json 已内建截断修复）
    payload = try_parse_json(raw)
    if isinstance(payload, dict):
        description = _one_line(_first_str(payload, "公开描述", "description", "描述"))
        if description:
            record.description = description[:300]
            record.response = _normalize_response(_first_str(payload, "回应", "response"))
            record.self_identity = _parse_identity(
                _first_str(payload, "身份自评", "identity", "self_identity")
            )
            record.status = "ok"
            return record

    # 3. 行前缀启发式
    description = _one_line(_line_prefixed(raw, "公开描述", "描述"))
    if description:
        record.description = description[:300]
        record.response = _normalize_response(_line_prefixed(raw, "回应"))
        record.status = "ok"
        return record

    # 4. 无分节标记的短文本：按整段描述处理（兼容只回一句话的旧调用约定与测试桩）。
    #    长度上限是安全阀——超过就不可能是"一句话描述"，宁可判不合规也不发布原文。
    if raw and len(raw) <= _MAX_DESCRIPTION_CHARS:
        record.description = _one_line(raw)[:300]
        record.status = "legacy_plain"
        return record

    record.description = _SPEECH_MALFORMED_PLACEHOLDER
    record.raw_text = raw[:_RAW_TEXT_KEEP]
    record.status = "malformed"
    return record


class UndercoverGame:
    """多模型对抗游戏「谁是卧底」编排器。

    使用 LLMService.generate_multimodal_as / generate_as 精确调用每个玩家
    对应的具体模型，不走路由/降级链——每个玩家必须是它自己。
    """

    _MAX_REVOTE_ROUNDS = 3

    def __init__(
        self,
        config: ConfigBundle,
        *,
        image_a_path: str,
        image_b_path: str,
        players: Optional[List[tuple]] = None,
        max_vote_workers: int = 8,
        rng: Optional[random.Random] = None,
        seed: Optional[int] = None,
        order_mode: str = "rotate",
        max_rounds: int = 0,
        max_players: int = 0,
        spy_count: Optional[int] = None,
        on_event: Optional[Callable[[str, dict], None]] = None,
        advance_event: Optional[threading.Event] = None,
        cancel_event: Optional[threading.Event] = None,
        referee_model_id: str = "deepseek-flash-zz",
        referee_role: str = "base_model",
    ):
        """初始化游戏。

        Args:
            config: 配置包
            image_a_path: 图片 A 路径（平民看到的图）
            image_b_path: 图片 B 路径（卧底看到的图）
            players: 可选，[(role, model_id), ...] 显式指定参与模型；
                     None 时使用 LLMService 全部可用模型（base_model + adv_model）
            max_vote_workers: 投票阶段并行调用线程数上限
            rng: 可选，注入随机数生成器（测试用，控制卧底抽取与发言顺序的确定性）
            seed: 可选随机种子（rng 为 None 时生效；固定后可复现卧底与发言顺序）
            order_mode: 发言起点模式——rotate=每轮顺延一位 / fixed=整局固定
            max_rounds: 轮次上限；0 表示按玩家人数自动推导
            max_players: 参与人数上限；0 表示不截断（按配置优先级取前 N 个，实验控成本用）
            spy_count: 可选，显式指定卧底人数；None 时按人数阈值自动判定
            on_event: 可选事件回调 ``(event_type, payload)``；None 时为无操作。
                      回调在游戏线程内同步调用，实现应非阻塞（如 ``queue.Queue.put_nowait``）。
            advance_event: 可选手动步进锁。非 None 时，每轮结束后游戏阻塞等待该 Event
                           被 set（最长 300 秒超时后自动继续），以支持 Web UI 单轮控制。
            referee_model_id: 裁判模型 ID，用于增量校验和对局总结（默认 deepseek-flash-zz）。
            referee_role: 裁判模型角色（默认 base_model）。
        """
        if order_mode not in _ORDER_MODES:
            raise UndercoverGameError(
                f"未知的发言起点模式: {order_mode}（可选 {' / '.join(_ORDER_MODES)}）"
            )

        self._llm = LLMService(config)
        self._image_a_path = str(Path(image_a_path).expanduser().resolve())
        self._image_b_path = str(Path(image_b_path).expanduser().resolve())
        self._max_vote_workers = max_vote_workers
        self._seed = seed
        self._rng = rng if rng is not None else random.Random(seed)
        self._order_mode = order_mode
        self._max_rounds = max_rounds

        model_manager = self._llm.get_provider().get_model_manager()
        if players is None:
            players = []
            for role in ("base_model", "adv_model"):
                for model_id, _cfg in model_manager.get_models_by_priority(role):
                    players.append((role, model_id))

        # 去重必须在人数校验与卧底抽取之前：玩家以 "role/model_id" 为字典键，
        # 重复项会在建表时相互覆盖。若卧底恰好落在被覆盖的那一项上，最终无人
        # is_spy，run() 会拿不到卧底。
        unique_players: List[tuple] = []
        seen: set = set()
        for role, model_id in players:
            pair = (str(role), str(model_id))
            if pair in seen:
                logger.warning("忽略重复玩家: %s/%s", pair[0], pair[1])
                continue
            seen.add(pair)
            unique_players.append(pair)

        # 截断放在去重之后：否则重复项会占掉名额，实际参战人数少于用户预期。
        if max_players > 0:
            unique_players = unique_players[:max_players]

        if len(unique_players) < 3:
            raise UndercoverGameError(f"参与模型数需 ≥3（去重后），当前仅 {len(unique_players)}")

        total = len(unique_players)
        resolved_spy_count = (
            spy_count
            if spy_count is not None
            else (2 if total >= _DOUBLE_SPY_MIN_PLAYERS else 1)
        )
        # 卧底人数达到总人数一半时开局即满足「卧底≥平民」，游戏没有意义。
        if resolved_spy_count < 1 or resolved_spy_count * 2 >= total:
            raise UndercoverGameError(
                f"卧底人数需 ≥1 且小于总人数的一半，当前 {resolved_spy_count} 名 / {total} 人"
            )
        self._spy_count = resolved_spy_count

        spy_indices = set(self._rng.sample(range(total), resolved_spy_count))
        self._players: Dict[str, GamePlayer] = {}
        for idx, (role, model_id) in enumerate(unique_players):
            key = f"{role}/{model_id}"
            self._players[key] = GamePlayer(
                role=role, model_id=model_id, key=key,
                is_spy=(idx in spy_indices),
                display_name=key,
                player_number=idx + 1,  # 全局固定编号（1-indexed）
            )

        # 发言顺序基准序：必须在抽卧底**之后** shuffle。反过来会改变 rng 的消费序列，
        # 让按固定 rng 锁定卧底的测试与可复现性全部失效。
        self._speaking_order_base: List[str] = list(self._players.keys())
        self._rng.shuffle(self._speaking_order_base)

        self._image_a_data_url = _encode_image_data_url(self._image_a_path)
        self._image_b_data_url = _encode_image_data_url(self._image_b_path)

        self._on_event = on_event
        self._advance_event = advance_event
        self._cancel_event = cancel_event or threading.Event()
        self._referee_model_id = referee_model_id
        self._referee_role = referee_role
        # 描述阶段裁判校验时需要读取当前已完成轮次（_run_describe_phase 运行时注入）
        self._prior_rounds_for_check: List[RoundRecord] = []

    # ── 事件系统 ────────────────────────────────────────────────

    def _emit(self, event_type: str, payload: dict) -> None:
        """向外部观察者推送游戏事件。回调失败不中断游戏主流程。"""
        if self._on_event is not None:
            try:
                self._on_event(event_type, payload)
            except Exception:  # noqa: BLE001
                pass

    # ── 裁判增量校验 ─────────────────────────────────────────────

    def _check_description_increment(
        self,
        player: GamePlayer,
        new_speech: "SpeechRecord",
        prior_rounds: List["RoundRecord"],
        round_no: int,
    ) -> Tuple[bool, str]:
        """调用裁判模型判断新发言是否包含增量信息。

        Returns:
            (has_increment, reason) — has_increment=True 表示有增量，可接受；
            False 表示无增量，应要求玩家重新描述。若裁判调用失败则默认接受（True）。
        """
        if not self._referee_model_id:
            return True, ""

        # 收集该玩家所有历史轮次的公开描述和质疑
        prior_lines: List[str] = []
        for record in prior_rounds:
            speech = next((s for s in record.speeches if s.key == player.key), None)
            if speech is not None and speech.status != "api_error":
                line = f"第{record.round_no}轮：{speech.description}"
                if speech.challenge:
                    line += f"（质疑：{speech.challenge}）"
                prior_lines.append(line)

        if not prior_lines:
            # 没有历史记录，无法比较增量，直接接受
            return True, ""

        prompt = _REFEREE_INCREMENT_PROMPT.format(
            player_key=player.key,
            player_number=player.player_number,
            round_no=round_no,
            prior_descriptions="\n".join(prior_lines),
            new_description=new_speech.description,
            new_challenge=new_speech.challenge or "无",
        )
        try:
            gen = self._llm.generate_as(
                self._referee_role,
                self._referee_model_id,
                prompt,
                route_context={"task_type": "undercover_referee"},
            )
            text = gen.text.strip()
            # 解析 "增量：是/否"
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("增量") and ("：" in stripped or ":" in stripped):
                    value = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
                    has_increment = value.startswith("是")
                    # 提取理由
                    reason = ""
                    for rline in text.splitlines():
                        rs = rline.strip()
                        if rs.startswith("理由") and ("：" in rs or ":" in rs):
                            reason = rs.split("：", 1)[-1].split(":", 1)[-1].strip()
                            break
                    return has_increment, reason
            # 解析失败，默认接受
            logger.warning("裁判增量判定结果解析失败，默认接受。原始: %s", text[:100])
            return True, ""
        except Exception as exc:  # noqa: BLE001 — 裁判失败不阻断游戏
            logger.warning("裁判增量校验调用失败（%s），默认接受", exc)
            return True, ""

    # ── 公开 API ────────────────────────────────────────────────

    def _player_numbers(self) -> Dict[str, int]:
        """{key: player_number}：渲染层统一用它标注玩家。

        早先描述 prompt 用 player_number、历史块用发言位次，两套编号并存且会
        分叉（位次随轮转变化），模型说「N号」时无从判断指哪一个。
        """
        return {p.key: p.player_number for p in self._players.values()}

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise _GameCancelled("游戏已取消")

    def run(self) -> GameResult:
        """协作取消：已发出的网络请求等待超时，停止安排后续请求。"""
        try:
            return self._run_game()
        except _GameCancelled:
            result = self._partial_result
            active = getattr(self, "_active_round", None)
            if active is not None and active not in result.rounds:
                result.rounds.append(active)
            result.winner = "cancelled"
            result.final_survivors = [p.key for p in self._players.values() if p.alive]
            result.errors.append("cancelled: 用户取消")
            self._emit("game_end", {"winner": "cancelled", "final_survivors": result.final_survivors,
                                    "spy_keys": result.spy_keys, "total_rounds": len(result.rounds)})
            return result

    def _run_game(self) -> GameResult:
        """运行完整对局，返回结果。"""
        spy_keys = [p.key for p in self._players.values() if p.is_spy]
        result = GameResult(
            image_civilian=self._image_a_path,
            image_spy=self._image_b_path,
            players=list(self._players.keys()),
            spy_keys=spy_keys,
            spy_count=len(spy_keys),
            speaking_order_base=list(self._speaking_order_base),
            order_mode=self._order_mode,
            seed=self._seed,
        )

        self._partial_result = result
        self._check_cancelled()

        # 轮次上限：每轮最多淘汰 1 人。留出余量并强制封顶，防止「全员弃权→无人淘汰
        # →存活集合不变」时死循环（所有玩家调用失败或被熔断时会真实发生）。
        max_rounds = self._max_rounds or max(len(self._players) * 2, 4)
        result.max_rounds = max_rounds

        self._emit("game_start", {
            "players": [
                {
                    "key": p.key, "role": p.role, "model_id": p.model_id,
                    "is_spy": p.is_spy, "display_name": p.display_name,
                    "player_number": p.player_number,
                }
                for p in self._players.values()
            ],
            "spy_keys": spy_keys,
            "spy_count": len(spy_keys),
            "image_civilian": self._image_a_path,
            "image_spy": self._image_b_path,
            "seed": self._seed,
            "order_mode": self._order_mode,
            "max_rounds": max_rounds,
        })

        round_no = 0
        while round_no < max_rounds:
            self._check_cancelled()
            round_no += 1
            started_at = time.monotonic()
            alive_players = [p for p in self._players.values() if p.alive]

            record = RoundRecord(round_no=round_no)
            self._active_round = record

            speaking_order = self._round_order({p.key for p in alive_players}, round_no)
            self._emit("round_start", {
                "round_no": round_no,
                "speaking_order": speaking_order,
                "alive": [p.key for p in alive_players],
            })

            speeches = self._run_describe_phase(
                alive_players, result.rounds, round_no, result.errors,
            )
            record.speeches = speeches
            record.speaking_order = [s.key for s in speeches]
            record.descriptions = {s.key: s.description for s in speeches}
            record.responses = {s.key: s.response for s in speeches if s.response}
            record.silent = [s.key for s in speeches if s.status == "api_error"]
            record.malformed = [s.key for s in speeches if s.status == "malformed"]

            self._check_cancelled()
            outcome = self._run_vote_phase(
                alive_players, result.rounds, round_no, speeches, result.errors,
            )
            record.votes = outcome.votes
            record.vote_reasons = outcome.reasons
            record.initial_votes = outcome.initial_votes
            record.initial_vote_reasons = outcome.initial_reasons
            record.revote_rounds = outcome.revote_rounds

            tally = Counter(v for v in outcome.votes.values() if v)
            record.vote_tally = dict(tally)

            eliminated = self._resolve_elimination(tally)
            record.eliminated = eliminated
            if eliminated:
                self._players[eliminated].alive = False
                record.eliminated_was_spy = self._players[eliminated].is_spy
                self._emit("elimination", {
                    "round_no": round_no,
                    "eliminated": eliminated,
                    "was_spy": record.eliminated_was_spy,
                    "tally": dict(tally),
                })

            record.elapsed_sec = time.monotonic() - started_at
            result.rounds.append(record)

            self._emit("round_end", {
                "round_no": round_no,
                "elapsed_sec": record.elapsed_sec,
                "silent": record.silent,
                "malformed": record.malformed,
                "eliminated": eliminated,
                "tally": dict(tally),
            })

            # 每轮结束后发出 checkpoint 事件，外部存储层可据此落盘断点
            self._emit("checkpoint", {
                "round_no": round_no,
                "alive_keys": [p.key for p in self._players.values() if p.alive],
                "spy_keys": spy_keys,
                "speaking_order_base": list(self._speaking_order_base),
                "completed_rounds": len(result.rounds),
            })

            # 手动步进：等待外部 advance_event 后才继续下一轮（超时兜底防止窗口关闭后卡死）
            alive = [p for p in self._players.values() if p.alive]
            remaining_spies = sum(p.is_spy for p in alive)
            continuing = round_no < max_rounds and 0 < remaining_spies < len(alive) - remaining_spies
            if self._advance_event is not None and continuing:
                self._emit("round_waiting", {"round_no": round_no})
                deadline = time.monotonic() + 300
                while not self._advance_event.wait(timeout=0.2):
                    self._check_cancelled()
                    if time.monotonic() >= deadline:
                        break
                self._advance_event.clear()

            self._check_cancelled()
            survivors = [p for p in self._players.values() if p.alive]
            result.final_survivors = [p.key for p in survivors]

            spy_alive = sum(1 for p in survivors if p.is_spy)
            civ_alive = len(survivors) - spy_alive
            # 单卧底时「spy_alive >= civ_alive」等价于旧的「存活 ≤2 且卧底存活」，
            # 故这一判定对既有单卧底对局是严格兼容的推广。
            if spy_alive == 0:
                result.winner = "civilians"
                self._emit("game_end", {
                    "winner": "civilians",
                    "final_survivors": result.final_survivors,
                    "spy_keys": spy_keys,
                    "total_rounds": round_no,
                })
                return result
            if spy_alive >= civ_alive:
                result.winner = "spy"
                self._emit("game_end", {
                    "winner": "spy",
                    "final_survivors": result.final_survivors,
                    "spy_keys": spy_keys,
                    "total_rounds": round_no,
                })
                return result

        # 达到轮次上限仍未分出胜负：全员弃权导致无人被淘汰。
        # 记为 stalemate 而非谎报一方胜利，errors 里已有失败明细可供诊断。
        logger.warning("对局达到轮次上限 %d 仍未分出胜负，记为 stalemate", max_rounds)
        result.winner = "stalemate"
        result.errors.append(f"stalemate: 达到轮次上限 {max_rounds} 仍无人被淘汰")
        self._emit("game_end", {
            "winner": "stalemate",
            "final_survivors": result.final_survivors,
            "spy_keys": spy_keys,
            "total_rounds": round_no,
        })
        return result

    # ── 发言顺序 ────────────────────────────────────────────────

    def _round_order(self, alive_keys: set, round_no: int) -> List[str]:
        """返回本轮发言顺序：基准序整局不变，起点按轮次顺延。

        起点必须在**完整基准序**上计算再过滤死者。若改为在存活子集上算起点，
        每淘汰一人都会让全体玩家的相对位次漂移，轮转偏移量就失去意义了。
        """
        base = self._speaking_order_base
        total = len(base)
        if total == 0:
            return []
        start = 0 if self._order_mode == "fixed" else (round_no - 1) % total
        rotated = base[start:] + base[:start]
        return [key for key in rotated if key in alive_keys]

    # ── 描述阶段 ────────────────────────────────────────────────

    def _run_describe_phase(
        self,
        alive_players: List[GamePlayer],
        prior_rounds: List[RoundRecord],
        round_no: int,
        errors: List[str],
    ) -> List[SpeechRecord]:
        """按顺序调用存活玩家，每人可见本轮前面所有人的公开内容。

        顺序是刻意的：需求要求「每个玩家陈述前可以参考本轮前面所有人的陈述」，
        这既是卧底校准措辞的前提，也是平民观察从众效应的前提。
        """
        # 注入已完成轮次，供裁判增量校验使用
        self._prior_rounds_for_check = prior_rounds

        order = self._round_order({p.key for p in alive_players}, round_no)
        by_key = {p.key: p for p in alive_players}
        ordered_players = [by_key[key] for key in order]

        numbers = self._player_numbers()
        history_text = _format_history(prior_rounds, round_no, numbers)
        alive_count = len(ordered_players)

        def _one(player: GamePlayer, index: int, accumulated: List[SpeechRecord]) -> SpeechRecord:
            return self._describe_one(
                player,
                index,
                history_text=history_text,
                prior_speeches_text=_render_prior_speeches(accumulated, numbers),
                own_private_text=_render_private_history(prior_rounds, player.key),
                round_no=round_no,
                alive_count=alive_count,
                errors=errors,
            )

        return self._run_sequential(ordered_players, _one, errors=errors, phase="describe")

    def _run_sequential(
        self,
        ordered_players: List[GamePlayer],
        fn,
        *,
        errors: List[str],
        phase: str,
    ) -> List[SpeechRecord]:
        """按给定顺序逐个执行 fn(player, index, accumulated)，单点异常不中断对局。

        与 _run_parallel 同款的逐项兜底。注意失败时产出的是 `malformed`（而非
        `api_error`）：玩家确实「发过言」了，只是内容不合规，第 3 位玩家看到的是
        一条劣质发言而不是一个空洞——两者对后续推理的含义完全不同。

        Args:
            ordered_players: 已排好序的玩家
            fn: 接受 (player, index, accumulated) 返回 SpeechRecord
            errors: 错误记录列表（append-only）
            phase: 阶段名，用于错误信息标注

        Returns:
            与 ordered_players 等长的 SpeechRecord 列表
        """
        speeches: List[SpeechRecord] = []
        for index, player in enumerate(ordered_players):
            if self._cancel_event.is_set():
                break
            try:
                speeches.append(fn(player, index, speeches))
            except Exception as exc:  # noqa: BLE001 — 单个玩家异常不应中断对局
                logger.warning("玩家 %s 在 %s 阶段意外异常: %s", player.key, phase, exc)
                errors.append(f"phase={phase} player={player.key} unexpected_error={exc}")
                speeches.append(SpeechRecord(
                    key=player.key,
                    order_index=index,
                    description=_SPEECH_MALFORMED_PLACEHOLDER,
                    status="malformed",
                ))
        return speeches

    def _describe_one(
        self,
        player: GamePlayer,
        index: int,
        *,
        history_text: str,
        prior_speeches_text: str,
        own_private_text: str,
        round_no: int,
        alive_count: int,
        errors: List[str],
    ) -> SpeechRecord:
        """单个玩家的发言：同一份 prompt 发给所有人（卧底与平民逐字相同）。"""

        # Emit player_thinking event
        self._emit("player_thinking", {
            "round_no": round_no,
            "player_key": player.key,
            "player_number": player.player_number,
            "phase": "describe",
        })

        data_url = self._image_b_data_url if player.is_spy else self._image_a_data_url

        # 构造增量要求文本（第2轮起强制）
        incremental_requirement = ""
        if round_no >= 2:
            incremental_requirement = (
                "从第2轮开始，你的本轮描述必须包含前几轮未提及的新角度、新元素，"
                "或对其他玩家发起有据可查的质疑。不得重复已说过的内容。"
            )
        else:
            incremental_requirement = "（第1轮无此要求）"

        # 本局第一位发言者没有任何可比对的前置内容，说得越细越容易被立刻锁定
        if round_no == 1 and index == 0:
            opening_note = (
                "你是本局第一位发言的人，此前没有任何可比对的内容：\n"
                "先给出一两个可核对的中性锚点（人物、场景类别、背景），让别人能与你比对——\n"
                "整轮若没有人给出可核对的内容，同伴就无从判断谁最与众不同，你自己也拿不到参照。\n"
                "但不要展开最具体的辨识特征（精确颜色、款式、数量），也不要主动质疑他人；\n"
                "把具体细节与质疑都留到听过别人的描述之后再决定。"
            )
        else:
            opening_note = "（不适用：你不是本局第一位发言者）"

        prompt_text = _DESCRIBE_PROMPT.format(
            spy_count=self._spy_count,
            total_players=len(self._players),
            alive_count=alive_count,
            round_no=round_no,
            player_key=player.key,
            player_number=player.player_number,
            speaking_position=index + 1,
            history=history_text,
            prior_speeches=prior_speeches_text,
            own_private_history=own_private_text,
            incremental_requirement=incremental_requirement,
            opening_note=opening_note,
        )
        content_parts: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]

        # 第2轮起需要裁判校验增量，最多重试3次
        max_attempts = 3 if round_no >= 2 else 1
        prior_rounds = getattr(self, "_prior_rounds_for_check", [])
        speech: Optional[SpeechRecord] = None
        forced = False
        for attempt in range(1, max_attempts + 1):
            try:
                # max_tokens 不覆盖：交给模型自身配置（推理型模型如 deepseek-flash-zz
                # 需要在 max_tokens 预算内先跑完思维链再输出正文，硬编码小值会导致
                # finish_reason=length 而正文为空）。
                text = self._llm.generate_multimodal_as(
                    player.role, player.model_id, content_parts,
                    route_context={"task_type": "undercover_describe"},
                )
            except LLMProviderError as exc:
                logger.warning("玩家 %s 描述阶段调用失败: %s", player.key, exc)
                errors.append(f"round={round_no} phase=describe player={player.key} error={exc}")
                speech = SpeechRecord(
                    key=player.key,
                    order_index=index,
                    description=_DESCRIBE_FAILED_PLACEHOLDER,
                    status="api_error",
                )
                self._emit("player_speech", {
                    "round_no": round_no,
                    "key": player.key,
                    "player_number": player.player_number,
                    "public": {
                        "description": speech.description,
                        "response": speech.response,
                        "challenge": speech.challenge,
                        "status": speech.status,
                    },
                    "private": {},
                })
                return speech

            speech = _parse_speech(text, player.key, index)

            # 第2轮起校验增量（最后一次尝试不再校验，直接接受）
            if round_no >= 2 and attempt < max_attempts:
                has_increment, reason = self._check_description_increment(
                    player, speech, prior_rounds, round_no
                )
                if not has_increment:
                    logger.info(
                        "玩家 %s 第 %d 轮第 %d 次尝试无增量，裁判理由: %s",
                        player.key, round_no, attempt, reason,
                    )
                    # 带反馈重新生成（修改 content_parts[0] 的 text）
                    feedback = (
                        f"\n\n【裁判反馈（第{attempt}次）】"
                        f"你本轮的描述被判定为无增量信息。理由：{reason}\n"
                        f"请重新组织，提供前几轮未提及的新元素，或对其他玩家发起有据可查的质疑。"
                    )
                    content_parts[0] = {"type": "text", "text": prompt_text + feedback}
                    continue
                # 有增量，跳出循环
                break
            elif round_no >= 2 and attempt == max_attempts:
                # 最后一次尝试：无论是否有增量都接受，标记 forced
                has_increment, _ = self._check_description_increment(
                    player, speech, prior_rounds, round_no
                )
                forced = not has_increment

        self._emit("player_speech", {
            "round_no": round_no,
            "key": player.key,
            "player_number": player.player_number,
            "public": {
                "description": speech.description,  # type: ignore[union-attr]
                "response": speech.response,  # type: ignore[union-attr]
                "challenge": speech.challenge,  # type: ignore[union-attr]
                "status": speech.status,  # type: ignore[union-attr]
            },
            "private": {
                "observation_list": speech.observation_list,  # type: ignore[union-attr]
                "public_elements": speech.public_elements,  # type: ignore[union-attr]
                "withheld_elements": speech.withheld_elements,  # type: ignore[union-attr]
                "self_identity": speech.self_identity,  # type: ignore[union-attr]
                "self_confidence": speech.self_confidence,  # type: ignore[union-attr]
                "self_reason": speech.self_reason,  # type: ignore[union-attr]
            },
            "forced": forced,
        })
        return speech  # type: ignore[return-value]

    # ── 投票阶段 ────────────────────────────────────────────────

    def _run_vote_phase(
        self,
        alive_players: List[GamePlayer],
        prior_rounds: List[RoundRecord],
        round_no: int,
        round_speeches: List[SpeechRecord],
        errors: List[str],
    ) -> _VoteOutcome:
        """并行调用存活玩家投票；出现平票则在平票候选间限定重投。

        投票保持并行：描述此时已全部公开，投票者之间没有新信息流动，串行只增加
        墙钟时间而不产生博弈价值。

        重投至多 _MAX_REVOTE_ROUNDS 次。每次重投都把上一次的投票分布显式喂回
        prompt（见 _format_tie_note），否则模型只被告知候选范围、缺少「上次为何
        平票」的信息，会倾向重复同样的判断从而反复平票。

        Args:
            alive_players: 存活玩家列表（全部参与投票，含平票候选自己）
            prior_rounds: 已完成的轮次记录
            round_no: 当前轮次编号
            round_speeches: 本轮全部发言（尚未写入 RoundRecord）
            errors: 错误记录列表（append-only）

        Returns:
            _VoteOutcome：生效投票 + 首投留存 + 重投轨迹
        """
        candidate_keys = [p.key for p in alive_players]
        votes, reasons = self._collect_votes(
            alive_players, prior_rounds, round_no, round_speeches, candidate_keys, errors,
        )
        initial_votes, initial_reasons = dict(votes), dict(reasons)

        revote_log: List[Dict[str, Any]] = []
        tally = Counter(v for v in votes.values() if v)
        for attempt in range(1, self._MAX_REVOTE_ROUNDS + 1):
            if not tally:
                break
            top_count = max(tally.values())
            tied = [k for k, c in tally.items() if c == top_count]
            if len(tied) <= 1:
                break
            # 平票：候选范围限定为平票者，但全部存活玩家都参与重投
            revote_votes, revote_reasons = self._collect_votes(
                alive_players, prior_rounds, round_no, round_speeches, tied, errors,
                extra_note=_format_tie_note(attempt, tied, votes, self._player_numbers()),
            )
            revote_log.append({
                "attempt": attempt,
                "tied_candidates": tied,
                "votes": revote_votes,
                "vote_reasons": revote_reasons,
            })
            if not revote_votes:
                # 重投全员弃权：保留上一次有效投票，交给 _resolve_elimination
                # 在平票候选间随机裁决，而不是把有效票丢成空票（空票=无人淘汰=可能死循环）。
                logger.warning("第 %d 次平票重投全员弃权，保留上一次投票结果", attempt)
                break
            votes, reasons = revote_votes, revote_reasons
            tally = Counter(v for v in votes.values() if v)

        return _VoteOutcome(
            votes=votes,
            reasons=reasons,
            initial_votes=initial_votes,
            initial_reasons=initial_reasons,
            revote_rounds=revote_log,
        )

    def _collect_votes(
        self,
        alive_players: List[GamePlayer],
        prior_rounds: List[RoundRecord],
        round_no: int,
        round_speeches: List[SpeechRecord],
        candidate_keys: List[str],
        errors: List[str],
        *,
        extra_note: str = "",
    ) -> tuple:
        """并行收集一轮投票（可能是首投或平票重投）。

        每个玩家的可投候选会剔除自己（不能投自己）；若剔除后为空
        （平票候选只剩该玩家一人），则退回原候选集合，避免无票可投。

        Args:
            alive_players: 存活玩家列表（全部参与投票）
            prior_rounds: 已完成的轮次记录
            round_no: 当前轮次编号
            round_speeches: 本轮全部发言（拼入 history）
            candidate_keys: 本次可投候选（首投=全部存活；重投=平票候选）
            errors: 错误记录列表（append-only）
            extra_note: 平票重投的补充说明（见 _format_tie_note）

        Returns:
            (votes, reasons)：votes 只含有效票，reasons 含全部玩家（含弃权者）
        """
        numbers = self._player_numbers()
        history_text = _format_history(prior_rounds, round_no, numbers, current_speeches=round_speeches)
        alive_list_text = ", ".join(_player_label(p.key, numbers) for p in alive_players)
        silent = [s.key for s in round_speeches if s.status == "api_error"]
        silent_list_text = ", ".join(_player_label(k, numbers) for k in silent) if silent else "无"
        by_key = {p.key: p for p in alive_players}

        def _vote_one(player: GamePlayer) -> tuple:
            # Emit thinking event before calling LLM
            self._emit("player_thinking", {
                "round_no": round_no,
                "player_key": player.key,
                "player_number": player.player_number,
                "phase": "vote",
            })
            # 不能投自己：从候选中剔除自身；剔空则退回原集合。
            own_candidates = [k for k in candidate_keys if k != player.key] or list(candidate_keys)
            prompt_text = _VOTE_PROMPT.format(
                spy_count=self._spy_count,
                total_players=len(self._players),
                history=history_text,
                alive_list=alive_list_text,
                silent_list=silent_list_text,
                # 私有档案必须带进投票阶段——否则「判断自己是卧底→转移视线」
                # 这条链路在投票时就断掉了。
                own_private_history=_render_private_history(
                    prior_rounds, player.key, round_speeches,
                ),
            )
            if extra_note:
                prompt_text = f"{prompt_text}\n{extra_note}"
            prompt_text = (
                f"{prompt_text}\n你是 {player.key}，不能投自己。"
                f"只能投给以下候选之一：{', '.join(_player_label(k, numbers) for k in own_candidates)}"
            )
            try:
                # 同上：不覆盖 max_tokens，交给模型自身配置。
                gen_result = self._llm.generate_as(
                    player.role, player.model_id, prompt_text,
                    route_context={"task_type": "undercover_vote"},
                )
                target, reason = _parse_vote(gen_result.text, own_candidates, numbers)
                return player.key, (target, reason)
            except LLMProviderError as exc:
                logger.warning("玩家 %s 投票阶段调用失败: %s", player.key, exc)
                errors.append(f"round={round_no} phase=vote player={player.key} error={exc}")
                return player.key, (None, "（未能获取投票，本轮弃权）")

        raw = self._run_parallel(
            alive_players, _vote_one, self._max_vote_workers,
            fallback=(None, "（未能获取投票，本轮弃权）"), errors=errors, phase="vote",
        )
        votes = {pid: target_reason[0] for pid, target_reason in raw.items() if target_reason[0]}
        reasons = {pid: target_reason[1] for pid, target_reason in raw.items()}
        # 逐票 emit（仅首投 / 重投中第一次调用 _collect_votes 时有实际观察价值）
        for pid, (target, reason) in raw.items():
            player = by_key.get(pid)
            self._emit("vote_cast", {
                "round_no": round_no,
                "key": pid,
                "player_number": player.player_number if player else 0,
                "target": target,
                "reason": reason,
            })
        return votes, reasons

    # ── 计票 ────────────────────────────────────────────────────

    def _resolve_elimination(self, tally: Counter) -> Optional[str]:
        """根据计票结果确定淘汰者。

        平票重投耗尽后在平票候选间随机裁决（显式记录于日志），保证每轮必有人
        被淘汰、对局能收敛。tally 为空（全员弃权）时返回 None，由 run() 的轮次
        上限兜底。

        Args:
            tally: {candidate_key: 票数}

        Returns:
            被淘汰玩家 key；无有效票时为 None
        """
        if not tally:
            return None
        top_count = max(tally.values())
        tied = [k for k, c in tally.items() if c == top_count]
        if len(tied) == 1:
            return tied[0]
        chosen = self._rng.choice(tied)
        logger.warning("平票重投耗尽仍未分出胜负，随机淘汰: %s（候选: %s）", chosen, tied)
        return chosen

    # ── 并行调用辅助 ──────────────────────────────────────────────

    def _run_parallel(
        self,
        players: List[GamePlayer],
        fn,
        max_workers: int,
        *,
        fallback: Any,
        errors: List[str],
        phase: str,
    ) -> Dict[str, Any]:
        """在共享线程池中并行调用 fn(player)，返回 {player.key: fn结果第二项}。

        fn 内部只捕获 LLMProviderError；其余异常（解析越界、配置缺字段等）若在
        future.result() 处直接抛出会中断整个对局并丢掉已完成玩家的结果。这里逐个
        future 兜底：异常玩家记为 fallback（等价弃权）并写入 errors，其余玩家不受影响。

        Args:
            players: 参与本次并行调用的玩家
            fn: 接受 GamePlayer、返回 (player_key, value) 的可调用对象
            max_workers: 线程数上限
            fallback: 玩家意外失败时填充的占位值
            errors: 错误记录列表（append-only）
            phase: 阶段名，用于错误信息标注

        Returns:
            {player_key: value}，必然覆盖全部 players
        """
        results: Dict[str, Any] = {}
        def guarded(player):
            if self._cancel_event.is_set():
                return player.key, fallback
            return fn(player)
        with shared_pool.executor(max_workers=min(max_workers, max(len(players), 1))) as executor:
            futures = {executor.submit(guarded, p): p for p in players}
            for future in as_completed(futures):
                player = futures[future]
                try:
                    pid, value = future.result()
                except Exception as exc:  # noqa: BLE001 — 单个玩家异常不应中断对局
                    logger.warning("玩家 %s 在 %s 阶段意外异常: %s", player.key, phase, exc)
                    errors.append(f"phase={phase} player={player.key} unexpected_error={exc}")
                    pid, value = player.key, fallback
                results[pid] = value
        return results


def _encode_image_data_url(path: str) -> str:
    """读取图片文件并编码为 base64 data URL（复用 complex_input 的编码逻辑约定）。"""
    from iris.utils.constants import IMAGE_MIME_MAP

    p = Path(path)
    if not p.exists():
        raise UndercoverGameError(f"图片文件不存在: {path}")
    ext = p.suffix.lower()
    mime = IMAGE_MIME_MAP.get(ext)
    if mime is None:
        raise UndercoverGameError(f"不支持的图片格式: {ext}")

    import base64
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"
