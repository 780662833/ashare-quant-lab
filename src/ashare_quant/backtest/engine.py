"""Deterministic A-share portfolio simulator.

Signals are formed after a session close and can first trade at the next
session open. Execution-state columns are mandatory: the engine fails closed
rather than assuming a security was tradable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor
from typing import Any

import polars as pl

BAR_COLUMNS = {
    "instrument", "date", "open", "high", "low", "close", "volume", "amount",
    "is_suspended", "is_limit_up_locked", "is_limit_down_locked",
    "limit_up", "limit_down",
}
TARGET_COLUMNS = {"signal_date", "instrument", "weight"}
SCORE_COLUMNS = {"signal_date", "instrument", "score"}


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_duty_sell_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0

    def __post_init__(self) -> None:
        for name in (
            "commission_rate", "minimum_commission", "stamp_duty_sell_rate",
            "transfer_fee_rate", "slippage_bps",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    top_n: int = 20
    board_lot: int = 100
    allow_leverage: bool = False
    costs: CostModel = field(default_factory=CostModel)
    target_fill_ratio: float = 1.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.top_n < 1 or self.board_lot < 1:
            raise ValueError("top_n and board_lot must be positive")
        if self.allow_leverage:
            raise ValueError("Leverage is out of scope for the v1 MVP")
        if not 0.0 <= self.target_fill_ratio <= 1.0:
            raise ValueError("target_fill_ratio must be in [0, 1]")


@dataclass(slots=True)
class BacktestResult:
    daily: pl.DataFrame
    orders: pl.DataFrame
    holdings: pl.DataFrame


def _date_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _validate_bars(bars: pl.DataFrame) -> pl.DataFrame:
    missing = BAR_COLUMNS.difference(bars.columns)
    if missing:
        raise ValueError(f"Missing required execution columns: {sorted(missing)}")
    if bars.is_empty():
        raise ValueError("bars must not be empty")
    if bars.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError("Duplicate (instrument, date) bars")
    numeric = ["open", "high", "low", "close", "volume", "amount"]
    if bars.select(pl.any_horizontal([pl.col(c).is_null() for c in numeric]).any()).item():
        raise ValueError("OHLCV contains null values")
    invalid = (
        (pl.col("open") <= 0) | (pl.col("high") <= 0) |
        (pl.col("low") <= 0) | (pl.col("close") <= 0) |
        (pl.col("high") < pl.max_horizontal("open", "close", "low")) |
        (pl.col("low") > pl.min_horizontal("open", "close", "high")) |
        (pl.col("volume") < 0) | (pl.col("amount") < 0)
    )
    if bars.select(invalid.any()).item():
        raise ValueError("Invalid OHLCV values")
    for column in ("is_suspended", "is_limit_up_locked", "is_limit_down_locked"):
        if bars[column].null_count():
            raise ValueError(f"{column} contains unknown execution state")
    if bars.filter(
        (pl.col("limit_up").is_not_null() & (pl.col("limit_up") <= 0)) |
        (pl.col("limit_down").is_not_null() & (pl.col("limit_down") <= 0))
    ).height:
        raise ValueError("Price limits, when supplied, must be positive")
    return bars.sort(["date", "instrument"])


def _validate_targets(targets: pl.DataFrame, trading_dates: set[str], top_n: int) -> pl.DataFrame:
    missing = TARGET_COLUMNS.difference(targets.columns)
    if missing:
        raise ValueError(f"Missing target columns: {sorted(missing)}")
    if targets.is_empty():
        return targets
    if targets.select(pl.struct(["signal_date", "instrument"]).is_duplicated().any()).item():
        raise ValueError("Duplicate (signal_date, instrument) targets")
    if targets.select(pl.any_horizontal(
        pl.col("signal_date").is_null(),
        pl.col("instrument").is_null(),
        pl.col("weight").is_null(),
    ).any()).item():
        raise ValueError("Targets contain null values")
    if targets.filter((pl.col("weight") < 0) | (pl.col("weight") > 1)).height:
        raise ValueError("Target weights must be in [0, 1]")
    for signal_date in {_date_text(x) for x in targets["signal_date"].to_list()}:
        if signal_date not in trading_dates:
            raise ValueError(f"Signal date {signal_date} is not a trading session")
    counts = targets.group_by("signal_date").len()
    if counts.filter(pl.col("len") > top_n).height:
        raise ValueError(f"Target holdings exceed configured top_n={top_n}")
    sums = targets.group_by("signal_date").agg(pl.col("weight").sum().alias("total"))
    if sums.filter(pl.col("total") > 1.0 + 1e-9).height:
        raise ValueError("Target weights must not exceed 100% gross exposure")
    return targets


def weekly_rebalance_dates(trading_dates: list[Any]) -> list[str]:
    """Return the final available session in each ISO week."""
    grouped: dict[tuple[int, int], str] = {}
    for value in sorted({_date_text(x) for x in trading_dates}):
        year, week, _ = date.fromisoformat(value).isocalendar()
        grouped[(year, week)] = value
    return [grouped[key] for key in sorted(grouped)]


def equal_weight_targets(scores: pl.DataFrame, top_n: int = 20) -> pl.DataFrame:
    """Select top-N per signal date, with deterministic symbol tie-breaking."""
    missing = SCORE_COLUMNS.difference(scores.columns)
    if missing:
        raise ValueError(f"Missing score columns: {sorted(missing)}")
    if top_n < 1:
        raise ValueError("top_n must be positive")
    rows: list[dict[str, Any]] = []
    signal_dates = sorted({_date_text(x) for x in scores["signal_date"].to_list()})
    for signal_date in signal_dates:
        group = scores.filter(pl.col("signal_date").cast(pl.String) == signal_date)
        ranked = group.sort(["score", "instrument"], descending=[True, False]).head(top_n)
        count = ranked.height
        if count:
            weight = 1.0 / count
            rows.extend(
                {"signal_date": signal_date, "instrument": row["instrument"], "weight": weight}
                for row in ranked.select("instrument").to_dicts()
            )
    return pl.DataFrame(rows, schema={
        "signal_date": pl.String, "instrument": pl.String, "weight": pl.Float64,
    })


def _fee_components(notional: float, side: str, costs: CostModel) -> dict[str, float]:
    """Return explicit transaction-fee components for one filled order.

    Slippage is deliberately not included here: it is already reflected in
    the worse fill price versus the raw open and is reported separately.
    """
    if notional <= 0:
        return {
            "commission": 0.0,
            "transfer_fee": 0.0,
            "stamp_duty": 0.0,
        }
    commission = max(notional * costs.commission_rate, costs.minimum_commission)
    transfer = notional * costs.transfer_fee_rate
    stamp = notional * costs.stamp_duty_sell_rate if side == "sell" else 0.0
    return {
        "commission": commission,
        "transfer_fee": transfer,
        "stamp_duty": stamp,
    }


def _fees(notional: float, side: str, costs: CostModel) -> float:
    """Transaction fees excluding slippage, retained for cash-affordability checks."""
    return sum(_fee_components(notional, side, costs).values())


def run_backtest(
    bars: pl.DataFrame,
    targets: pl.DataFrame,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run close-signal/next-open execution with explicit no-fill outcomes.

    Unfilled target differences are retried on later sessions until a new
    signal replaces the target. Missing bars never imply a fill.
    """
    cfg = config or BacktestConfig()
    frame = _validate_bars(bars)
    dates = sorted({_date_text(x) for x in frame["date"].to_list()})
    checked_targets = _validate_targets(targets, set(dates), cfg.top_n)
    targets_by_date: dict[str, dict[str, float]] = {}
    if not checked_targets.is_empty():
        for signal_date in dates:
            subset = checked_targets.filter(pl.col("signal_date").cast(pl.String) == signal_date)
            if subset.height:
                targets_by_date[signal_date] = {
                    row["instrument"]: float(row["weight"]) for row in subset.to_dicts()
                }

    bars_by_date: dict[str, dict[str, dict[str, Any]]] = {}
    last_close: dict[str, float] = {}
    for date_value in dates:
        day = frame.filter(pl.col("date").cast(pl.String) == date_value)
        bars_by_date[date_value] = {row["instrument"]: row for row in day.to_dicts()}

    cash = cfg.initial_cash
    positions: dict[str, int] = {}
    active_targets: dict[str, float] = {}
    target_nav = cfg.initial_cash
    desired_quantities: dict[str, int] = {}
    target_requested_quantities: dict[str, int] = {}
    target_fill_cap_quantities: dict[str, int] = {}
    execution_target_quantities: dict[str, int] = {}
    active_target_signal_date: str | None = None
    daily_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    previous_nav = cfg.initial_cash
    costs = cfg.costs

    for date_value in dates:
        day = bars_by_date[date_value]
        pending = set(positions) | set(active_targets)
        attempts: list[tuple[str, int, dict[str, Any] | None]] = []
        for instrument in sorted(pending):
            bar = day.get(instrument)
            current_qty = positions.get(instrument, 0)
            weight = active_targets.get(instrument, 0.0)
            if weight <= 0:
                desired_quantities[instrument] = 0
            elif instrument not in desired_quantities:
                if bar is None:
                    order_rows.append({
                        "date": date_value, "instrument": instrument, "side": "buy",
                        "target_signal_date": active_target_signal_date,
                        "target_requested_qty": None,
                        "target_fill_cap_qty": None,
                        "target_fill_ratio": cfg.target_fill_ratio,
                        "requested_qty": None, "filled_qty": 0, "fill_price": None,
                        "fees": 0.0, "commission": 0.0, "transfer_fee": 0.0,
                        "stamp_duty": 0.0, "slippage_cost": 0.0,
                        "status": "no_fill", "reason": "missing_bar",
                    })
                    continue
                open_price = float(bar["open"])
                desired_quantities[instrument] = (
                    floor((target_nav * weight / open_price) / cfg.board_lot) * cfg.board_lot
                )
                target_requested_quantities.setdefault(
                    instrument, abs(desired_quantities[instrument] - current_qty)
                )
            if instrument not in execution_target_quantities:
                target_delta = desired_quantities[instrument] - current_qty
                target_requested = abs(target_delta)
                if cfg.target_fill_ratio >= 1.0:
                    fill_cap = target_requested
                else:
                    fill_cap = floor(
                        target_requested * cfg.target_fill_ratio / cfg.board_lot
                    ) * cfg.board_lot
                target_requested_quantities[instrument] = target_requested
                target_fill_cap_quantities[instrument] = fill_cap
                execution_target_quantities[instrument] = (
                    current_qty + (fill_cap if target_delta >= 0 else -fill_cap)
                )
                if target_requested > 0 and fill_cap == 0:
                    order_rows.append({
                        "date": date_value, "instrument": instrument,
                        "side": "buy" if target_delta > 0 else "sell",
                        "target_signal_date": active_target_signal_date,
                        "target_requested_qty": target_requested,
                        "target_fill_cap_qty": fill_cap,
                        "target_fill_ratio": cfg.target_fill_ratio,
                        "requested_qty": 0, "filled_qty": 0, "fill_price": None,
                        "fees": 0.0, "commission": 0.0, "transfer_fee": 0.0,
                        "stamp_duty": 0.0, "slippage_cost": 0.0,
                        "status": "no_fill", "reason": "target_fill_cap_below_board_lot",
                    })
            executable_target = execution_target_quantities[instrument]
            delta = executable_target - current_qty
            if delta:
                attempts.append((instrument, delta, bar))

        attempts.sort(key=lambda item: (0 if item[1] < 0 else 1, item[0]))
        for instrument, delta, bar in attempts:
            if delta == 0:
                continue
            side = "buy" if delta > 0 else "sell"
            reason = None
            if bar is None:
                reason = "missing_bar"
            elif bar["is_suspended"]:
                reason = "suspended"
            elif side == "buy" and bar["is_limit_up_locked"]:
                reason = "limit_up_locked"
            elif side == "sell" and bar["is_limit_down_locked"]:
                reason = "limit_down_locked"
            elif side == "buy" and bar["limit_up"] is not None and float(bar["open"]) > float(bar["limit_up"]) + 1e-8:
                reason = "open_above_limit_up"
            elif side == "sell" and bar["limit_down"] is not None and float(bar["open"]) < float(bar["limit_down"]) - 1e-8:
                reason = "open_below_limit_down"

            requested = abs(delta)
            filled = 0
            fill_price = None
            fee = 0.0
            fee_parts = {
                "commission": 0.0,
                "transfer_fee": 0.0,
                "stamp_duty": 0.0,
            }
            slippage_cost = 0.0
            if reason is None and bar is not None:
                raw_open = float(bar["open"])
                slip = costs.slippage_bps / 10_000.0
                fill_price = raw_open * (1.0 + slip if side == "buy" else 1.0 - slip)
                if side == "sell":
                    filled = min(requested, positions.get(instrument, 0))
                    if filled:
                        notional = filled * fill_price
                        fee_parts = _fee_components(notional, side, costs)
                        fee = sum(fee_parts.values())
                        slippage_cost = filled * max(raw_open - fill_price, 0.0)
                        cash += notional - fee
                        positions[instrument] = positions.get(instrument, 0) - filled
                        if positions[instrument] == 0:
                            positions.pop(instrument)
                else:
                    affordable = requested
                    while affordable > 0:
                        notional = affordable * fill_price
                        fee = _fees(notional, side, costs)
                        if notional + fee <= cash + 1e-8:
                            break
                        affordable -= cfg.board_lot
                    filled = max(0, affordable)
                    if filled:
                        notional = filled * fill_price
                        fee_parts = _fee_components(notional, side, costs)
                        fee = sum(fee_parts.values())
                        slippage_cost = filled * max(fill_price - raw_open, 0.0)
                        cash -= notional + fee
                        positions[instrument] = positions.get(instrument, 0) + filled
                    else:
                        fee = 0.0
            status = (
                "no_fill" if filled == 0 else
                "filled" if filled == requested else "partially_filled"
            )
            if reason is None and filled == 0:
                reason = "insufficient_cash_or_below_lot"
            order_rows.append({
                "date": date_value, "instrument": instrument, "side": side,
                "target_signal_date": active_target_signal_date,
                "target_requested_qty": target_requested_quantities.get(instrument),
                "target_fill_cap_qty": target_fill_cap_quantities.get(instrument),
                "target_fill_ratio": cfg.target_fill_ratio,
                "requested_qty": requested, "filled_qty": filled,
                "fill_price": fill_price, "fees": fee,
                **fee_parts, "slippage_cost": slippage_cost,
                "status": status, "reason": reason,
            })

        for instrument, bar in day.items():
            last_close[instrument] = float(bar["close"])
        market_value = 0.0
        for instrument, quantity in positions.items():
            mark = last_close.get(instrument)
            if mark is None:
                raise ValueError(f"No close mark available for held instrument {instrument}")
            value = quantity * mark
            market_value += value
            holding_rows.append({
                "date": date_value, "instrument": instrument,
                "quantity": quantity, "mark_price": mark, "market_value": value,
            })
        nav = cash + market_value
        if cash < -1e-6:
            raise AssertionError("Cash became negative; leverage invariant violated")
        daily_rows.append({
            "date": date_value, "cash": cash, "market_value": market_value,
            "nav": nav, "daily_return": nav / previous_nav - 1.0,
            "gross_exposure": market_value / nav if nav else 0.0,
        })
        previous_nav = nav
        if date_value in targets_by_date:
            active_targets = targets_by_date[date_value]
            target_nav = nav
            # A new signal defines a new target-NAV snapshot. Recompute every
            # instrument's desired quantity at its first eligible next-session
            # open; retain that quantity only while retrying this same signal.
            desired_quantities = {}
            target_requested_quantities = {}
            target_fill_cap_quantities = {}
            execution_target_quantities = {}
            active_target_signal_date = date_value

    return BacktestResult(
        daily=pl.DataFrame(daily_rows),
        orders=pl.DataFrame(order_rows) if order_rows else pl.DataFrame(schema={
            "date": pl.String, "instrument": pl.String, "side": pl.String,
            "target_signal_date": pl.String, "target_requested_qty": pl.Int64,
            "target_fill_cap_qty": pl.Int64, "target_fill_ratio": pl.Float64,
            "requested_qty": pl.Int64, "filled_qty": pl.Int64,
            "fill_price": pl.Float64, "fees": pl.Float64,
            "commission": pl.Float64, "transfer_fee": pl.Float64,
            "stamp_duty": pl.Float64, "slippage_cost": pl.Float64,
            "status": pl.String, "reason": pl.String,
        }),
        holdings=pl.DataFrame(holding_rows) if holding_rows else pl.DataFrame(schema={
            "date": pl.String, "instrument": pl.String, "quantity": pl.Int64,
            "mark_price": pl.Float64, "market_value": pl.Float64,
        }),
    )
