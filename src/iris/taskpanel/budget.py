"""长任务预算：统一限制时间、调用次数、token 和估算成本。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from iris.core.exceptions import IrisRuntimeError

class TaskBudgetExceeded(IrisRuntimeError):
    """任务超过任一预算上限。"""


@dataclass
class TaskBudget:
    """可嵌入任意长任务的轻量预算对象。"""

    max_seconds: Optional[float] = None
    max_calls: Optional[int] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_cost: Optional[float] = None
    started_at: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    def __post_init__(self) -> None:
        if self.started_at <= 0:
            self.started_at = time.monotonic()
        for value in (self.max_seconds, self.max_cost):
            if value is not None and value < 0:
                raise ValueError("预算上限不能为负数")
        for value in (self.max_calls, self.max_input_tokens, self.max_output_tokens):
            if value is not None and value < 0:
                raise ValueError("预算上限必须为非负整数")

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def check(self, *, calls: int = 1) -> None:
        """在开始外部调用前检查预算。"""
        if self.max_seconds is not None and self.elapsed_seconds >= self.max_seconds:
            raise TaskBudgetExceeded("任务超过时间预算")
        if self.max_calls is not None and self.calls + calls > self.max_calls:
            raise TaskBudgetExceeded("任务超过调用次数预算")

    def record(self, *, input_tokens: int = 0, output_tokens: int = 0,
               cost: float = 0.0, calls: int = 1) -> None:
        """记录一次调用结果，并检查累计预算。"""
        if min(input_tokens, output_tokens, cost, calls) < 0:
            raise ValueError("用量不能为负数")
        self.calls += calls
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost += cost
        if self.max_input_tokens is not None and self.input_tokens > self.max_input_tokens:
            raise TaskBudgetExceeded("任务超过输入 token 预算")
        if self.max_output_tokens is not None and self.output_tokens > self.max_output_tokens:
            raise TaskBudgetExceeded("任务超过输出 token 预算")
        if self.max_cost is not None and self.cost > self.max_cost:
            raise TaskBudgetExceeded("任务超过费用预算")

    def snapshot(self) -> dict[str, float | int]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
        }
