from __future__ import annotations

import pytest

from iris.taskpanel.budget import TaskBudget, TaskBudgetExceeded


def test_budget_tracks_usage_and_snapshot():
    budget = TaskBudget(max_calls=2, max_input_tokens=10, max_output_tokens=8,
                        max_cost=1.5)
    budget.check()
    budget.record(input_tokens=4, output_tokens=3, cost=0.5)
    assert budget.snapshot()["calls"] == 1
    budget.record(input_tokens=6, output_tokens=5, cost=1.0)
    assert budget.snapshot()["input_tokens"] == 10


def test_budget_rejects_call_overflow():
    budget = TaskBudget(max_calls=1)
    budget.record()
    with pytest.raises(TaskBudgetExceeded):
        budget.check()


def test_budget_rejects_negative_values():
    with pytest.raises(ValueError):
        TaskBudget(max_calls=-1)
    with pytest.raises(ValueError):
        TaskBudget().record(cost=-1)


@pytest.mark.parametrize("limits,usage", [
    ({"max_seconds": 0}, None),
    ({"max_input_tokens": 1}, {"input_tokens": 2}),
    ({"max_output_tokens": 1}, {"output_tokens": 2}),
    ({"max_cost": 1}, {"cost": 2}),
])
def test_each_budget_limit_is_enforced(limits, usage):
    budget = TaskBudget(**limits)
    with pytest.raises(TaskBudgetExceeded):
        if usage is None:
            budget.check()
        else:
            budget.record(**usage)
    if usage:
        # 超限仍然记录已经发生的用量，不能丢失实际消耗。
        assert budget.calls == 1


def test_negative_time_budget_is_rejected():
    with pytest.raises(ValueError):
        TaskBudget(max_seconds=-1)
