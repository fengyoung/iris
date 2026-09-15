"""测试 games.undercover — 多模型对抗游戏逻辑。"""

import random
from collections import Counter
from unittest.mock import MagicMock, patch

import pytest

from iris.games.undercover import (
    _DESCRIBE_FAILED_PLACEHOLDER,
    GamePlayer,
    RoundRecord,
    UndercoverGame,
    UndercoverGameError,
    _format_history,
    _format_tie_note,
    _parse_vote,
)
from iris.llm import LLMProviderError


def _make_game(players=None, rng=None, llm=None, **kwargs):
    """构造一个 LLM 全 mock 的游戏实例。"""
    mock_llm = llm or MagicMock()
    mock_llm.get_provider.return_value.get_model_manager.return_value = MagicMock()
    with patch("iris.games.undercover._encode_image_data_url", return_value="data:image/png;base64,fake"), \
         patch("iris.games.undercover.LLMService", return_value=mock_llm):
        game = UndercoverGame(
            MagicMock(),
            image_a_path="/tmp/a.png",
            image_b_path="/tmp/b.png",
            players=players or [("base_model", "m1"), ("base_model", "m2"), ("base_model", "m3")],
            rng=rng,
            **kwargs,
        )
    return game, mock_llm


def _players(count: int):
    """生成 count 个 base_model 玩家（≥8 人会自动抽 2 名卧底）。"""
    return [("base_model", f"m{i}") for i in range(1, count + 1)]


def _capture_prompts(mock_llm, game, describe_reply, vote_picker):
    """挂上会记录 prompt 的 mock，返回按调用顺序记录的 (phase, owner_key, text) 列表。

    vote_picker(candidates, voter) 返回该玩家要投的 key；返回 None 表示弃权。
    """
    prompts = []

    def mm_side_effect(role, model_id, parts, **kwargs):
        key = f"{role}/{model_id}"
        prompts.append(("describe", key, parts[0]["text"]))
        return describe_reply(key)

    def gen_side_effect(role, model_id, prompt, **kwargs):
        key = f"{role}/{model_id}"
        prompts.append(("vote", key, prompt))
        candidates = prompt.split("只能投给以下候选之一：", 1)[1].strip().split(", ")
        target = vote_picker(candidates, key, prompt)
        result = MagicMock()
        result.text = f"投票：{target}\n理由：理由-{key}" if target else "我不知道投给谁"
        return result

    mock_llm.generate_multimodal_as.side_effect = mm_side_effect
    mock_llm.generate_as.side_effect = gen_side_effect
    return prompts


class TestParseVote:
    """测试投票输出解析。"""

    def test_standard_format(self):
        text = "投票：base_model/deepseek-flash\n理由：描述差异明显"
        target, reason = _parse_vote(text, ["base_model/deepseek-flash", "adv_model/qwen3.8-max"])
        assert target == "base_model/deepseek-flash"
        assert reason == "描述差异明显"

    def test_colon_variant(self):
        text = "投票:adv_model/qwen3.8-max\n理由:怀疑是卧底"
        target, reason = _parse_vote(text, ["base_model/deepseek-flash", "adv_model/qwen3.8-max"])
        assert target == "adv_model/qwen3.8-max"
        assert reason == "怀疑是卧底"

    def test_loose_match(self):
        text = "我认为 adv_model/qwen3.8-max 最可疑，因为描述与大家不同"
        target, _ = _parse_vote(text, ["base_model/deepseek-flash", "adv_model/qwen3.8-max"])
        assert target == "adv_model/qwen3.8-max"

    def test_invalid_returns_none(self):
        text = "我不知道投给谁"
        target, reason = _parse_vote(text, ["base_model/deepseek-flash"])
        assert target is None
        assert "不知道" in reason

    def test_prefix_ambiguity_prefers_longest_key(self):
        """key 互为前缀时必须命中更长的那个（m10 不能被误判成 m1）。"""
        keys = ["base_model/m1", "base_model/m10"]
        target, _ = _parse_vote("投票：base_model/m10\n理由：描述离群", keys)
        assert target == "base_model/m10"

    def test_vote_line_takes_priority_over_body(self):
        """投票行有明确目标时，不应被正文里提到的其他 key 抢走。"""
        keys = ["base_model/m1", "base_model/m2"]
        text = "投票：base_model/m2\n理由：base_model/m1 的描述反而很正常"
        target, _ = _parse_vote(text, keys)
        assert target == "base_model/m2"

    def test_falls_back_to_body_when_vote_line_has_no_key(self):
        keys = ["base_model/m1", "base_model/m2"]
        text = "投票：不确定\n我倾向 base_model/m1"
        target, _ = _parse_vote(text, keys)
        assert target == "base_model/m1"


