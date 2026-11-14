import polars as pl
import pytest

from ashare_quant.research.ranking_diagnostics import evaluate_validation_ranking


def _frames():
    predictions = pl.DataFrame(
        {
            "signal_date": ["2024-01-05"] * 3,
            "instrument": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "predicted_return": [0.9, 0.2, 0.1],
        }
    )
    labels = pl.DataFrame(
        {
            "signal_date": ["2024-01-05"] * 3,
            "instrument": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "label_end_date": ["2024-01-12"] * 3,
            "forward_return": [0.04, 0.01, -0.02],
        }
    )
    return predictions, labels


def test_validation_ranking_reports_ic_top_n_excess_and_non_overlapping_spread():
    predictions, labels = _frames()
    diagnostics, report = evaluate_validation_ranking(
        predictions,
        labels,
        top_n=2,
        validation_start="2024-01-05",
        validation_end="2024-01-05",
        labels_mature_before="2024-01-19",
    )

    assert diagnostics.height == 1
    row = diagnostics.row(0, named=True)
    assert row["sample_count"] == 3
    assert row["spearman_rank_ic"] == pytest.approx(1.0)
    assert row["pearson_ic"] == pytest.approx(1.0)
    assert row["top_n_count"] == 2
    assert row["top_n_mean_forward_return"] == pytest.approx(0.025)
    assert row["universe_mean_forward_return"] == pytest.approx(0.01)
    assert row["top_n_excess_forward_return"] == pytest.approx(0.015)
    assert row["diagnostic_bucket_count"] == 1
    assert row["top_minus_bottom_forward_return"] == pytest.approx(0.06)
    assert report["status"] == "evaluated"
    assert report["label_coverage_rate"] == pytest.approx(1.0)
    assert report["mean_spearman_rank_ic"] == pytest.approx(1.0)
    assert report["positive_spearman_rank_ic_ratio"] == pytest.approx(1.0)


def test_validation_ranking_reports_partial_label_coverage():
    predictions, labels = _frames()
    labels = labels.filter(pl.col("instrument") != "000003.SZ")
    diagnostics, report = evaluate_validation_ranking(
        predictions,
        labels,
        top_n=1,
        validation_start="2024-01-05",
        validation_end="2024-01-05",
        labels_mature_before="2024-01-19",
    )

    assert diagnostics.height == 1
    assert diagnostics["sample_count"].item() == 2
    assert report["prediction_rows"] == 3
    assert report["matched_labeled_rows"] == 2
    assert report["label_coverage_rate"] == pytest.approx(2 / 3)


def test_validation_ranking_rejects_label_maturing_on_test_start():
    predictions, labels = _frames()
    labels = labels.with_columns(pl.lit("2024-01-19").alias("label_end_date"))

    with pytest.raises(ValueError, match="strictly before the test cutoff"):
        evaluate_validation_ranking(
            predictions,
            labels,
            top_n=2,
            validation_start="2024-01-05",
            validation_end="2024-01-05",
            labels_mature_before="2024-01-19",
        )


def test_validation_ranking_rejects_test_period_rows_in_validation_inputs():
    predictions, labels = _frames()
    predictions = predictions.with_columns(pl.lit("2024-01-26").alias("signal_date"))

    with pytest.raises(ValueError, match="outside the validation window"):
        evaluate_validation_ranking(
            predictions,
            labels,
            top_n=2,
            validation_start="2024-01-05",
            validation_end="2024-01-05",
            labels_mature_before="2024-01-19",
        )


def test_validation_ranking_rejects_duplicate_prediction_keys():
    predictions, labels = _frames()
    predictions = pl.concat([predictions, predictions.head(1)])

    with pytest.raises(ValueError, match="duplicate signal_date/instrument"):
        evaluate_validation_ranking(
            predictions,
            labels,
            top_n=2,
            validation_start="2024-01-05",
            validation_end="2024-01-05",
            labels_mature_before="2024-01-19",
        )


def test_validation_ranking_returns_empty_diagnostics_when_no_labels_match():
    predictions, labels = _frames()
    labels = labels.with_columns(pl.lit("600000.SH").alias("instrument"))
    diagnostics, report = evaluate_validation_ranking(
        predictions,
        labels,
        top_n=2,
        validation_start="2024-01-05",
        validation_end="2024-01-05",
        labels_mature_before="2024-01-19",
    )

    assert diagnostics.is_empty()
    assert report["status"] == "no_matched_validation_labels"
    assert report["matched_labeled_rows"] == 0
    assert report["mean_spearman_rank_ic"] is None
