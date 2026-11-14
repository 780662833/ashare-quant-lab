from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.research.features import (
    CrossSectionalFeatureConfig,
    build_cross_sectional_features,
)
from ashare_quant.research.labels import build_forward_return_labels


def market(count=35):
    sessions = []
    current = date(2024, 1, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    adjusted, raw, members = [], [], []
    for i, day in enumerate(sessions):
        for symbol, base, slope in (("600000.SH", 10.0, 0.1), ("000001.SZ", 20.0, 0.03)):
            adjusted.append({"instrument": symbol, "date": day, "adj_close": base + slope * i})
            raw.append({"instrument": symbol, "date": day, "volume": 1000 + i * 10, "amount": (base + slope * i) * 1000})
            members.append({"instrument": symbol, "date": day})
    fundamentals = []
    for symbol in ("600000.SH", "000001.SZ"):
        fundamentals.extend([
            {"instrument": symbol, "item": "roe", "report_period": "2023-09-30",
             "available_at": "2023-10-30T10:00:00+08:00", "value": 0.12,
             "pit_quality": "strict", "pit_is_exact": True},
            {"instrument": symbol, "item": "net_profit", "report_period": "2023-09-30",
             "available_at": "2023-10-30T10:00:00+08:00", "value": 100.0,
             "pit_quality": "strict", "pit_is_exact": True},
            {"instrument": symbol, "item": "roe", "report_period": "2023-12-31",
             "available_at": "2024-02-20T18:00:00+08:00", "value": 0.25,
             "pit_quality": "strict", "pit_is_exact": True},
        ])
    return (
        pl.DataFrame(adjusted), pl.DataFrame(raw), pl.DataFrame(members),
        pl.DataFrame(fundamentals), sessions,
    )


def test_feature_builder_uses_weekly_pit_members_and_strict_fundamentals():
    adj, raw, members, fundamentals, sessions = market()
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=fundamentals, trading_dates=sessions,
        config=CrossSectionalFeatureConfig(return_windows=(5, 10), volatility_window=5, activity_window=5),
    )
    row = features.filter(
        (pl.col("signal_date") == "2024-02-02") & (pl.col("instrument") == "600000.SH")
    ).row(0, named=True)
    assert row["pit_roe"] == pytest.approx(0.25)
    assert row["return_5"] is not None
    assert row["avg_amount_5"] is not None


def test_future_fundamental_publication_does_not_enter_earlier_features():
    adj, raw, members, fundamentals, sessions = market()
    config = CrossSectionalFeatureConfig(return_windows=(5,), volatility_window=5, activity_window=5)
    baseline = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=fundamentals, trading_dates=sessions, config=config,
    )
    changed = fundamentals.with_columns(
        pl.when(pl.col("report_period") == "2023-12-31").then(999.0).otherwise(pl.col("value")).alias("value")
    )
    altered = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=changed, trading_dates=sessions, config=config,
    )
    signal = "2024-02-16"
    left = baseline.filter(pl.col("signal_date") == signal).sort("instrument")
    right = altered.filter(pl.col("signal_date") == signal).sort("instrument")
    assert left["pit_roe"].to_list() == right["pit_roe"].to_list()


def test_feature_builder_rejects_non_strict_pit_rows_as_fundamental_values():
    adj, raw, members, fundamentals, sessions = market()
    bad = fundamentals.with_columns(pl.lit("reconstructed").alias("pit_quality"))
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=bad, trading_dates=sessions,
        config=CrossSectionalFeatureConfig(return_windows=(5,), volatility_window=5, activity_window=5),
    )
    assert features["pit_roe"].null_count() == features.height


def test_feature_history_missing_session_is_not_compressed_or_forward_filled():
    adj, raw, members, fundamentals, sessions = market()
    missing_date = sessions[10]
    adj = adj.filter(~((pl.col("date") == missing_date) & (pl.col("instrument") == "600000.SH")))
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=fundamentals, trading_dates=sessions,
        config=CrossSectionalFeatureConfig(return_windows=(5,), volatility_window=5, activity_window=5),
    )
    signal = sessions[15]
    row = features.filter(
        (pl.col("signal_date") == signal) & (pl.col("instrument") == "600000.SH")
    ).row(0, named=True)
    assert row["return_5"] is None


def test_forward_labels_have_exact_maturity_and_no_tail_label():
    adj, raw, members, fundamentals, sessions = market()
    labels = build_forward_return_labels(
        adjusted_bars=adj, universe_membership=members, trading_dates=sessions, horizon_sessions=5,
    )
    first = labels.filter(pl.col("signal_date") == sessions[4])
    assert first.height == 2
    assert set(first["label_end_date"].to_list()) == {sessions[9]}
    assert labels["signal_date"].max() < sessions[-5]


def test_forward_label_uses_signal_date_membership_not_future_membership():
    adj, raw, members, fundamentals, sessions = market()
    signal = sessions[4]
    members = members.filter(
        ~((pl.col("date") == signal) & (pl.col("instrument") == "600000.SH"))
    )
    labels = build_forward_return_labels(
        adjusted_bars=adj, universe_membership=members, trading_dates=sessions, horizon_sessions=5,
    )
    assert "600000.SH" not in labels.filter(pl.col("signal_date") == signal)["instrument"].to_list()


def test_invalid_label_horizon_is_rejected():
    adj, raw, members, fundamentals, sessions = market()
    with pytest.raises(ValueError, match="positive"):
        build_forward_return_labels(
            adjusted_bars=adj, universe_membership=members, trading_dates=sessions, horizon_sessions=0,
        )
