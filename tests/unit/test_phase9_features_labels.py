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
            raw_close = base + slope * i
            raw_open = raw_close - 0.05
            adjusted.append({
                "instrument": symbol, "date": day, "adj_close": raw_close * 2.0,
                "adj_is_exact": True,
            })
            raw.append({
                "instrument": symbol, "date": day, "open": raw_open, "close": raw_close,
                "volume": 1000 + i * 10, "amount": raw_close * 1000,
            })
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
    adj, raw, members, fundamentals, sessions = market(45)
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=fundamentals, trading_dates=sessions,
        config=CrossSectionalFeatureConfig(return_windows=(5, 10), volatility_window=5, activity_window=5),
    )
    row = features.filter(
        (pl.col("signal_date") == "2024-02-23") & (pl.col("instrument") == "600000.SH")
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
    missing_date = sessions[9]
    adj = adj.filter(~((pl.col("date") == missing_date) & (pl.col("instrument") == "600000.SH")))
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=fundamentals, trading_dates=sessions,
        config=CrossSectionalFeatureConfig(return_windows=(5,), volatility_window=5, activity_window=5),
    )
    signal = sessions[14]
    row = features.filter(
        (pl.col("signal_date") == signal) & (pl.col("instrument") == "600000.SH")
    ).row(0, named=True)
    assert row["return_5"] is None


def test_forward_labels_align_entry_and_exit_with_next_open_execution():
    adj, raw, members, fundamentals, sessions = market()
    labels = build_forward_return_labels(
        adjusted_bars=adj, execution_bars=raw,
        universe_membership=members, trading_dates=sessions,
    )
    first = labels.filter(pl.col("signal_date") == sessions[4])
    assert first.height == 2
    assert set(first["label_entry_date"].to_list()) == {sessions[5]}
    assert set(first["label_end_date"].to_list()) == {sessions[10]}
    row = first.filter(pl.col("instrument") == "600000.SH").row(0, named=True)
    entry_raw_open = 10.0 + 0.1 * 5 - 0.05
    exit_raw_open = 10.0 + 0.1 * 10 - 0.05
    assert row["forward_return"] == pytest.approx(exit_raw_open / entry_raw_open - 1.0)
    assert labels["signal_date"].max() < sessions[-5]


def test_execution_aligned_labels_follow_actual_weekly_schedule_in_short_holiday_week():
    adj, raw, members, fundamentals, sessions = market()
    # Remove Monday from the second week, leaving a four-session trading week.
    short_week_sessions = [value for value in sessions if value != sessions[5]]
    short_adj = adj.filter(pl.col("date") != sessions[5])
    short_raw = raw.filter(pl.col("date") != sessions[5])
    short_members = members.filter(pl.col("date") != sessions[5])
    labels = build_forward_return_labels(
        adjusted_bars=short_adj, execution_bars=short_raw,
        universe_membership=short_members, trading_dates=short_week_sessions,
    )
    first = labels.filter(pl.col("signal_date") == sessions[4])
    assert first.height == 2
    # Entry is Tuesday because Monday is absent; exit is the open after the
    # next week's Friday signal, not a hard-coded five-session offset.
    assert set(first["label_entry_date"].to_list()) == {sessions[6]}
    assert set(first["label_end_date"].to_list()) == {sessions[10]}


def test_forward_label_uses_signal_date_membership_not_future_membership():
    adj, raw, members, fundamentals, sessions = market()
    signal = sessions[4]
    members = members.filter(
        ~((pl.col("date") == signal) & (pl.col("instrument") == "600000.SH"))
    )
    labels = build_forward_return_labels(
        adjusted_bars=adj, execution_bars=raw,
        universe_membership=members, trading_dates=sessions,
    )
    assert "600000.SH" not in labels.filter(pl.col("signal_date") == signal)["instrument"].to_list()


def test_forward_labels_require_both_adjusted_open_endpoints():
    adj, raw, members, fundamentals, sessions = market()
    missing_exit_open = raw.filter(
        ~((pl.col("date") == sessions[10]) & (pl.col("instrument") == "600000.SH"))
    )
    labels = build_forward_return_labels(
        adjusted_bars=adj, execution_bars=missing_exit_open,
        universe_membership=members, trading_dates=sessions,
    )
    first = labels.filter(pl.col("signal_date") == sessions[4])
    assert "600000.SH" not in first["instrument"].to_list()
    assert "000001.SZ" in first["instrument"].to_list()


