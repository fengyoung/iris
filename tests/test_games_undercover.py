"""测试 games.undercover — 多模型对抗游戏逻辑。"""

import random
import re
import threading
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
    裁判增量校验与投票共用 generate_as，按「候选清单」标记区分：没有该标记的
    即为裁判调用，记作 referee 相位并一律判定有增量（不改变既有剧情走向）。
    """
    prompts = []

    def mm_side_effect(role, model_id, parts, **kwargs):
        key = f"{role}/{model_id}"
        prompts.append(("describe", key, parts[0]["text"]))
        return describe_reply(key)

    def gen_side_effect(role, model_id, prompt, **kwargs):
        key = f"{role}/{model_id}"
        result = MagicMock()
        if "只能投给以下候选之一：" not in prompt:
            prompts.append(("referee", key, prompt))
            result.text = "增量：是\n理由：mock 裁判判定有增量"
            return result
        prompts.append(("vote", key, prompt))
        # 候选清单渲染成「N号=key」，这里还原成裸 key——picker 与断言都按 key 写，
        # 编号格式只是呈现层的事，不该漏进测试 API。
        raw = prompt.split("只能投给以下候选之一：", 1)[1].strip().split(", ")
        candidates = [c.split("=", 1)[-1] for c in raw]
        target = vote_picker(candidates, key, prompt)
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

    def test_multi_candidate_body_without_vote_line_abstains(self):
        """回归：正文里逐个列出多名玩家的 key 时，不得按长度挑一个当投票对象。

        这正是「投票理由通篇论证甲、投票对象却是乙」的根因——挑中谁只取决于
        key 的字符串长度，与模型真正投的人无关。多个候选时无从判断意图，宁可弃权。
        """
        keys = ["base_model/m1", "base_model/m2", "base_model/m10"]
        text = "我核对了 base_model/m1 与 base_model/m2 的描述，两者都可疑。"
        target, _ = _parse_vote(text, keys)
        assert target is None

    def test_resolves_number_vote(self):
        """模型常只写编号（prompt 里每个玩家都带 N号= 前缀），按 numbers 精确映射。"""
        keys = ["base_model/m1", "base_model/m2", "base_model/m3"]
        numbers = {"base_model/m1": 1, "base_model/m2": 2, "base_model/m3": 3}
        target, _ = _parse_vote("投票：3号\n理由：离群", keys, numbers)
        assert target == "base_model/m3"

    def test_resolves_verbatim_label(self):
        """模型照抄候选清单里的「N号=key」整串也要能解析。"""
        keys = ["base_model/m2"]
        target, _ = _parse_vote("投票：2号=base_model/m2\n理由：x", keys, {"base_model/m2": 2})
        assert target == "base_model/m2"

    def test_longer_number_not_stolen_by_shorter(self):
        """「14号」不能被「4号」抢走。"""
        keys = [f"base_model/m{i}" for i in range(1, 15)]
        numbers = {k: i + 1 for i, k in enumerate(keys)}
        target, _ = _parse_vote("投票：14号\n理由：x", keys, numbers)
        assert target == "base_model/m14"

    def test_number_vote_wins_over_multi_key_preamble(self):
        """回归：前言列了多个 key、投票行只写编号——必须按编号解析，不能被前言带走。"""
        keys = ["base_model/m1", "base_model/m2", "base_model/m10"]
        numbers = {"base_model/m1": 1, "base_model/m2": 2, "base_model/m10": 10}
        text = ("先逐个核对各玩家：\n- base_model/m1 提到农田\n- base_model/m2 提到城市\n"
                "投票：10号\n理由：与多数玩家核心元素冲突")
        target, _ = _parse_vote(text, keys, numbers)
        assert target == "base_model/m10"

    def test_number_not_in_candidates_is_not_votable(self):
        """编号指向自己（不能投自己）时不应命中。"""
        keys = ["base_model/m2", "base_model/m3"]
        numbers = {"base_model/m1": 1, "base_model/m2": 2, "base_model/m3": 3}
        target, _ = _parse_vote("投票：1号\n理由：x", keys, numbers)
        assert target is None


class TestOpeningSpeakerGuidance:
    """「少说细节」的提示只给本局第一位发言者，其余人拿到「不适用」。

    给所有人发会让后面的玩家以为说的是自己——他们有前置内容可参考，恰恰不该保守。
    """

    def test_only_the_very_first_speaker_gets_it(self):
        game, mock_llm = _make_game(players=_players(4), rng=random.Random(5))
        prompts = _capture_prompts(
            mock_llm, game, describe_reply=lambda key: f"描述-{key}",
            vote_picker=lambda candidates, voter, prompt: None,
        )
        game.run()

        describe = [t for phase, _o, t in prompts if phase == "describe"]
        assert describe
        assert "你是本局第一位发言的人" in describe[0]
        # 不能只剩「少说」：整轮零信息会让同伴无从比对，首轮白白空转
        assert "可核对" in describe[0]
        assert "不要展开最具体的辨识特征" in describe[0]
        # 模板检查只看模板本身，这条是运行时拼接的，得在渲染结果上查
        assert "**" not in describe[0]
        for text in describe[1:]:
            assert "（不适用：你不是本局第一位发言者）" in text
            assert "你是本局第一位发言的人" not in text


class TestPlayerNumberUnambiguous:
    """玩家引用统一成 `N号=key`，且 N 取全局固定编号而非本轮发言位次。

    早先描述 prompt 用 `player_number`、历史块用发言位次：第 1 轮两者恰好重合，
    第 2 轮起位次随轮转变化就分叉，模型说「4号」时无从判断指哪一个。
    """

    def test_every_label_uses_fixed_number(self):
        """跑满多轮，逐条核对 N号 与 player_number 一致。

        若标签用的是发言位次，第 2 轮轮转后就会与固定编号分叉——本断言即失败。
        """
        game, mock_llm = _make_game(players=_players(6), rng=random.Random(5))
        prompts = _capture_prompts(
            mock_llm, game, describe_reply=lambda key: f"描述-{key}",
            vote_picker=lambda candidates, voter, prompt: None,
        )
        game.run()

        numbers = game._player_numbers()
        checked = 0
        for phase, _owner, text in prompts:
            if phase != "vote":
                continue
            for match in re.finditer(r"(\d+)号=([\w./\-]+)", text):
                num, key = int(match.group(1)), match.group(2)
                assert numbers.get(key) == num, f"{key} 标成 {num}号，实际固定编号为 {numbers.get(key)}"
                checked += 1
        assert checked > 0, "prompt 里应至少出现一处玩家编号标签"

    def test_candidate_list_is_votable_verbatim(self):
        """候选清单形如 `N号=key`，模型照抄整串也要能被解析回该玩家。"""
        game, mock_llm = _make_game(players=_players(4), rng=random.Random(5))
        prompts = _capture_prompts(
            mock_llm, game, describe_reply=lambda key: f"描述-{key}",
            vote_picker=lambda candidates, voter, prompt: None,
        )
        game.run()

        text = [t for p, _o, t in prompts if p == "vote"][0]
        raw = text.split("只能投给以下候选之一：", 1)[1].strip()
        for item in raw.split(", "):
            number, key = item.split("=", 1)
            assert number.endswith("号")
            assert game._players[key].player_number == int(number[:-1])


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

    def test_spies_win_only_when_outnumbering(self):
        """卧底必须在人数上严格多于平民才获胜。

        3 人局恒投平民：第 1 轮淘汰后停在 1v1 打平态，此时**不能**收局——再淘汰一名
        平民、轮到 1v0 时才判卧底胜。旧的「存活 ≤2 且卧底存活」语义下这里正好是 2 人收局。
        """
        game, mock_llm = _make_game(rng=random.Random(0))
        spy_key = next(p.key for p in game._players.values() if p.is_spy)
        mock_llm.generate_multimodal_as.return_value = "描述"

        def gen_side_effect(role, model_id, prompt, **kwargs):
            res = MagicMock()
            alive_civilians = [
                p.key for p in game._players.values() if not p.is_spy and p.alive
            ]
            res.text = f"投票：{alive_civilians[0]}\n理由：随意" if alive_civilians else "我不知道投给谁"
            return res

        mock_llm.generate_as.side_effect = gen_side_effect

        result = game.run()

        assert result.winner == "spy"
        assert spy_key in result.final_survivors
        assert len(result.final_survivors) == 1
        assert len(result.rounds) == 2

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
            res = MagicMock()
            # 裁判增量校验与投票共用 generate_as：前者无候选清单，不参与投票断言
            if "只能投给以下候选之一：" not in prompt:
                res.text = "增量：是\n理由：mock 裁判判定有增量"
                return res
            prompts[key] = prompt
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


class TestOutcomeConditions:
    """胜负判定：卧底须严格多于平民才获胜，人数打平时继续。"""

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

    def test_no_win_at_parity(self):
        """人数打平不收局（8 人局：2 卧底 vs 2 平民时继续，直到卧底严格多于平民）。

        本用例是「打平继续」的正面回归钉，**必须显式断言轮数**：旧规则下这三条关于
        幸存者构成的断言会全部照旧成立（只是提前一轮收局），属于典型的假阳性。
        """
        game, mock_llm = _make_game(players=_players(8), rng=random.Random(4))
        spies = {p.key for p in game._players.values() if p.is_spy}

        def picker(candidates, voter, prompt):
            civilians = [c for c in candidates if c not in spies]
            return civilians[0] if civilians else None

        _capture_prompts(
            mock_llm, game, describe_reply=lambda key: "雨天玻璃", vote_picker=picker,
        )
        result = game.run()

        # 每轮淘汰一名平民：8 → 7 → 6 → 5（第 4 轮后是 2v2，打平不收局）→ 4（2v1 收局）
        assert len(result.rounds) == 5
        assert all(r.eliminated_was_spy is False for r in result.rounds)
        spy_alive = sum(1 for k in result.final_survivors if k in spies)
        civ_alive = len(result.final_survivors) - spy_alive
        assert (spy_alive, civ_alive) == (2, 1)
        assert spy_alive > civ_alive
        assert result.winner == "spy"

    def test_civilians_win_from_parity_state(self):
        """打平态下平民的胜利路径没有被改坏：1v1 时投出卧底照样平民胜。"""
        game, mock_llm = _make_game(players=_players(3), rng=random.Random(0))
        spy_key = next(p.key for p in game._players.values() if p.is_spy)
        civilians = [p.key for p in game._players.values() if not p.is_spy]

        def picker(candidates, voter, prompt):
            # 第 1 轮误淘汰平民（制造 1v1 打平态），此后投卧底
            target = spy_key if "[第1轮结果]" in prompt else civilians[0]
            return target if target in candidates else None

        _capture_prompts(mock_llm, game, describe_reply=lambda key: "描述", vote_picker=picker)
        result = game.run()

        assert result.rounds[0].eliminated_was_spy is False
        assert result.rounds[1].eliminated == spy_key
        assert result.winner == "civilians"
        assert len(result.final_survivors) == 1

    def test_stalemate_when_round_cap_reached_at_parity(self):
        """打平态撞上轮次上限同样记 stalemate，不谎报一方胜利。"""
        game, mock_llm = _make_game(players=_players(3), rng=random.Random(0), max_rounds=1)
        civilians = [p.key for p in game._players.values() if not p.is_spy]

        def picker(candidates, voter, prompt):
            return civilians[0] if civilians[0] in candidates else None

        _capture_prompts(mock_llm, game, describe_reply=lambda key: "描述", vote_picker=picker)
        result = game.run()

        # 第 1 轮淘汰平民后停在 1v1 打平态，而轮次上限已到
        assert len(result.rounds) == 1
        assert result.rounds[0].eliminated_was_spy is False
        assert result.winner == "stalemate"
        assert len(result.final_survivors) == 2
        assert any("stalemate" in e for e in result.errors)

    def test_round_waiting_emitted_at_parity_in_step_mode(self):
        """打平轮也必须发 round_waiting——否则手动步进模式下该轮被静默跳过。

        这是「继续 ⟺ 卧底存活且不多于平民」这条互补关系的唯一守卫。若 continuing
        仍写成 卧底 < 平民，打平轮不发事件，Web 端相位停在 waiting 之外，步进按钮
        不启用（web_server 的相位守卫会返回 409），对局静默推进到下一轮。
        """

        def wait_rounds_for(player_count: int) -> list:
            advance = threading.Event()
            waiting: list = []

            def on_event(event_type, payload):
                if event_type == "round_waiting":
                    waiting.append(payload["round_no"])
                    # 回调在 wait() 之前触发，这里置位即可立即继续（不会阻塞）
                    advance.set()

            game, mock_llm = _make_game(
                players=_players(player_count), rng=random.Random(4),
                advance_event=advance, on_event=on_event,
            )
            spies = {p.key for p in game._players.values() if p.is_spy}

            def picker(candidates, voter, prompt):
                civilians = [c for c in candidates if c not in spies]
                return civilians[0] if civilians else None

            _capture_prompts(mock_llm, game, describe_reply=lambda key: "描述", vote_picker=picker)
            game.run()
            return waiting

        # 3 人局：第 1 轮淘汰平民后停在 1v1 打平态，须等待；第 2 轮分出胜负，不再等待
        assert wait_rounds_for(3) == [1]
        # 8 人局：2v2 出现在第 4 轮之后，故第 1 到 4 轮都等待
        assert wait_rounds_for(8) == [1, 2, 3, 4]


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
            number = game._players[silent_key].player_number
            assert f"[第1轮未发言] {number}号={silent_key}" in history

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


def test_checkpoint_restores_history_identity_and_next_round():
    from iris.games.undercover import RoundRecord, SpeechRecord
    game, _ = _make_game(max_rounds=2)
    keys = list(game._players)
    history = RoundRecord(round_no=1, speeches=[SpeechRecord(key=keys[0], description="历史锚点")])
    cp = {"resume_version": 1, "completed_rounds": [history.to_dict()],
          "alive_keys": keys[1:], "spy_keys": [keys[1]], "speaking_order_base": keys[::-1]}
    seen = []
    game._on_event = lambda kind, payload: seen.append((kind, payload))
    with patch.object(game, "_run_describe_phase", return_value=[]) as describe, \
         patch.object(game, "_run_vote_phase") as vote:
        vote.return_value.votes = {}
        vote.return_value.reasons = {}
        vote.return_value.initial_votes = {}
        vote.return_value.initial_reasons = {}
        vote.return_value.revote_rounds = []
        result = game.run(checkpoint=cp)
    assert [r.round_no for r in result.rounds] == [1, 2]
    assert result.rounds[0].speeches[0].description == "历史锚点"
    assert result.spy_keys == [keys[1]]
    assert result.speaking_order_base == keys[::-1]
    assert [p.key for p in describe.call_args.args[0]] == keys[1:]
    assert describe.call_args.args[2] == 2
    assert next(p for k, p in seen if k == "checkpoint")["completed_rounds"][0] == history.to_dict()


def test_legacy_checkpoint_rejected_before_model_calls():
    game, llm = _make_game()
    with pytest.raises(UndercoverGameError, match="旧断点"):
        game.run(checkpoint={"completed_rounds": [{"round_no": 1}]})
    llm.generate_multimodal_as.assert_not_called()


def test_restored_manual_game_waits_and_can_be_cancelled_before_next_round():
    advance = threading.Event()
    game, llm = _make_game(advance_event=advance)
    keys = list(game._players)
    cp = {"resume_version": 1, "completed_rounds": [RoundRecord(1).to_dict()],
          "alive_keys": keys, "spy_keys": keys[:1], "speaking_order_base": keys}
    events = []

    def on_event(kind, payload):
        events.append(kind)
        if kind == "round_waiting":
            assert payload["round_no"] == 1
            game._cancel_event.set()

    game._on_event = on_event
    result = game.run(checkpoint=cp)
    assert result.winner == "cancelled"
    assert len(result.rounds) == 1
    assert "round_start" not in events
    llm.generate_multimodal_as.assert_not_called()
    llm.generate_as.assert_not_called()


@pytest.mark.parametrize("survivors,winner", [([1, 2], "civilians"), ([0], "spy")])
def test_restored_terminal_game_does_not_call_models(survivors, winner):
    game, llm = _make_game()
    keys = list(game._players)
    cp = {"resume_version": 1, "completed_rounds": [RoundRecord(1).to_dict()],
          "alive_keys": [keys[i] for i in survivors], "spy_keys": keys[:1], "speaking_order_base": keys}
    result = game.run(checkpoint=cp)
    assert result.winner == winner
    assert result.final_survivors == cp["alive_keys"]
    llm.generate_multimodal_as.assert_not_called()


@pytest.mark.parametrize("history", [[{"round_no": 1}], [RoundRecord(2).to_dict()], None])
def test_versioned_but_incomplete_checkpoint_is_rejected(history):
    game, llm = _make_game()
    keys = list(game._players)
    cp = {"resume_version": 1, "completed_rounds": history,
          "alive_keys": keys, "spy_keys": keys[:1], "speaking_order_base": keys}
    with pytest.raises(UndercoverGameError, match="损坏"):
        game.run(checkpoint=cp)
    llm.generate_multimodal_as.assert_not_called()
