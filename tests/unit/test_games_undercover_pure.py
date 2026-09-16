"""谁是卧底——纯逻辑测试：解析器 / 渲染器 / 顺序机制 / prompt 契约 / schema 兼容。

这些用例不需要真实 LLM，也不碰文件系统。放在 tests/unit/ 下会被 conftest
自动打 unit 标记，进快速门禁；涉及编排流程的用例在 tests/test_games_undercover.py。
"""

import random
from unittest.mock import MagicMock, patch

import pytest

from iris.games.undercover import (
    _DESCRIBE_FAILED_PLACEHOLDER,
    _DESCRIBE_PROMPT,
    _SPEECH_MALFORMED_PLACEHOLDER,
    _VOTE_PROMPT,
    GameResult,
    RoundRecord,
    SpeechRecord,
    UndercoverGame,
    UndercoverGameError,
    _format_history,
    _parse_confidence,
    _parse_identity,
    _parse_observation_list,
    _parse_speech,
    _render_prior_speeches,
    _render_private_history,
)


def _make_game(players=None, rng=None, **kwargs):
    """构造一个 LLM 全 mock 的游戏实例（与集成测试同款桩）。"""
    mock_llm = MagicMock()
    mock_llm.get_provider.return_value.get_model_manager.return_value = MagicMock()
    with patch("iris.games.undercover._encode_image_data_url", return_value="data:image/png;base64,fake"), \
         patch("iris.games.undercover.LLMService", return_value=mock_llm):
        game = UndercoverGame(
            MagicMock(),
            image_a_path="/tmp/a.png",
            image_b_path="/tmp/b.png",
            players=players or [("base_model", f"m{i}") for i in range(1, 4)],
            rng=rng,
            **kwargs,
        )
    return game, mock_llm


def _players(count: int):
    return [("base_model", f"m{i}") for i in range(1, count + 1)]


# ── 解析：身份自评 ────────────────────────────────────────────────


class TestParseIdentity:
    """身份自评解析——两类陷阱都会导致误判，必须逐个钉死。"""

    def test_exact_label(self):
        assert _parse_identity("平民") == "civilian"
        assert _parse_identity("卧底") == "spy"
        assert _parse_identity("不确定") == "uncertain"

    def test_first_line_exact_label(self):
        assert _parse_identity("卧底\n因为多数人提到的元素我都没看到") == "spy"

    def test_negation_means_civilian(self):
        """「我不是卧底」含「卧底」二字，朴素 in 判断会误判成 spy。"""
        assert _parse_identity("我不是卧底") == "civilian"

    def test_qingwen_structure_is_not_negation(self):
        """「我不确定自己是不是卧底」里的「不是卧底」是疑问结构的一部分，
        不能被当成否定判断读成平民。"""
        assert _parse_identity("我不确定自己是不是卧底") == "uncertain"

    def test_plain_declaration(self):
        assert _parse_identity("我是平民") == "civilian"
        assert _parse_identity("确定为卧底") == "spy"

    def test_unknown_text_defaults_to_uncertain(self):
        assert _parse_identity("证据不足") == "uncertain"
        assert _parse_identity("") == "uncertain"

    def test_confidence_clamped(self):
        assert _parse_confidence("85") == 85
        assert _parse_confidence("置信度 120") == 100
        assert _parse_confidence("") == 0
        assert _parse_confidence("不好说") == 0


# ── 解析：观察清单 ────────────────────────────────────────────────


class TestParseObservationList:
    def test_splits_public_and_withheld(self):
        body = "- 雨滴 (公开)\n- 蓝色卡车（保留）\n- 建筑"
        items, public, withheld = _parse_observation_list(body)
        assert items == ["雨滴", "蓝色卡车", "建筑"]
        assert public == ["雨滴"]
        assert withheld == ["蓝色卡车"]

    def test_strips_numbered_prefix(self):
        items, _, _ = _parse_observation_list("1. 雨滴\n2) 建筑\n3、草地")
        assert items == ["雨滴", "建筑", "草地"]

    def test_unlabelled_items_count_in_neither_bucket(self):
        items, public, withheld = _parse_observation_list("- 雨滴")
        assert items == ["雨滴"]
        assert public == [] and withheld == []


# ── 解析：整条发言 ────────────────────────────────────────────────


_FULL_SPEECH = """【观察清单】
- 雨滴 (公开)
- 蓝色卡车 (保留)
- 建筑 (公开)
【身份自评】卧底
【置信度】80
【判断依据】多数人提到的建筑我看到的不一样
【公开描述】透过雨滴玻璃可见建筑与道路，色调灰冷
【回应】前排说的护栏我这边没注意到"""