def test_invalid_price_data_and_calendar_are_rejected():
    adj, raw, members, fundamentals, sessions = market()
    bad = raw.with_columns(
        pl.when(pl.col("instrument") == "600000.SH")
        .then(0.0).otherwise(pl.col("open")).alias("open")
    )
    with pytest.raises(ValueError, match="finite and positive"):
        build_forward_return_labels(
            adjusted_bars=adj, execution_bars=bad,
            universe_membership=members, trading_dates=sessions,
        )
    with pytest.raises(ValueError, match="outside trading calendar"):
        build_forward_return_labels(
            adjusted_bars=adj, execution_bars=raw,
            universe_membership=members, trading_dates=sessions[:-1],
        )



def test_forward_labels_reject_non_exact_hfq_adjustment_rows():
    adj, raw, members, fundamentals, sessions = market()
    bad = adj.with_columns(
        pl.when(pl.col("date") == sessions[5])
        .then(False).otherwise(pl.col("adj_is_exact")).alias("adj_is_exact")
    )
    with pytest.raises(ValueError, match="finite, positive and exact"):
        build_forward_return_labels(
            adjusted_bars=bad, execution_bars=raw,
            universe_membership=members, trading_dates=sessions,
        )


def test_forward_labels_reject_unsorted_calendar_without_reordering():
    adj, raw, members, fundamentals, sessions = market()
    unsorted = [sessions[1], sessions[0], *sessions[2:]]
    with pytest.raises(ValueError, match="strictly chronological"):
        build_forward_return_labels(
            adjusted_bars=adj, execution_bars=raw,
            universe_membership=members, trading_dates=unsorted,
        )



def test_feature_builder_rejects_non_exact_hfq_prices():
    adj, raw, members, fundamentals, sessions = market()
    bad = adj.with_columns(
        pl.when(pl.col("date") == sessions[5])
        .then(False).otherwise(pl.col("adj_is_exact")).alias("adj_is_exact")
    )
    with pytest.raises(ValueError, match="finite, positive and exact"):
        build_cross_sectional_features(
            adjusted_bars=bad, execution_bars=raw,
            universe_membership=members, fundamentals=fundamentals,
            trading_dates=sessions,
            config=CrossSectionalFeatureConfig(
                return_windows=(5,), volatility_window=5, activity_window=5
            ),
        )


def test_feature_builder_requires_exactness_provenance_column():
    adj, raw, members, fundamentals, sessions = market()
    with pytest.raises(ValueError, match="adj_is_exact"):
        build_cross_sectional_features(
            adjusted_bars=adj.drop("adj_is_exact"), execution_bars=raw,
            universe_membership=members, fundamentals=fundamentals,
            trading_dates=sessions,
            config=CrossSectionalFeatureConfig(
                return_windows=(5,), volatility_window=5, activity_window=5
            ),
        )


def test_feature_builder_rejects_duplicate_strict_pit_fact_keys():
    adj, raw, members, fundamentals, sessions = market()
    duplicate = pl.concat([fundamentals, fundamentals.head(1)])
    with pytest.raises(ValueError, match="duplicate strict PIT fact keys"):
        build_cross_sectional_features(
            adjusted_bars=adj, execution_bars=raw,
            universe_membership=members, fundamentals=duplicate,
            trading_dates=sessions,
            config=CrossSectionalFeatureConfig(
                return_windows=(5,), volatility_window=5, activity_window=5
            ),
        )


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf")])
def test_feature_builder_rejects_non_finite_strict_pit_values(bad_value):
    adj, raw, members, fundamentals, sessions = market()
    bad = fundamentals.with_columns(
        pl.when(
            (pl.col("instrument") == "600000.SH")
            & (pl.col("item") == "roe")
        ).then(bad_value).otherwise(pl.col("value")).alias("value")
    )
    with pytest.raises(ValueError, match="strict PIT value must be finite"):
        build_cross_sectional_features(
            adjusted_bars=adj, execution_bars=raw,
            universe_membership=members, fundamentals=bad,
            trading_dates=sessions,
            config=CrossSectionalFeatureConfig(
                return_windows=(5,), volatility_window=5, activity_window=5
            ),
        )


def test_feature_builder_rejects_unsorted_calendar_without_reordering():
    adj, raw, members, fundamentals, sessions = market()
    unsorted = [sessions[1], sessions[0], *sessions[2:]]
    with pytest.raises(ValueError, match="strictly chronological"):
        build_cross_sectional_features(
            adjusted_bars=adj, execution_bars=raw,
            universe_membership=members, fundamentals=fundamentals,
            trading_dates=unsorted,
            config=CrossSectionalFeatureConfig(
                return_windows=(5,), volatility_window=5, activity_window=5
            ),
        )



