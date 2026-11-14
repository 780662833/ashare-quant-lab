import polars as pl
import pytest

from ashare_quant.research.validation_stability import (
    ValidationRankingAcceptancePolicy,
    summarize_validation_stability,
)


def _inputs():
    diagnostics = [
        pl.DataFrame(
            {
                "signal_date": ["2024-01-05", "2024-01-12"],
                "sample_count": [4, 4],
                "spearman_rank_ic": [0.2, 0.1],
                "top_n_excess_forward_return": [0.02, 0.01],
                "top_minus_bottom_forward_return": [0.04, 0.03],
            }
        ),
        pl.DataFrame(
            {
                "signal_date": ["2024-01-19", "2024-01-26"],
                "sample_count": [3, 4],
                "spearman_rank_ic": [0.3, 0.0],
                "top_n_excess_forward_return": [-0.03, -0.005],
                "top_minus_bottom_forward_return": [0.05, -0.01],
            }
        ),
    ]
    reports = [
        {
            "status": "evaluated",
            "validation_period": {"start": "2024-01-05", "end": "2024-01-12"},
            "labels_mature_before": "2024-01-19",
            "prediction_rows": 8,
            "matched_labeled_rows": 8,
            "label_coverage_rate": 1.0,
            "evaluated_signal_date_count": 2,
        },
        {
            "status": "evaluated",
            "validation_period": {"start": "2024-01-19", "end": "2024-01-26"},
            "labels_mature_before": "2024-02-02",
            "prediction_rows": 12,
            "matched_labeled_rows": 10,
            "label_coverage_rate": 10 / 12,
            "evaluated_signal_date_count": 2,
        },
    ]
    return diagnostics, reports


def _policy(**overrides):
    values = {
        "policy_id": "validation-v1-test",
        "minimum_fold_count": 2,
        "minimum_signal_date_count": 4,
        "minimum_rank_ic_observation_count": 4,
        "minimum_cross_section_size": 3,
        "minimum_label_coverage_rate": 0.9,
        "minimum_mean_spearman_rank_ic": 0.1,
        "minimum_positive_spearman_rank_ic_ratio": 0.7,
        "minimum_positive_top_n_excess_fold_ratio": 0.5,
        "minimum_positive_rank_ic_fold_ratio": 0.5,
        "minimum_rank_icir": 0.5,
        "minimum_mean_top_n_excess_forward_return": -0.01,
    }
    values.update(overrides)
    return ValidationRankingAcceptancePolicy(**values)


def test_cross_fold_validation_stability_applies_explicit_policy():
    diagnostics, reports = _inputs()
    summary = summarize_validation_stability(
        diagnostics,
        reports,
        fold_ids=["fold_001", "fold_002"],
        policy=_policy(),
    )

    assert summary["decision"] == "ACCEPTED"
    assert summary["policy_id"] == "validation-v1-test"
    assert len(summary["policy_fingerprint"]) == 64
    assert summary["fold_count"] == 2
    assert summary["evaluated_fold_count"] == 2
    assert summary["signal_date_count"] == 4
    assert summary["rank_ic_observation_count"] == 4
    assert summary["minimum_weekly_cross_section_size"] == 3
    assert summary["label_coverage_rate"] == pytest.approx(18 / 20)
    assert summary["mean_spearman_rank_ic"] == pytest.approx(0.15)
    assert summary["positive_spearman_rank_ic_ratio"] == pytest.approx(0.75)
    assert summary["positive_rank_ic_fold_ratio"] == pytest.approx(1.0)
    assert summary["positive_top_n_excess_fold_ratio"] == pytest.approx(0.5)
    assert summary["rank_icir"] is not None
    assert all(item["passed"] for item in summary["criteria"].values())


def test_cross_fold_validation_policy_rejects_weak_ranking():
    diagnostics, reports = _inputs()
    summary = summarize_validation_stability(
        diagnostics,
        reports,
        policy=_policy(minimum_mean_spearman_rank_ic=0.4),
    )

    assert summary["decision"] == "REJECTED"
    assert "minimum_mean_spearman_rank_ic" in summary["failed_criteria"]
    assert summary["criteria"]["minimum_mean_spearman_rank_ic"]["status"] == "FAIL"



def test_cross_fold_policy_requires_minimum_cross_section_and_ic_sample():
    diagnostics, reports = _inputs()
    diagnostics[1] = diagnostics[1].with_columns(
        pl.Series("sample_count", [2, 4])
    )
    summary = summarize_validation_stability(
        diagnostics,
        reports,
        policy=_policy(minimum_cross_section_size=3),
    )

    assert summary["minimum_weekly_cross_section_size"] == 2
    assert summary["decision"] == "INSUFFICIENT_EVIDENCE"
    assert "minimum_cross_section_size" in summary["failed_criteria"]


def test_cross_fold_validation_policy_checks_positive_ic_fold_consistency():
    diagnostics, reports = _inputs()
    diagnostics[1] = diagnostics[1].with_columns(
        pl.Series("spearman_rank_ic", [-0.3, -0.1])
    )
    summary = summarize_validation_stability(
        diagnostics,
        reports,
        policy=_policy(minimum_positive_rank_ic_fold_ratio=0.75),
    )

    assert summary["positive_rank_ic_fold_ratio"] == pytest.approx(0.5)
    assert summary["decision"] == "REJECTED"
    assert "minimum_positive_rank_ic_fold_ratio" in summary["failed_criteria"]


def test_cross_fold_validation_policy_fails_closed_on_insufficient_folds():
    diagnostics, reports = _inputs()
    summary = summarize_validation_stability(
        diagnostics,
        reports,
        policy=_policy(minimum_fold_count=3),
    )

    assert summary["decision"] == "INSUFFICIENT_EVIDENCE"
    assert "minimum_fold_count" in summary["failed_criteria"]


def test_cross_fold_stability_is_descriptive_without_explicit_policy():
    diagnostics, reports = _inputs()
    summary = summarize_validation_stability(diagnostics, reports)

    assert summary["decision"] == "NOT_ASSESSED"
    assert summary["policy_id"] is None
    assert summary["acceptance_policy"] is None
    assert summary["mean_spearman_rank_ic"] == pytest.approx(0.15)


def test_cross_fold_stability_rejects_validation_window_or_maturity_mismatch():
    diagnostics, reports = _inputs()
    reports[0] = {
        **reports[0],
        "labels_mature_before": "2024-01-12",
    }
    with pytest.raises(ValueError, match="must end before test cutoff"):
        summarize_validation_stability(diagnostics, reports)


def test_cross_fold_stability_rejects_overlapping_validation_signal_dates():
    diagnostics, reports = _inputs()
    diagnostics[1] = diagnostics[1].with_columns(
        pl.Series("signal_date", ["2024-01-12", "2024-01-26"])
    )
    reports[1] = {
        **reports[1],
        "validation_period": {"start": "2024-01-12", "end": "2024-01-26"},
    }
    with pytest.raises(ValueError, match="overlap across folds"):
        summarize_validation_stability(diagnostics, reports)


def test_cross_fold_stability_validates_reported_label_coverage():
    diagnostics, reports = _inputs()
    reports[1] = {**reports[1], "label_coverage_rate": 1.0}
    with pytest.raises(ValueError, match="does not match row counts"):
        summarize_validation_stability(diagnostics, reports)


def test_validation_policy_requires_explicit_valid_thresholds():
    with pytest.raises(ValueError, match="minimum_fold_count"):
        _policy(minimum_fold_count=1)
    with pytest.raises(ValueError, match="minimum_label_coverage_rate"):
        _policy(minimum_label_coverage_rate=1.1)