class TestParseSpeech:
    def test_full_sections(self):
        rec = _parse_speech(_FULL_SPEECH, "base_model/m1", 3)
        assert rec.status == "ok"
        assert rec.description == "透过雨滴玻璃可见建筑与道路，色调灰冷"
        assert rec.observation_list == ["雨滴", "蓝色卡车", "建筑"]
        assert rec.public_elements == ["雨滴", "建筑"]
        assert rec.withheld_elements == ["蓝色卡车"]
        assert rec.self_identity == "spy"
        assert rec.self_confidence == 80
        assert rec.response == "前排说的护栏我这边没注意到"
        assert rec.order_index == 3
        assert rec.key == "base_model/m1"

    def test_json_fallback(self):
        text = '```json\n{"公开描述": "雨天玻璃", "回应": "无", "身份自评": "平民"}\n```'
        rec = _parse_speech(text, "k", 0)
        assert rec.status == "ok"
        assert rec.description == "雨天玻璃"
        assert rec.response == ""
        assert rec.self_identity == "civilian"

    def test_line_prefix_fallback(self):
        rec = _parse_speech("公开描述：雨滴玻璃窗外\n回应：无", "k", 0)
        assert rec.status == "ok"
        assert rec.description == "雨滴玻璃窗外"
        assert rec.response == ""

    def test_short_unstructured_text_treated_as_description(self):
        """兼容只回一句话的旧调用约定与测试桩。"""
        rec = _parse_speech("描述文本", "k", 0)
        assert rec.status == "legacy_plain"
        assert rec.description == "描述文本"

    def test_long_unstructured_text_is_not_published(self):
        """超长无结构文本不可能是「一句话描述」，宁可判不合规也不整段发布。"""
        rec = _parse_speech("这是一段没有任何分节标记的长文本" * 20, "k", 0)
        assert rec.status == "malformed"
        assert rec.description == _SPEECH_MALFORMED_PLACEHOLDER

    def test_parse_failure_keeps_raw_text_private(self):
        """安全红线：解析失败时原文可能整段是私有推理，绝不能泄漏成公开描述。"""
        secret = "ZZMARKER 我的私有推理 " + "内容" * 100
        rec = _parse_speech(secret, "k", 0)
        assert rec.status == "malformed"
        assert "ZZMARKER" not in rec.description
        assert rec.description == _SPEECH_MALFORMED_PLACEHOLDER
        assert "ZZMARKER" in rec.raw_text

    def test_raw_text_truncated(self):
        rec = _parse_speech("X" * 5000, "k", 0)
        assert len(rec.raw_text) <= 800


# ── 渲染 ──────────────────────────────────────────────────────────


class TestRenderPriorSpeeches:
    def test_empty_gives_no_prior_hint(self):
        assert "还没有人给出可参考" in _render_prior_speeches([])

    def test_excludes_api_error_speakers(self):
        """技术性沉默不该以「本轮弃权」的刺眼形态出现在轮内比对块里。"""
        silent = SpeechRecord(
            key="p1", order_index=0,
            description=_DESCRIBE_FAILED_PLACEHOLDER, status="api_error",
        )
        assert "还没有人给出可参考" in _render_prior_speeches([silent])

    def test_includes_malformed_speakers(self):
        """输出不合规必须留在比对块里——否则「装死」就是卧底的严格优势策略。"""
        broken = SpeechRecord(
            key="p1", order_index=0,
            description=_SPEECH_MALFORMED_PLACEHOLDER, status="malformed",
        )
        rendered = _render_prior_speeches([broken])
        assert "格式不合规" in rendered

    def test_renders_position_and_response(self):
        speech = SpeechRecord(key="p1", order_index=4, description="雨天玻璃", response="我这边也有建筑")
        rendered = _render_prior_speeches([speech])
        assert "5. p1: 雨天玻璃" in rendered
        assert "└ 回应: 我这边也有建筑" in rendered