def test_pit_features_prefer_latest_known_report_period_over_late_old_period_revision():
    adj, raw, members, fundamentals, sessions = market(45)
    # Dec-2023 is already known by Feb-20. A restatement of Sep-2023 is
    # announced later, on Feb-22. That older period must not eclipse Dec-2023.
    additional = pl.DataFrame([
        {
            "instrument": "600000.SH", "item": "roe", "report_period": "2023-09-30",
            "available_at": "2024-02-22T14:00:00+08:00", "value": 0.88,
            "pit_quality": "strict", "pit_is_exact": True,
        },
        {
            "instrument": "600000.SH", "item": "roe", "report_period": "2023-12-31",
            "available_at": "2024-02-22T10:00:00+08:00", "value": 0.35,
            "pit_quality": "strict", "pit_is_exact": True,
        },
    ])
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=pl.concat([fundamentals, additional]), trading_dates=sessions,
        config=CrossSectionalFeatureConfig(
            return_windows=(5,), volatility_window=5, activity_window=5
        ),
    )
    row = features.filter(
        (pl.col("signal_date") == "2024-02-23")
        & (pl.col("instrument") == "600000.SH")
    ).row(0, named=True)
    assert row["pit_roe"] == pytest.approx(0.35)


def test_pit_features_use_latest_revision_within_latest_report_period():
    adj, raw, members, fundamentals, sessions = market(45)
    additional = pl.DataFrame([{
        "instrument": "600000.SH", "item": "roe", "report_period": "2023-12-31",
        "available_at": "2024-02-22T10:00:00+08:00", "value": 0.35,
        "pit_quality": "strict", "pit_is_exact": True,
    }])
    features = build_cross_sectional_features(
        adjusted_bars=adj, execution_bars=raw, universe_membership=members,
        fundamentals=pl.concat([fundamentals, additional]), trading_dates=sessions,
        config=CrossSectionalFeatureConfig(
            return_windows=(5,), volatility_window=5, activity_window=5
        ),
    )
    row = features.filter(
        (pl.col("signal_date") == "2024-02-23")
        & (pl.col("instrument") == "600000.SH")
    ).row(0, named=True)
    assert row["pit_roe"] == pytest.approx(0.35)


@pytest.mark.parametrize("bad_period", ["2023Q4", "2024-2-01", "2024-03-01"])
def test_pit_features_reject_noncanonical_or_future_report_period(bad_period):
    adj, raw, members, fundamentals, sessions = market()
    bad = fundamentals.with_columns(
        pl.when(
            (pl.col("instrument") == "600000.SH")
            & (pl.col("item") == "roe")
            & (pl.col("report_period") == "2023-12-31")
        ).then(pl.lit(bad_period)).otherwise(pl.col("report_period")).alias("report_period")
    )
    with pytest.raises(ValueError, match="report_period"):
        build_cross_sectional_features(
            adjusted_bars=adj, execution_bars=raw, universe_membership=members,
            fundamentals=bad, trading_dates=sessions,
            config=CrossSectionalFeatureConfig(
                return_windows=(5,), volatility_window=5, activity_window=5
            ),
        )



def test_zero_volume_window_produces_null_volume_ratio_not_nonfinite_values():
    adj, raw, members, fundamentals, sessions = market()
    zero_volume = raw.with_columns(
        pl.when(pl.col("instrument") == "600000.SH")
        .then(0.0).otherwise(pl.col("volume")).alias("volume"),
        pl.when(pl.col("instrument") == "600000.SH")
        .then(0.0).otherwise(pl.col("amount")).alias("amount"),
    )
    features = build_cross_sectional_features(
        adjusted_bars=adj,
        execution_bars=zero_volume,
        universe_membership=members,
        fundamentals=fundamentals,
        trading_dates=sessions,
        config=CrossSectionalFeatureConfig(
            return_windows=(5,), volatility_window=5, activity_window=5
        ),
    )
    row = features.filter(
        (pl.col("signal_date") == sessions[14])
        & (pl.col("instrument") == "600000.SH")
    ).row(0, named=True)
    assert row["avg_amount_5"] == pytest.approx(0.0)
    assert row["volume_ratio_5"] is None