class TestFormatHistory:
    """测试历史记录格式化。"""

    def test_empty_history(self):
        assert "暂无历史" in _format_history([], 1)

    def test_with_rounds(self):
        r1 = RoundRecord(round_no=1)
        r1.descriptions = {"p1": "描述A", "p2": "描述B"}
        r1.votes = {"p1": "p2"}
        r1.vote_reasons = {"p1": "怀疑"}
        r1.eliminated = "p2"
        result = _format_history([r1], 2)
        assert "[第1轮描述]" in result
        assert "p1: 描述A" in result
        assert "[第1轮投票]" in result
        assert "p2 被淘汰" in result


class TestFormatTieNote:
    """测试平票重投补充说明——必须把上一次投票分布喂回模型。"""

    def test_includes_previous_vote_distribution(self):
        note = _format_tie_note(1, ["p1", "p2"], {"p3": "p1", "p4": "p2"})
        assert "第 1 次平票重投" in note
        assert "p3 投给 p1" in note
        assert "p4 投给 p2" in note
        assert "p1, p2" in note

    def test_handles_empty_previous_votes(self):
        note = _format_tie_note(2, ["p1", "p2"], {})
        assert "第 2 次平票重投" in note
        assert "p1, p2" in note


class TestGamePlayer:
    def test_construct(self):
        p = GamePlayer(role="base_model", model_id="test", key="base_model/test", is_spy=True)
        assert p.role == "base_model"
        assert p.is_spy
        assert p.alive


class TestUndercoverGameInit:
    """测试游戏初始化。"""

    def test_init_with_explicit_players(self):
        game, _ = _make_game(
            players=[("base_model", "m1"), ("base_model", "m2"), ("adv_model", "m3")],
            rng=random.Random(42),
        )
        assert len(game._players) == 3
        assert sum(1 for p in game._players.values() if p.is_spy) == 1

    def test_init_too_few_players_raises(self):
        with pytest.raises(UndercoverGameError, match="参与模型数需 ≥3"):
            _make_game(players=[("base_model", "m1")])

    def test_duplicate_players_deduped_and_spy_survives(self):
        """重复项在建表时会互相覆盖；去重必须发生在抽卧底之前，否则可能无人 is_spy。"""
        game, _ = _make_game(
            players=[
                ("base_model", "m1"), ("base_model", "m1"),
                ("base_model", "m2"), ("adv_model", "m3"),
            ],
            rng=random.Random(0),
        )
        assert len(game._players) == 3
        assert sum(1 for p in game._players.values() if p.is_spy) == 1

    def test_too_few_after_dedup_raises(self):
        with pytest.raises(UndercoverGameError, match="去重后"):
            _make_game(players=[("base_model", "m1"), ("base_model", "m1"), ("base_model", "m2")])