class TestRenderPrivateHistory:
    def test_empty_gives_hint(self):
        assert "第一轮" in _render_private_history([], "p1")

    def test_includes_own_previous_record(self):
        rec = RoundRecord(round_no=1)
        rec.speeches = [SpeechRecord(
            key="p1", order_index=0, description="雨天玻璃",
            observation_list=["雨滴"], withheld_elements=["蓝车"],
            self_identity="spy", self_confidence=70, self_reason="多数人没提蓝车",
        )]
        rec.descriptions = {"p1": "雨天玻璃"}
        rendered = _render_private_history([rec], "p1")
        assert "第1轮 你的档案" in rendered
        assert "你的身份自评: 卧底（置信度 70）" in rendered
        assert "你当时打算保留: 蓝车" in rendered

    def test_other_players_records_not_included(self):
        rec = RoundRecord(round_no=1)
        rec.speeches = [SpeechRecord(key="p2", order_index=0, description="X")]
        assert "第一轮" in _render_private_history([rec], "p1")


class TestFormatHistory:
    def test_empty(self):
        assert "暂无历史" in _format_history([], 1)

    def test_current_round_label_is_one_based(self):
        """回归：当前块曾因传 round_no-1 被标成「第0轮」。"""
        result = _format_history([], 1, current_speeches=[SpeechRecord(key="p1", order_index=0, description="A")])
        assert "[第1轮描述]" in result
        assert "[第0轮" not in result

    def test_renders_by_speaking_order_not_dict_order(self):
        rec = RoundRecord(round_no=1)
        rec.speaking_order = ["p2", "p1"]
        rec.descriptions = {"p1": "描述A", "p2": "描述B"}
        result = _format_history([rec], 2)
        assert result.index("p2: 描述B") < result.index("p1: 描述A")

    def test_falls_back_to_descriptions_without_speeches(self):
        """v1 记录没有 speaking_order 时应回落到 dict 顺序。"""
        rec = RoundRecord(round_no=1)
        rec.descriptions = {"p1": "描述A", "p2": "描述B"}
        result = _format_history([rec], 2)
        assert "p1: 描述A" in result and "p2: 描述B" in result

    def test_silent_block_is_neutral(self):
        rec = RoundRecord(round_no=1)
        rec.descriptions = {"p1": "描述A"}
        rec.silent = ["p9"]
        result = _format_history([rec], 2)
        assert "[第1轮未发言] p9" in result
        assert "不参与比对" in result

    def test_elimination_result_does_not_reveal_identity(self):
        """淘汰反馈不得公布被淘汰者身份——哪怕他确实是卧底。

        `eliminated_was_spy` 只供复盘分析；一旦渲染进 prompt，平民每轮都能
        白嫖一条「死的是不是卧底」的硬信息。用「淘汰卧底 / 淘汰平民」两种记录
        渲染出完全相同的结果行来证明它没有泄漏。
        """
        def render(was_spy: bool) -> str:
            rec = RoundRecord(round_no=1)
            rec.descriptions = {"p1": "描述A"}
            rec.eliminated = "p1"
            rec.eliminated_was_spy = was_spy
            return _format_history([rec], 2).split("[第1轮结果]")[1].splitlines()[0]

        assert render(True) == render(False)
        assert "p1 被淘汰出局" in render(True)
        assert "场上仍有卧底存活" in render(True)


# ── 卧底人数与顺序机制 ────────────────────────────────────────────


class TestSpyCount:
    def test_eight_players_gets_two_spies(self):
        game, _ = _make_game(players=_players(8), rng=random.Random(42))
        assert game._spy_count == 2
        assert sum(1 for p in game._players.values() if p.is_spy) == 2

    def test_seven_players_gets_one_spy(self):
        game, _ = _make_game(players=_players(7), rng=random.Random(42))
        assert game._spy_count == 1
        assert sum(1 for p in game._players.values() if p.is_spy) == 1

    def test_explicit_spy_count_overrides_threshold(self):
        game, _ = _make_game(players=_players(9), rng=random.Random(1), spy_count=1)
        assert game._spy_count == 1

    def test_spy_count_at_half_of_players_rejected(self):
        """卧底达到总人数一半时开局即满足卧底胜，游戏没有意义。"""
        with pytest.raises(UndercoverGameError, match="小于总人数的一半"):
            _make_game(players=_players(4), spy_count=2)

    def test_unknown_order_mode_rejected(self):
        with pytest.raises(UndercoverGameError, match="未知的发言起点模式"):
            _make_game(players=_players(3), order_mode="bogus")

    def test_max_players_truncates(self):
        game, _ = _make_game(players=_players(6), max_players=4, rng=random.Random(1))
        assert len(game._players) == 4

    def test_max_players_applied_after_dedup(self):
        """截断若发生在去重之前，重复项会占掉名额，实际参战人数少于预期。"""
        players = [
            ("base_model", "m1"), ("base_model", "m1"),
            ("base_model", "m2"), ("base_model", "m3"),
        ]
        game, _ = _make_game(players=players, max_players=3, rng=random.Random(1))
        assert len(game._players) == 3


