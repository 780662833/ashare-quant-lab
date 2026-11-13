import pytest

from ashare_quant.backtest import BacktestConfig, CostModel
from ashare_quant.research.cost_stress import (
    COST_STRESS_MULTIPLIERS,
    build_cost_stress_scenarios,
)


def test_cost_stress_scenarios_scale_every_cost_component_from_exact_baseline():
    baseline = BacktestConfig(
        initial_cash=250_000,
        top_n=20,
        costs=CostModel(
            commission_rate=0.0003,
            minimum_commission=5.0,
            stamp_duty_sell_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_bps=5.0,
        ),
    )
    scenarios = build_cost_stress_scenarios(baseline)

    assert tuple(item.multiplier for item in scenarios) == COST_STRESS_MULTIPLIERS
    assert [item.scenario_id for item in scenarios] == ["C0", "C1", "C2", "C3"]
    assert scenarios[0].backtest_config == baseline
    for item in scenarios:
        factor = item.multiplier
        costs = item.backtest_config.costs
        assert costs.commission_rate == pytest.approx(0.0003 * factor)
        assert costs.minimum_commission == pytest.approx(5.0 * factor)
        assert costs.stamp_duty_sell_rate == pytest.approx(0.0005 * factor)
        assert costs.transfer_fee_rate == pytest.approx(0.00001 * factor)
        assert costs.slippage_bps == pytest.approx(5.0 * factor)
        assert item.backtest_config.initial_cash == baseline.initial_cash
        assert item.backtest_config.top_n == baseline.top_n


def test_zero_cost_baseline_remains_zero_under_all_multipliers():
    baseline = BacktestConfig(costs=CostModel(
        commission_rate=0.0,
        minimum_commission=0.0,
        stamp_duty_sell_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=0.0,
    ))
    scenarios = build_cost_stress_scenarios(baseline)
    for item in scenarios:
        assert item.backtest_config.costs == baseline.costs


def test_cost_stress_rejects_non_backtest_baseline():
    with pytest.raises(TypeError, match="BacktestConfig"):
        build_cost_stress_scenarios({"costs": {}})