class TestUndercoverGameRun:
    """测试完整对局流程（mock LLM 调用）。"""

    def test_civilians_win_spy_eliminated(self):
        game, mock_llm = _make_game(rng=random.Random(1))
        mock_llm.generate_multimodal_as.return_value = "描述文本"
        spy_key = next(p.key for p in game._players.values() if p.is_spy)

        gen = MagicMock()
        gen.text = f"投票：{spy_key}\n理由：怀疑"
        mock_llm.generate_as.return_value = gen

        result = game.run()

        assert result.winner == "civilians"
        assert spy_key not in result.final_survivors
        assert len(result.rounds) == 1
        assert result.rounds[0].eliminated == spy_key

    def test_spy_wins_survivors_le_2(self):
        game, mock_llm = _make_game(rng=random.Random(0))
        spy_key = next(p.key for p in game._players.values() if p.is_spy)
        target_civilian = [p.key for p in game._players.values() if not p.is_spy][0]
        mock_llm.generate_multimodal_as.return_value = "描述"

        def gen_side_effect(role, model_id, prompt, **kwargs):
            res = MagicMock()
            res.text = f"投票：{target_civilian}\n理由：随意"
            return res

        mock_llm.generate_as.side_effect = gen_side_effect

        result = game.run()

        assert result.winner == "spy"
        assert spy_key in result.final_survivors
        assert len(result.final_survivors) == 2

    def test_all_players_failing_ends_as_stalemate(self):
        """全员调用失败→无有效票→无人淘汰。必须靠轮次上限收敛，不能死循环。"""
        game, mock_llm = _make_game(rng=random.Random(0))
        mock_llm.generate_multimodal_as.side_effect = LLMProviderError("boom")
        mock_llm.generate_as.side_effect = LLMProviderError("boom")

        result = game.run()

        assert result.winner == "stalemate"
        assert len(result.final_survivors) == 3
        assert len(result.rounds) == max(len(game._players) * 2, 4)
        assert any("stalemate" in e for e in result.errors)
        assert any("phase=describe" in e for e in result.errors)

    def test_describe_failure_recorded_but_game_continues(self):
        """单个玩家描述失败记为弃权，对局照常推进。"""
        game, mock_llm = _make_game(rng=random.Random(1))
        spy_key = next(p.key for p in game._players.values() if p.is_spy)
        failing = [p.key for p in game._players.values() if not p.is_spy][0]

        def mm_side_effect(role, model_id, parts, **kwargs):
            if f"{role}/{model_id}" == failing:
                raise LLMProviderError("describe down")
            return "正常描述"

        mock_llm.generate_multimodal_as.side_effect = mm_side_effect
        gen = MagicMock()
        gen.text = f"投票：{spy_key}\n理由：怀疑"
        mock_llm.generate_as.return_value = gen

        result = game.run()

        assert result.winner == "civilians"
        assert "弃权" in result.rounds[0].descriptions[failing]
        assert any(f"player={failing}" in e for e in result.errors)


class TestSelfVoteExclusion:
    """玩家不能投自己。"""

    def test_player_cannot_vote_for_self(self):
        game, mock_llm = _make_game(rng=random.Random(1))
        mock_llm.generate_multimodal_as.return_value = "描述"
        prompts = {}

        def gen_side_effect(role, model_id, prompt, **kwargs):
            key = f"{role}/{model_id}"
            prompts[key] = prompt
            res = MagicMock()
            # 每个玩家都试图投自己
            res.text = f"投票：{key}\n理由：自投"
            return res

        mock_llm.generate_as.side_effect = gen_side_effect
        result = game.run()

        # 自投一律解析不到候选 → 全员弃权 → 无人淘汰 → stalemate
        assert result.winner == "stalemate"
        assert all(not r.votes for r in result.rounds)
        for key, prompt in prompts.items():
            assert "不能投自己" in prompt
            # 只截取「只能投给以下候选之一：」之后的清单部分做判断：
            # 同一行前半段是「你是 <key>，不能投自己。」，必然含 key。
            candidate_line = [ln for ln in prompt.splitlines() if "只能投给以下候选" in ln][0]
            candidate_list = candidate_line.split("只能投给以下候选之一：", 1)[1]
            assert key not in candidate_list