class TestSpeakingOrder:
    def test_base_order_contains_each_player_once(self):
        game, _ = _make_game(players=_players(9), rng=random.Random(7))
        assert sorted(game._speaking_order_base) == sorted(game._players.keys())

    def test_base_order_is_stable_within_a_game(self):
        game, _ = _make_game(players=_players(9), rng=random.Random(7))
        before = list(game._speaking_order_base)
        game._round_order(set(game._players.keys()), 1)
        game._round_order(set(game._players.keys()), 2)
        assert game._speaking_order_base == before

    def test_same_seed_reproduces_order_and_spies(self):
        players = _players(9)
        first, _ = _make_game(players=players, rng=random.Random(11))
        second, _ = _make_game(players=players, rng=random.Random(11))
        assert first._speaking_order_base == second._speaking_order_base
        assert (
            [p.key for p in first._players.values() if p.is_spy]
            == [p.key for p in second._players.values() if p.is_spy]
        )

    def test_round_two_starts_at_next_position(self):
        game, _ = _make_game(players=_players(5), rng=random.Random(3))
        alive = set(game._players.keys())
        base = game._speaking_order_base
        assert game._round_order(alive, 1) == base
        assert game._round_order(alive, 2) == base[1:] + base[:1]

    def test_rotation_wraps_around(self):
        game, _ = _make_game(players=_players(4), rng=random.Random(3))
        alive = set(game._players.keys())
        base = game._speaking_order_base
        # 第 4 轮起点 = base[3]，第 5 轮回绕到 base[0]
        assert game._round_order(alive, 4) == base[3:] + base[:3]
        assert game._round_order(alive, 5) == base

    def test_fixed_mode_never_rotates(self):
        game, _ = _make_game(players=_players(5), rng=random.Random(3), order_mode="fixed")
        alive = set(game._players.keys())
        base = game._speaking_order_base
        assert game._round_order(alive, 1) == base
        assert game._round_order(alive, 3) == base

    def test_dead_players_removed_but_rotation_offset_preserved(self):
        """起点必须在完整基准序上算再过滤死者。

        若改成在存活子集上算起点，每淘汰一人都会让全体相对位次漂移，
        轮转偏移量失去意义——这是最容易写错的一处。
        """
        game, _ = _make_game(players=_players(4), rng=random.Random(3))
        base = game._speaking_order_base
        alive = set(base) - {base[0]}   # base[0] 在上一轮被淘汰
        assert game._round_order(alive, 2) == [k for k in base[1:] + base[:1] if k in alive]
        # 不能退化成「存活子集的第 1 位」
        assert game._round_order(alive, 2)[0] != base[1] or base[1] in alive


# ── prompt 契约 ───────────────────────────────────────────────────


