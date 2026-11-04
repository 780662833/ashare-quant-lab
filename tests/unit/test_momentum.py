from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.strategies.momentum import (
    MomentumConfig,
    momentum_scores,
    weekly_momentum_targets,
)


def make_sessions(count=35):
    sessions = []
    current = date(2024, 1, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    return sessions


def make_market(count=35):
    sessions = make_sessions(count)
    bars = []
    membership = []
    for i, session in enumerate(sessions):
        for symbol, base, slope in (
            ("600000.SH", 10.0, 0.03),
            ("000001.SZ", 20.0, 0.01),
            ("600519.SH", 30.0, -0.005),
        ):
            bars.append({
                "instrument": symbol,
                "date": session,
                "adj_close": base + slope * i,
            })
            membership.append({"instrument": symbol, "date": session})
    return pl.DataFrame(bars), pl.DataFrame(membership), sessions


def test_momentum_uses_fixed_lookback_and_skips_recent_sessions():
    bars, members, sessions = make_market()
    cfg = MomentumConfig(top_n=3, lookback_sessions=8, skip_recent_sessions=2)
    scores = momentum_scores(bars, members, sessions, cfg)
    first_signal = scores.filter(pl.col("signal_date") == "2024-01-12")
    assert first_signal.height == 3
    assert set(first_signal["start_date"].to_list()) == {"2024-01-02"}
    assert set(first_signal["end_date"].to_list()) == {"2024-01-10"}
    assert first_signal.sort("score", descending=True)["instrument"].to_list() == [
        "600000.SH", "000001.SZ", "600519.SH"
    ]


def test_weekly_targets_are_top_n_equal_weight_and_use_pit_membership():
    bars, members, sessions = make_market()
    members = members.filter(
        ~((pl.col("date") == "2024-01-12") & (pl.col("instrument") == "600000.SH"))
    )
    cfg = MomentumConfig(top_n=2, lookback_sessions=8, skip_recent_sessions=2)
    targets = weekly_momentum_targets(bars, members, sessions, cfg)
    chosen = targets.filter(pl.col("signal_date") == "2024-01-12")
    assert chosen.height == 2
    assert "600000.SH" not in chosen["instrument"].to_list()
    assert chosen["weight"].to_list() == [0.5, 0.5]


def test_missing_exact_endpoint_price_excludes_candidate_without_forward_fill():
    bars, members, sessions = make_market()
    # The Jan 12 signal uses Jan 10 as its skip-period endpoint.
    bars = bars.filter(
        ~((pl.col("date") == "2024-01-10") & (pl.col("instrument") == "600000.SH"))
    )
    scores = momentum_scores(
        bars, members, sessions,
        MomentumConfig(top_n=3, lookback_sessions=8, skip_recent_sessions=2),
    )
    chosen = scores.filter(pl.col("signal_date") == "2024-01-12")
    assert "600000.SH" not in chosen["instrument"].to_list()


def test_future_prices_do_not_change_earlier_signal_scores():
    bars, members, sessions = make_market()
    cfg = MomentumConfig(top_n=3, lookback_sessions=8, skip_recent_sessions=2)
    baseline = momentum_scores(bars, members, sessions, cfg)
    altered = bars.with_columns(
        pl.when(pl.col("date") > "2024-01-12")
        .then(pl.col("adj_close") * 100.0)
        .otherwise(pl.col("adj_close"))
        .alias("adj_close")
    )
    changed = momentum_scores(altered, members, sessions, cfg)
    signal = "2024-01-12"
    left = baseline.filter(pl.col("signal_date") == signal).sort("instrument")
    right = changed.filter(pl.col("signal_date") == signal).sort("instrument")
    assert left.select("instrument", "score").equals(right.select("instrument", "score"))


def test_momentum_rejects_duplicate_membership_and_invalid_prices():
    bars, members, sessions = make_market()
    with pytest.raises(ValueError, match="duplicate"):
        momentum_scores(
            bars, pl.concat([members, members.head(1)]), sessions,
            MomentumConfig(lookback_sessions=10, skip_recent_sessions=2),
        )
    invalid = bars.with_columns(
        pl.when(pl.col("instrument") == "600000.SH")
        .then(0.0)
        .otherwise(pl.col("adj_close"))
        .alias("adj_close")
    )
    with pytest.raises(ValueError, match="positive"):
        momentum_scores(
            invalid, members, sessions,
            MomentumConfig(lookback_sessions=10, skip_recent_sessions=2),
        )


def test_momentum_rejects_missing_weekly_point_in_time_membership():
    bars, members, sessions = make_market()
    members = members.filter(pl.col("date") != "2024-01-12")
    with pytest.raises(ValueError, match="missing weekly signal dates"):
        momentum_scores(
            bars, members, sessions,
            MomentumConfig(lookback_sessions=8, skip_recent_sessions=2),
        )


def test_momentum_rejects_duplicate_trading_sessions():
    bars, members, sessions = make_market()
    with pytest.raises(ValueError, match="unique sessions"):
        momentum_scores(
            bars, members, sessions + [sessions[0]],
            MomentumConfig(lookback_sessions=8, skip_recent_sessions=2),
        )


def test_config_requires_a_valid_momentum_window():
    with pytest.raises(ValueError, match="smaller"):
        MomentumConfig(lookback_sessions=21, skip_recent_sessions=21)
    with pytest.raises(ValueError, match="non-negative"):
        MomentumConfig(skip_recent_sessions=-1)