class TestVotePhaseRevote:
    """测试平票重投编排（直接驱动 _run_vote_phase）。"""

    def _players(self, game):
        return list(game._players.values())

    def test_revote_resolves_tie_and_preserves_initial_votes(self):
        game, _ = _make_game(
            players=[("base_model", f"m{i}") for i in range(1, 5)],
            rng=random.Random(3),
        )
        keys = [p.key for p in self._players(game)]
        a, b, c, d = keys
        first = ({a: c, b: c, c: d, d: a}, {k: "r" for k in keys})   # c:2, d:1, a:1 → 无平票
        # 造平票：c 与 d 各 2 票
        first = ({a: c, b: c, c: d, d: d}, {k: "r" for k in keys})
        second = ({a: c, b: c, c: c, d: c}, {k: "r2" for k in keys})
        game._collect_votes = MagicMock(side_effect=[first, second])

        outcome = game._run_vote_phase(self._players(game), [], 1, {}, [])

        assert outcome.initial_votes == first[0]
        assert outcome.votes == second[0]
        assert len(outcome.revote_rounds) == 1
        assert sorted(outcome.revote_rounds[0]["tied_candidates"]) == sorted([c, d])
        # 重投必须携带上一次投票分布
        note = game._collect_votes.call_args_list[1].kwargs["extra_note"]
        assert "上一次投票分布" in note

    def test_revote_all_abstain_keeps_previous_votes(self):
        """重投全员弃权时不能把有效票清空，否则本轮无人淘汰。"""
        game, _ = _make_game(
            players=[("base_model", f"m{i}") for i in range(1, 5)],
            rng=random.Random(3),
        )
        keys = [p.key for p in self._players(game)]
        a, b, c, d = keys
        first = ({a: c, b: c, c: d, d: d}, {k: "r" for k in keys})
        game._collect_votes = MagicMock(side_effect=[first, ({}, {})])

        outcome = game._run_vote_phase(self._players(game), [], 1, {}, [])

        assert outcome.votes == first[0]
        assert len(outcome.revote_rounds) == 1

    def test_revote_exhausted_stops_at_max(self):
        game, _ = _make_game(
            players=[("base_model", f"m{i}") for i in range(1, 5)],
            rng=random.Random(3),
        )
        keys = [p.key for p in self._players(game)]
        a, b, c, d = keys
        tied = ({a: c, b: c, c: d, d: d}, {k: "r" for k in keys})
        game._collect_votes = MagicMock(return_value=tied)

        outcome = game._run_vote_phase(self._players(game), [], 1, {}, [])

        assert len(outcome.revote_rounds) == UndercoverGame._MAX_REVOTE_ROUNDS
        # 首投 + 3 次重投
        assert game._collect_votes.call_count == UndercoverGame._MAX_REVOTE_ROUNDS + 1


class TestResolveElimination:
    """测试计票与淘汰逻辑（包括平票随机）。"""

    def test_resolve_elimination_single_top(self):
        game, _ = _make_game()
        assert game._resolve_elimination(Counter({"p1": 3, "p2": 1})) == "p1"

    def test_resolve_elimination_tie_random(self):
        game, _ = _make_game(rng=random.Random(42))
        assert game._resolve_elimination(Counter({"p1": 2, "p2": 2})) in ("p1", "p2")

    def test_resolve_elimination_empty_tally(self):
        game, _ = _make_game()
        assert game._resolve_elimination(Counter()) is None


class TestRunParallelResilience:
    """并行调用中的意外异常不能中断整个对局。"""

    def test_unexpected_exception_falls_back(self):
        game, _ = _make_game()
        players = list(game._players.values())
        boom_key = players[0].key
        errors = []

        def fn(player):
            if player.key == boom_key:
                raise ValueError("unexpected")
            return player.key, "ok"

        results = game._run_parallel(
            players, fn, 4, fallback="FALLBACK", errors=errors, phase="describe",
        )

        assert set(results) == {p.key for p in players}
        assert results[boom_key] == "FALLBACK"
        assert all(results[p.key] == "ok" for p in players[1:])
        assert any("unexpected_error" in e for e in errors)


