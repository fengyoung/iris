"""游戏命令 handler。"""

from pathlib import Path
from typing import Any, Dict, List, Tuple

from iris.app.cli.helpers import _emit_output
from iris.games.undercover import GameResult, UndercoverGame
from iris.utils.shared import atomic_write_json

_VALID_ROLES = ("base_model", "adv_model")


def _eliminated_positions(result: GameResult) -> List[Dict[str, Any]]:
    """逐轮记录被淘汰者的发言位次与身份——位次惩罚（首个发言人被系统性淘汰）的诊断数据。

    风险背景：顺序发言下第 1 位掌握的本轮信息最少，描述天然最独特；而投票规则是
    「谁最与众不同」。若某位次的淘汰占比显著高于 1/N，说明 prompt 里的「位次不是判据」
    约束没生效。日志里直接给出这个分布，省得每轮复盘都手算。
    """
    positions: List[Dict[str, Any]] = []
    for record in result.rounds:
        if not record.eliminated:
            continue
        speech = next((s for s in record.speeches if s.key == record.eliminated), None)
        positions.append({
            "round": record.round_no,
            "key": record.eliminated,
            "order_index": speech.order_index if speech else None,
            "was_spy": record.eliminated_was_spy,
        })
    return positions


def _parse_game_models(spec: str, logger) -> List[Tuple[str, str]]:
    """解析 --game-models 规格串为 [(role, model_id), ...]。

    逐项校验并跳过非法项（记 warning 而非静默丢弃）：
      - 缺 "/" 分隔符
      - role 或 model_id 为空（如 "/m1"、"base_model/"）
      - role 不在 base_model / adv_model 之内
      - 与已收集项重复（重复会在 UndercoverGame 内被 key 去重，
        导致实际人数少于用户预期，故在入口就拒绝）
    """
    players: List[Tuple[str, str]] = []
    seen: set = set()
    for raw_item in spec.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if "/" not in item:
            logger.log(
                "undercover_game",
                {"warning": f"模型格式错误（应为 role/model_id）: {item}"},
                level="warning",
            )
            continue
        role, model_id = (part.strip() for part in item.split("/", 1))
        if not role or not model_id:
            logger.log(
                "undercover_game",
                {"warning": f"role 与 model_id 均不能为空: {item}"},
                level="warning",
            )
            continue
        if role not in _VALID_ROLES:
            logger.log(
                "undercover_game",
                {"warning": f"未知 role（应为 {' / '.join(_VALID_ROLES)}）: {item}"},
                level="warning",
            )
            continue
        key = f"{role}/{model_id}"
        if key in seen:
            logger.log(
                "undercover_game",
                {"warning": f"重复模型，已忽略: {key}"},
                level="warning",
            )
            continue
        seen.add(key)
        players.append((role, model_id))
    return players


def handle_undercover_game(args, bundle, logger) -> int:
    """执行多模型对抗游戏「谁是卧底」。"""
    if not args.image_a or not args.image_b:
        raise ValueError("undercover-game 需要 --image-a 和 --image-b")

    image_a = Path(args.image_a).expanduser().resolve()
    image_b = Path(args.image_b).expanduser().resolve()
    # 两张图相同则卧底与平民看到同一张，游戏失去意义（无差异可辨），提前拒绝。
    if image_a == image_b:
        raise ValueError("--image-a 与 --image-b 不能是同一张图片")

    players = None
    if args.game_models:
        players = _parse_game_models(args.game_models, logger)
        if len(players) < 3:
            raise ValueError(f"参与模型数需 ≥3（去重并剔除非法项后），当前仅 {len(players)}")

    game = UndercoverGame(
        bundle,
        image_a_path=str(image_a),
        image_b_path=str(image_b),
        players=players,
        max_players=args.max_players,
        # 0 表示随机种子，转成 None 才是「不固定」。
        seed=args.seed or None,
        max_rounds=args.max_rounds,
        order_mode=args.order_mode,
    )
    result = game.run()

    if args.output_file:
        atomic_write_json(Path(args.output_file), result.to_dict())
        logger.log("undercover_game", {
            "output_file": args.output_file,
            "winner": result.winner,
            "spy_keys": result.spy_keys,
            "seed": result.seed,
            "order_mode": result.order_mode,
            "speaking_order_base": result.speaking_order_base,
            "eliminated_order_index": _eliminated_positions(result),
        })

    _emit_output(args.command, result.to_dict(), pretty=args.pretty)
    return 0


GAMES_HANDLERS: Dict[str, object] = {
    "undercover-game": handle_undercover_game,
}
