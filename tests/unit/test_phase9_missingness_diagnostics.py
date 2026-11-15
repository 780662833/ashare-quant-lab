import polars as pl
import pytest

from ashare_quant.research.missingness_diagnostics import (
    evaluate_feature_coverage,
    evaluate_validation_feature_missingness,
)


def _frames():
    features = pl.DataFrame(
        {
            "signal_date": ["2024-01-05"] * 6,
            "instrument": [
                "000001.SZ", "000002.SZ", "000003.SZ",
                "000004.SZ", "000005.SZ", "000006.SZ",
            ],
            "return_5": [0.05, 0.04, 0.03, 0.02, 0.01, None],
            "pit_roe": [None, 0.10, None, 0.20, 0.30, None],
            "pit_net_profit": [1.0, None, 3.0, None, 5.0, 6.0],
        }
    )
    predictions = pl.DataFrame(
        {
            "signal_date": ["2024-01-05"] * 5,
            "instrument": [
                "000001.SZ", "000002.SZ", "000003.SZ",
                "000004.SZ", "000005.SZ",
            ],
            "predicted_return": [0.90, 0.80, 0.70, 0.60, 0.50],
        }
    )
    labels = pl.DataFrame(
        {
            "signal_date": ["2024-01-05"] * 4,
            "instrument": [
                "000001.SZ", "000002.SZ", "000003.SZ", "000005.SZ",
            ],
            "label_end_date": ["2024-01-12"] * 4,
            "forward_return": [0.04, -0.02, 0.02, 0.01],
        }
    )
    return features, predictions, labels


def evaluate(features, predictions, labels):
    return evaluate_validation_feature_missingness(
        features,
        predictions,
        labels,
        top_n=3,
        validation_start="2024-01-05",
        validation_end="2024-01-05",
        labels_mature_before="2024-01-19",
    )


def test_reports_pit_coverage_top_n_mix_and_matured_group_outcomes():
    features, predictions, labels = _frames()
    diagnostics, report = evaluate(features, predictions, labels)

    any_missing = diagnostics.filter(
        pl.col("pit_feature") == "__any_pit_missing__"
    ).row(0, named=True)
    assert any_missing["universe_rows"] == 6
    assert any_missing["universe_missing_rows"] == 5
    assert any_missing["universe_missing_rate"] == pytest.approx(5 / 6)
    assert any_missing["eligible_rows"] == 5
    assert any_missing["prediction_rows"] == 5
    assert any_missing["prediction_missing_rows"] == 4
    assert any_missing["prediction_missing_rate"] == pytest.approx(4 / 5)
    assert any_missing["top_n_rows"] == 3
    assert any_missing["top_n_missing_rows"] == 3
    assert any_missing["top_n_missing_rate"] == pytest.approx(1.0)
    assert any_missing["missing_mean_predicted_return"] == pytest.approx(0.75)
    assert any_missing["observed_mean_predicted_return"] == pytest.approx(0.50)
    assert any_missing["missing_labeled_rows"] == 3
    assert any_missing["missing_mean_forward_return"] == pytest.approx(
        (0.04 - 0.02 + 0.02) / 3
    )
    assert any_missing["missing_positive_forward_ratio"] == pytest.approx(2 / 3)
    assert any_missing["observed_mean_forward_return"] == pytest.approx(0.01)

    roe = diagnostics.filter(pl.col("pit_feature") == "pit_roe").row(0, named=True)
    assert roe["universe_missing_rows"] == 3
    assert roe["prediction_missing_rows"] == 2
    assert roe["top_n_missing_rows"] == 2

    net_profit = diagnostics.filter(
        pl.col("pit_feature") == "pit_net_profit"
    ).row(0, named=True)
    assert net_profit["universe_missing_rows"] == 2
    assert net_profit["prediction_missing_rows"] == 2
    assert net_profit["top_n_missing_rows"] == 1

    assert report["status"] == "evaluated"
    assert report["by_feature"]["__any_pit_missing__"]["top_n_missing_rate"] == 1.0
    assert report["metadata_dimensions_deferred"] == [
        "market_cap", "listing_age", "industry"
    ]


def test_rejects_prediction_rows_that_bypass_feature_eligibility():
    features, predictions, labels = _frames()
    predictions = pl.concat([predictions, pl.DataFrame({
        "signal_date": ["2024-01-05"],
        "instrument": ["000006.SZ"],
        "predicted_return": [0.1],
    })])
    with pytest.raises(ValueError, match="exactly cover feature-eligible rows"):
        evaluate(features, predictions, labels)


def test_rejects_validation_labels_that_are_not_mature_before_cutoff():
    features, predictions, labels = _frames()
    labels = labels.with_columns(pl.lit("2024-01-19").alias("label_end_date"))
    with pytest.raises(ValueError, match="mature strictly before the test cutoff"):
        evaluate(features, predictions, labels)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nonfinite_nonnull_feature_sources(bad_value):
    features, predictions, labels = _frames()
    features = features.with_columns(
        pl.when(pl.col("instrument") == "000001.SZ")
        .then(bad_value)
        .otherwise(pl.col("pit_roe"))
        .alias("pit_roe")
    )
    with pytest.raises(ValueError, match="non-null NaN or infinite source values"):
        evaluate(features, predictions, labels)


def test_test_window_coverage_is_label_free_and_reports_top_n_pit_missingness():
    features, predictions, _ = _frames()
    coverage, report = evaluate_feature_coverage(
        features,
        predictions,
        top_n=2,
        start_date="2024-01-05",
        end_date="2024-01-05",
    )

    any_missing = coverage.filter(
        pl.col("pit_feature") == "__any_pit_missing__"
    ).row(0, named=True)
    assert any_missing["universe_rows"] == 6
    assert any_missing["universe_missing_rows"] == 5
    assert any_missing["eligible_rows"] == 5
    assert any_missing["prediction_rows"] == 5
    assert any_missing["top_n_rows"] == 2
    assert any_missing["top_n_missing_rows"] == 2
    assert any_missing["top_n_missing_rate"] == pytest.approx(1.0)
    assert report["status"] == "evaluated"
    assert report["period"] == {"start": "2024-01-05", "end": "2024-01-05"}
    assert "labels_mature_before" not in report
    assert "forward_return" not in coverage.columns