class TestSequentialDescribe:
    """顺序发言：每位玩家只能看到本轮位次更靠前的人。"""

    def test_each_speaker_sees_only_earlier_speakers(self):
        game, mock_llm = _make_game(players=_players(4), rng=random.Random(5))
        order = game._speaking_order_base
        prompts = _capture_prompts(
            mock_llm, game,
            describe_reply=lambda key: f"看到{key}的元素",
            vote_picker=lambda candidates, voter, prompt: None,
        )

        speeches = game._run_describe_phase(list(game._players.values()), [], 1, [])

        assert [s.key for s in speeches] == order
        for index, key in enumerate(order):
            text = prompts[index][2]
            for position, other in enumerate(order):
                marker = f"看到{other}的元素"
                if position < index:
                    assert marker in text, f"第{index + 1}位看不到第{position + 1}位"
                else:
                    assert marker not in text, f"第{index + 1}位不该看到第{position + 1}位"

    def test_first_speaker_has_no_prior_block(self):
        game, mock_llm = _make_game(players=_players(3), rng=random.Random(5))
        prompts = _capture_prompts(
            mock_llm, game, describe_reply=lambda key: "雨天玻璃", vote_picker=lambda c, v, p: None,
        )
        game._run_describe_phase(list(game._players.values()), [], 1, [])
        assert "（本轮前面还没有人给出可参考的有效发言）" in prompts[0][2]

    def test_silent_speaker_excluded_from_prior_block(self):
        """轮内也不能把「弃权」当成一条与众不同的描述喂给后面的人。"""
        game, mock_llm = _make_game(players=_players(3), rng=random.Random(5))
        first = game._speaking_order_base[0]

        def mm_side_effect(role, model_id, parts, **kwargs):
            if f"{role}/{model_id}" == first:
                raise LLMProviderError("boom")
            return "雨天玻璃"

        mock_llm.generate_multimodal_as.side_effect = mm_side_effect
        speeches = game._run_describe_phase(list(game._players.values()), [], 1, [])

        assert speeches[0].status == "api_error"
        assert "（你是本轮第一位发言者" not in mock_llm.generate_multimodal_as.call_args_list[1][0][2][0]["text"]

    def test_speaking_order_and_elapsed_recorded(self):
        game, mock_llm = _make_game(players=_players(3), rng=random.Random(5))
        mock_llm.generate_multimodal_as.return_value = "雨天玻璃"
        gen = MagicMock()
        gen.text = "投票：base_model/m1\n理由：x"
        mock_llm.generate_as.return_value = gen

        result = game.run()

        record = result.rounds[0]
        assert record.speaking_order == [s.key for s in record.speeches]
        assert [s.order_index for s in record.speeches] == [0, 1, 2]
        assert record.elapsed_sec >= 0
        assert result.speaking_order_base == game._speaking_order_base
        assert result.order_mode == "rotate"