class TestPromptContracts:
    """prompt 里的硬约束是需求能否成立的唯一保障，逐条钉死。"""

    _DESCRIBE_VARS = {
        "spy_count": 2, "total_players": 8, "alive_count": 8, "round_no": 1,
        "player_key": "base_model/m1", "player_number": 3, "speaking_position": 3,
        "history": "H", "prior_speeches": "P", "own_private_history": "O",
        "incremental_requirement": "R",
    }
    _VOTE_VARS = {
        "spy_count": 2, "total_players": 8, "history": "H",
        "alive_list": "A", "silent_list": "无", "own_private_history": "O",
    }

    def test_describe_prompt_formats_without_leftover_braces(self):
        rendered = _DESCRIBE_PROMPT.format(**self._DESCRIBE_VARS)
        assert "{" not in rendered and "}" not in rendered

    def test_vote_prompt_formats_without_leftover_braces(self):
        rendered = _VOTE_PROMPT.format(**self._VOTE_VARS)
        assert "{" not in rendered and "}" not in rendered

    def test_describe_prompt_is_identical_for_spy_and_civilian(self):
        """需求「事先不知道自己身份」的守门测试。

        prompt 模板对全部玩家逐字相同，身份差异只体现在喂进去的图片上——
        模板内部不存在任何按 is_spy 分叉的占位符。
        """
        for token in ("is_spy", "你是卧底", "spy_key", "你的身份是"):
            assert token not in _DESCRIBE_PROMPT

    def test_describe_prompt_forbids_fabrication_and_denial(self):
        assert "严禁为了隐藏而写下你图中并不存在的元素" in _DESCRIBE_PROMPT
        assert "严禁否认你实际看到的元素" in _DESCRIBE_PROMPT

    def test_describe_prompt_forbids_identity_leak_and_coordination(self):
        assert "严禁在公开内容中透露或暗示你是平民还是卧底" in _DESCRIBE_PROMPT
        assert "严禁试图与其他玩家建立任何联络" in _DESCRIBE_PROMPT

    def test_vote_prompt_forbids_identity_leak_and_coordination(self):
        assert "不要试图与其他玩家建立联络" in _VOTE_PROMPT

    def test_describe_prompt_contains_concealment_tactics(self):
        for tactic in ("取舍", "概括", "借用共享词", "措辞模糊化"):
            assert tactic in _DESCRIBE_PROMPT

    def test_borrowed_words_restricted_to_own_image(self):
        assert "只有当这个词在你自己的图里也成立时才能用" in _DESCRIBE_PROMPT

    def test_private_section_precedes_public_in_format_spec(self):
        """私有清单必须先于公开描述生成——顺序是载荷性的。

        自回归生成下，先写清单再写描述，清单才会成为描述的约束；反过来模型会
        倒推出「只写安全元素」的清单，审计价值归零。
        """
        spec = _DESCRIBE_PROMPT.split("【输出格式】", 1)[1]
        assert spec.index("观察清单") < spec.index("公开描述")

    def test_vote_prompt_does_not_expose_identity_self_assessment(self):
        """私有面不外泄：投票 prompt 不得出现身份自评字段名。"""
        assert "身份自评" not in _VOTE_PROMPT
        assert "置信度" not in _VOTE_PROMPT

    def test_vote_prompt_tells_position_is_not_evidence(self):
        assert "发言位次本身不是判据" in _VOTE_PROMPT

    def test_vote_prompt_tells_silent_players_are_not_evidence(self):
        assert "因系统故障未发言的玩家不参与比对" in _VOTE_PROMPT

    def test_prompts_state_double_spy_win_condition(self):
        assert "所有卧底都被投出" in _DESCRIBE_PROMPT
        assert "卧底人数不少于平民人数" in _DESCRIBE_PROMPT


# ── schema 兼容 ───────────────────────────────────────────────────


class TestSchemaCompat:
    """对局 JSON 是复盘输入，格式演进必须是纯追加。"""

    _LEGACY_ROUND_KEYS = (
        "round_no", "descriptions", "votes", "vote_reasons", "vote_tally",
        "initial_votes", "initial_vote_reasons", "eliminated", "revote_rounds",
    )
    _LEGACY_RESULT_KEYS = (
        "image_civilian", "image_spy", "spy_key", "players", "rounds",
        "winner", "final_survivors", "errors",
    )

    def test_round_dict_keeps_legacy_keys_and_types(self):
        data = RoundRecord(round_no=1).to_dict()
        for key in self._LEGACY_ROUND_KEYS:
            assert key in data, key
        assert isinstance(data["descriptions"], dict)
        assert isinstance(data["revote_rounds"], list)

    def test_game_result_dict_v1_keys_stay_first(self):
        result = GameResult(image_civilian="a", image_spy="b", players=["p1"])
        data = result.to_dict()
        assert list(data.keys())[: len(self._LEGACY_RESULT_KEYS)] == list(self._LEGACY_RESULT_KEYS)
        for key in self._LEGACY_RESULT_KEYS:
            assert key in data

    def test_schema_version_is_two(self):
        assert GameResult(image_civilian="a", image_spy="b", players=[]).schema_version == 2

    def test_spy_key_is_first_spy_alias(self):
        result = GameResult(image_civilian="a", image_spy="b", players=[], spy_keys=["s1", "s2"])
        assert result.spy_key == "s1"
        assert result.to_dict()["spy_key"] == "s1"
        assert result.to_dict()["spy_keys"] == ["s1", "s2"]

    def test_spy_key_empty_when_no_spies(self):
        assert GameResult(image_civilian="a", image_spy="b", players=[]).spy_key == ""

    def test_speech_dict_round_trips(self):
        speech = SpeechRecord(
            key="p1", order_index=2, description="d", response="r",
            observation_list=["a"], withheld_elements=["b"],
            self_identity="spy", self_confidence=60, status="ok",
        )
        data = speech.to_dict()
        assert data["order_index"] == 2
        assert data["withheld_elements"] == ["b"]
        assert data["self_identity"] == "spy"