class TestDoubleSpyOutcome:
    """双卧底的胜负判定。"""

    def test_eliminating_one_spy_does_not_end_the_game(self):
        """淘汰一名卧底后游戏必须继续——这正是「游戏未结束⇒被淘汰者不是卧底」
        这条旧措辞在双卧底下的失效点。"""
        game, mock_llm = _make_game(players=_players(8), rng=random.Random(4))
        spies = [p.key for p in game._players.values() if p.is_spy]
        assert len(spies) == 2

        def picker(candidates, voter, prompt):
            # 第 1 轮投第一个卧底，第 2 轮投第二个（靠 history 里的轮次结果区分）
            target = spies[1] if "[第1轮结果]" in prompt else spies[0]
            return target if target in candidates else None

        _capture_prompts(
            mock_llm, game, describe_reply=lambda key: f"描述-{key}", vote_picker=picker,
        )
        result = game.run()

        assert result.rounds[0].eliminated == spies[0]
        assert result.rounds[0].eliminated_was_spy is True
        assert len(result.rounds) == 2
        assert result.rounds[1].eliminated == spies[1]
        assert result.winner == "civilians"

    def test_spies_win_at_parity(self):
        """卧底人数不少于平民人数即获胜（8 人局：2 卧底 vs 2 平民时收局）。"""
        game, mock_llm = _make_game(players=_players(8), rng=random.Random(4))
        spies = {p.key for p in game._players.values() if p.is_spy}

        def picker(candidates, voter, prompt):
            civilians = [c for c in candidates if c not in spies]
            return civilians[0] if civilians else None

        _capture_prompts(
            mock_llm, game, describe_reply=lambda key: "雨天玻璃", vote_picker=picker,
        )
        result = game.run()

        assert result.winner == "spy"
        spy_alive = sum(1 for k in result.final_survivors if k in spies)
        civ_alive = len(result.final_survivors) - spy_alive
        assert spy_alive >= civ_alive
        assert spy_alive == 2

    def test_single_spy_condition_matches_legacy(self):
        """单卧底时新判定必须与旧的「存活 ≤2 且卧底存活」严格等价。"""
        game, mock_llm = _make_game(players=_players(3), rng=random.Random(0))
        spy_key = next(p.key for p in game._players.values() if p.is_spy)
        civilians = [p.key for p in game._players.values() if not p.is_spy]

        def picker(candidates, voter, prompt):
            return civilians[0] if civilians[0] in candidates else None

        _capture_prompts(mock_llm, game, describe_reply=lambda key: "描述", vote_picker=picker)
        result = game.run()

        assert result.winner == "spy"
        assert len(result.final_survivors) == 2
        assert spy_key in result.final_survivors


class TestPrivateIsolation:
    """私有档案绝不能泄漏到其他玩家的 prompt。"""

    _SPY_REPLY = (
        "【观察清单】\n- ZZMARKER私有元素 (保留)\n"
        "【身份自评】卧底\n【置信度】90\n【判断依据】ZZMARKER理由\n"
        "【公开描述】雨天玻璃窗\n【回应】无"
    )

    def test_observation_list_never_leaks_to_other_players(self):
        game, mock_llm = _make_game(players=_players(8), rng=random.Random(4))
        spies = {p.key for p in game._players.values() if p.is_spy}

        def describe_reply(key):
            return self._SPY_REPLY if key in spies else "雨天玻璃窗外有建筑"

        prompts = _capture_prompts(
            mock_llm, game, describe_reply=describe_reply,
            vote_picker=lambda candidates, voter, prompt: None,
        )
        players = list(game._players.values())
        game._run_describe_phase(players, [], 1, [])

        # 第一轮：任何人都没有往轮私有档案，所以全场 prompt 都不该出现哨兵串
        for phase, owner, text in prompts:
            assert "ZZMARKER" not in text, f"{phase} 阶段 {owner} 的 prompt 泄漏了私有观察清单"

    def test_own_private_history_reaches_self_only_in_later_rounds(self):
        """第 2 轮起：卧底自己能回顾上轮档案，别人看不到。"""
        game, mock_llm = _make_game(players=_players(8), rng=random.Random(4))
        spies = {p.key for p in game._players.values() if p.is_spy}
        spy_key = sorted(spies)[0]

        prompts = _capture_prompts(
            mock_llm, game,
            describe_reply=lambda key: self._SPY_REPLY if key in spies else "雨天玻璃",
            vote_picker=lambda candidates, voter, prompt: None,
        )
        players = list(game._players.values())
        round_one = game._run_describe_phase(players, [], 1, [])
        record = RoundRecord(round_no=1)
        record.speeches = round_one
        record.speaking_order = [s.key for s in round_one]
        record.descriptions = {s.key: s.description for s in round_one}

        prompts.clear()
        game._run_describe_phase(players, [record], 2, [])

        leaked = [(o, t) for _p, o, t in prompts if "ZZMARKER" in t and o not in spies]
        assert not leaked, f"卧底私有档案泄漏给平民: {[o for o, _ in leaked]}"
        assert any("ZZMARKER" in t and o == spy_key for _p, o, t in prompts), \
            "卧底本人应当能在第 2 轮看到自己的往轮档案"


class TestDefectRegressions:
    """两个已知缺陷的防回归。"""

    def test_abstainer_not_in_comparison_block(self):
        """调用失败的玩家不能以「本轮弃权」参与「谁最与众不同」的比对。"""
        game, mock_llm = _make_game(players=_players(4), rng=random.Random(5))
        silent_key = game._speaking_order_base[0]

        def describe_reply(key):
            if key == silent_key:
                raise LLMProviderError("describe down")
            return f"描述-{key}"

        prompts = _capture_prompts(
            mock_llm, game, describe_reply=describe_reply,
            vote_picker=lambda candidates, voter, prompt: None,
        )
        result = game.run()

        assert silent_key in result.rounds[0].silent
        assert _DESCRIBE_FAILED_PLACEHOLDER in result.rounds[0].descriptions[silent_key]
        vote_prompts = [t for p, _o, t in prompts if p == "vote"]
        assert vote_prompts
        for text in vote_prompts:
            # 只看公开比对块：该玩家**自己**的私有档案里出现「你公开的描述：…弃权」
            # 是正确的（他应当知道自己上一轮失败了），但绝不能作为一条描述进入
            # 全场的「谁最与众不同」比对。
            history = text.split("以下是本局到目前为止的完整公开记录：", 1)[1]
            history = history.split("当前存活玩家：", 1)[0]
            assert _DESCRIBE_FAILED_PLACEHOLDER not in history
            assert f"[第1轮未发言] {silent_key}" in history

    def test_malformed_speaker_stays_in_comparison_block(self):
        """输出不合规必须留在比对块里——否则「装死」就是卧底的严格优势策略。"""
        game, mock_llm = _make_game(players=_players(4), rng=random.Random(5))
        noisy_key = game._speaking_order_base[0]

        def describe_reply(key):
            if key == noisy_key:
                return "没有分节标记的超长文本" * 30
            return f"描述-{key}"

        prompts = _capture_prompts(
            mock_llm, game, describe_reply=describe_reply,
            vote_picker=lambda candidates, voter, prompt: None,
        )
        result = game.run()

        assert noisy_key in result.rounds[0].malformed
        assert noisy_key not in result.rounds[0].silent
        rendered = next(t for p, _o, t in prompts if p == "vote")
        assert "格式不合规" in rendered

    def test_first_round_label_is_one_based(self):
        """回归：投票 history 的首轮标题曾输出成「[第0轮描述]」。"""
        game, mock_llm = _make_game(players=_players(4), rng=random.Random(5))
        prompts = _capture_prompts(
            mock_llm, game, describe_reply=lambda key: f"描述-{key}",
            vote_picker=lambda candidates, voter, prompt: None,
        )
        game.run()

        vote_prompts = [t for p, _o, t in prompts if p == "vote"]
        assert vote_prompts
        for text in vote_prompts:
            assert "[第0轮" not in text
            assert "[第1轮描述]" in text


class TestParseGameModels:
    """测试 CLI --game-models 解析。"""

    def _parse(self, spec):
        from iris.app.cli._handlers._games import _parse_game_models
        return _parse_game_models(spec, MagicMock())

    def test_valid_spec(self):
        assert self._parse("base_model/m1,adv_model/m2") == [
            ("base_model", "m1"), ("adv_model", "m2"),
        ]

    def test_skips_missing_separator(self):
        assert self._parse("base_model/m1,bogus") == [("base_model", "m1")]

    def test_skips_empty_role_or_model(self):
        assert self._parse("/m1,base_model/,base_model/m2") == [("base_model", "m2")]

    def test_skips_unknown_role(self):
        assert self._parse("nope/m1,base_model/m2") == [("base_model", "m2")]

    def test_dedupes(self):
        assert self._parse("base_model/m1,base_model/m1") == [("base_model", "m1")]

    def test_tolerates_whitespace_and_blanks(self):
        assert self._parse(" base_model / m1 , ,adv_model/m2 ") == [
            ("base_model", "m1"), ("adv_model", "m2"),
        ]
