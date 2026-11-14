"""Cross-fold validation stability summaries and explicit acceptance policies.

This module evaluates only validation diagnostics and validation labels that
were already required to mature before each fold's test cutoff. It never reads
test-period returns or OOS backtest metrics.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from statistics import mean, median, stdev
from typing import Any, Mapping, Sequence

import polars as pl

POLICY_VERSION = "validation-ranking-policy-v1"
_DIAGNOSTIC_COLUMNS = {
    "signal_date",
    "sample_count",
    "spearman_rank_ic",
    "top_n_excess_forward_return",
    "top_minus_bottom_forward_return",
}


def _iso_date(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return value


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _ratio(value: Any, name: str) -> float:
    result = _finite_number(value, name)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return result


@dataclass(frozen=True, slots=True)
class ValidationRankingAcceptancePolicy:
    """Versioned, caller-supplied thresholds; no implicit financial defaults."""

    policy_id: str
    minimum_fold_count: int
    minimum_signal_date_count: int
    minimum_rank_ic_observation_count: int
    minimum_cross_section_size: int
    minimum_label_coverage_rate: float
    minimum_mean_spearman_rank_ic: float
    minimum_positive_spearman_rank_ic_ratio: float
    minimum_positive_top_n_excess_fold_ratio: float
    minimum_positive_rank_ic_fold_ratio: float | None = None
    minimum_rank_icir: float | None = None
    minimum_mean_top_n_excess_forward_return: float | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.policy_id)
        ):
            raise ValueError("policy_id must be 1-64 safe ASCII characters")
        for name, value in (
            ("minimum_fold_count", self.minimum_fold_count),
            ("minimum_signal_date_count", self.minimum_signal_date_count),
            ("minimum_rank_ic_observation_count", self.minimum_rank_ic_observation_count),
            ("minimum_cross_section_size", self.minimum_cross_section_size),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer >= 2")
        _ratio(self.minimum_label_coverage_rate, "minimum_label_coverage_rate")
        rank_ic = _finite_number(
            self.minimum_mean_spearman_rank_ic, "minimum_mean_spearman_rank_ic"
        )
        if not -1.0 <= rank_ic <= 1.0:
            raise ValueError("minimum_mean_spearman_rank_ic must be in [-1, 1]")
        _ratio(
            self.minimum_positive_spearman_rank_ic_ratio,
            "minimum_positive_spearman_rank_ic_ratio",
        )
        _ratio(
            self.minimum_positive_top_n_excess_fold_ratio,
            "minimum_positive_top_n_excess_fold_ratio",
        )
        if self.minimum_positive_rank_ic_fold_ratio is not None:
            _ratio(
                self.minimum_positive_rank_ic_fold_ratio,
                "minimum_positive_rank_ic_fold_ratio",
            )
        if self.minimum_rank_icir is not None:
            _finite_number(self.minimum_rank_icir, "minimum_rank_icir")
        if self.minimum_mean_top_n_excess_forward_return is not None:
            _finite_number(
                self.minimum_mean_top_n_excess_forward_return,
                "minimum_mean_top_n_excess_forward_return",
            )

    def to_dict(self) -> dict[str, Any]:
        return {"policy_version": POLICY_VERSION, **asdict(self)}

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarize_validation_stability(
    fold_diagnostics: Sequence[pl.DataFrame],
    fold_reports: Sequence[Mapping[str, Any]],
    *,
    fold_ids: Sequence[str] | None = None,
    policy: ValidationRankingAcceptancePolicy | None = None,
) -> dict[str, Any]:
    """Summarize weekly validation IC and cross-fold consistency.

    Fold means are kept separate from week-level IC observations. The ICIR is
    mean weekly Spearman IC / sample standard deviation of weekly IC; it is not
    annualized and does not assume independent weekly observations. No p-value,
    confidence interval, or test-period quantity is calculated.
    """
    if not isinstance(fold_diagnostics, Sequence) or not isinstance(fold_reports, Sequence):
        raise TypeError("fold_diagnostics and fold_reports must be sequences")
    if len(fold_diagnostics) != len(fold_reports) or len(fold_reports) < 1:
        raise ValueError("fold_diagnostics and fold_reports must have equal non-zero lengths")
    if fold_ids is None:
        ids = [f"fold_{index:03d}" for index in range(1, len(fold_reports) + 1)]
    else:
        ids = list(fold_ids)
        if (
            len(ids) != len(fold_reports)
            or any(not isinstance(value, str) or not value for value in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("fold_ids must be unique, non-empty strings matching the fold count")

    all_dates: set[str] = set()
    all_weekly_ic: list[float] = []
    cross_section_sizes: list[int] = []
    all_weekly_top_excess: list[float] = []
    all_weekly_top_bottom: list[float] = []
    fold_ic_means: list[float] = []
    fold_top_excess_means: list[float] = []
    fold_top_bottom_means: list[float] = []
    total_predictions = 0
    total_matched = 0
    evaluated_folds = 0
    fold_details: list[dict[str, Any]] = []
    total_diagnostic_dates = 0
    missing_ic_dates = 0

    for fold_id, diagnostics, report in zip(ids, fold_diagnostics, fold_reports, strict=True):
        if not isinstance(diagnostics, pl.DataFrame):
            raise TypeError(f"{fold_id} diagnostics must be a Polars DataFrame")
        if not isinstance(report, Mapping):
            raise TypeError(f"{fold_id} validation report must be a mapping")
        missing = _DIAGNOSTIC_COLUMNS.difference(diagnostics.columns)
        if missing:
            raise ValueError(f"{fold_id} validation diagnostics missing columns: {sorted(missing)}")

        period = report.get("validation_period")
        if not isinstance(period, Mapping):
            raise ValueError(f"{fold_id} validation report is missing validation_period")
        start = _iso_date(period.get("start"), f"{fold_id}.validation_period.start")
        end = _iso_date(period.get("end"), f"{fold_id}.validation_period.end")
        cutoff = _iso_date(report.get("labels_mature_before"), f"{fold_id}.labels_mature_before")
        if start > end or end >= cutoff:
            raise ValueError(f"{fold_id} validation period must end before test cutoff")

        status = report.get("status")
        if status not in {"evaluated", "no_matched_validation_labels"}:
            raise ValueError(f"{fold_id} has unsupported validation status")
        prediction_count = report.get("prediction_rows")
        matched_count = report.get("matched_labeled_rows")
        for name, value in (
            ("prediction_rows", prediction_count),
            ("matched_labeled_rows", matched_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{fold_id}.{name} must be a non-negative integer")
        if matched_count > prediction_count:
            raise ValueError(f"{fold_id} matched label count exceeds prediction count")
        coverage = (
            matched_count / prediction_count if prediction_count else 0.0
        )
        reported_coverage = _ratio(
            report.get("label_coverage_rate"), f"{fold_id}.label_coverage_rate"
        )
        if abs(coverage - reported_coverage) > 1e-9:
            raise ValueError(f"{fold_id} label coverage does not match row counts")
        if (status == "evaluated") != (matched_count > 0):
            raise ValueError(f"{fold_id} validation status conflicts with matched label count")
        expected_signal_dates = report.get("evaluated_signal_date_count")
        if (
            isinstance(expected_signal_dates, bool)
            or not isinstance(expected_signal_dates, int)
            or expected_signal_dates != diagnostics.height
        ):
            raise ValueError(f"{fold_id} evaluated signal-date count does not match diagnostics")
        if status == "no_matched_validation_labels" and not diagnostics.is_empty():
            raise ValueError(f"{fold_id} has diagnostics despite having no matched labels")
        total_predictions += prediction_count
        total_matched += matched_count
        evaluated_folds += int(status == "evaluated")

        fold_ic: list[float] = []
        fold_top_excess: list[float] = []
        fold_top_bottom: list[float] = []
        for row in diagnostics.iter_rows(named=True):
            signal_date = _iso_date(row["signal_date"], f"{fold_id}.signal_date")
            if not start <= signal_date <= end or signal_date >= cutoff:
                raise ValueError(f"{fold_id} diagnostic signal date is outside validation")
            if signal_date in all_dates:
                raise ValueError("Validation signal dates overlap across folds")
            all_dates.add(signal_date)
            total_diagnostic_dates += 1

            sample_count = row["sample_count"]
            if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
                raise ValueError(f"{fold_id} sample_count must be a positive integer")
            cross_section_sizes.append(sample_count)
            ic_value = row["spearman_rank_ic"]
            if sample_count < 2 and ic_value is not None:
                raise ValueError(f"{fold_id} Rank IC cannot be defined with fewer than two stocks")
            if ic_value is None:
                missing_ic_dates += 1
            else:
                ic = _finite_number(ic_value, f"{fold_id}.spearman_rank_ic")
                if not -1.0 <= ic <= 1.0:
                    raise ValueError(f"{fold_id} Spearman rank IC must be in [-1, 1]")
                fold_ic.append(ic)
                all_weekly_ic.append(ic)

            top_excess_value = row["top_n_excess_forward_return"]
            if top_excess_value is not None:
                excess = _finite_number(
                    top_excess_value, f"{fold_id}.top_n_excess_forward_return"
                )
                fold_top_excess.append(excess)
                all_weekly_top_excess.append(excess)

            top_bottom_value = row["top_minus_bottom_forward_return"]
            if top_bottom_value is not None:
                spread = _finite_number(
                    top_bottom_value, f"{fold_id}.top_minus_bottom_forward_return"
                )
                fold_top_bottom.append(spread)
                all_weekly_top_bottom.append(spread)

        fold_ic_mean = mean(fold_ic) if fold_ic else None
        fold_top_excess_mean = mean(fold_top_excess) if fold_top_excess else None
        fold_top_bottom_mean = mean(fold_top_bottom) if fold_top_bottom else None
        if fold_ic_mean is not None:
            fold_ic_means.append(fold_ic_mean)
        if fold_top_excess_mean is not None:
            fold_top_excess_means.append(fold_top_excess_mean)
        if fold_top_bottom_mean is not None:
            fold_top_bottom_means.append(fold_top_bottom_mean)
        fold_details.append(
            {
                "fold_id": fold_id,
                "status": status,
                "validation_period": {"start": start, "end": end},
                "labels_mature_before": cutoff,
                "prediction_rows": prediction_count,
                "matched_labeled_rows": matched_count,
                "label_coverage_rate": coverage,
                "signal_date_count": diagnostics.height,
                "rank_ic_observation_count": len(fold_ic),
                "mean_spearman_rank_ic": fold_ic_mean,
                "positive_rank_ic_ratio": (
                    sum(value > 0 for value in fold_ic) / len(fold_ic)
                    if fold_ic else None
                ),
                "mean_top_n_excess_forward_return": fold_top_excess_mean,
                "mean_top_minus_bottom_forward_return": fold_top_bottom_mean,
            }
        )

    total_signal_dates = len(all_dates)
    mean_ic = mean(all_weekly_ic) if all_weekly_ic else None
    std_ic = stdev(all_weekly_ic) if len(all_weekly_ic) > 1 else None
    icir = (
        mean_ic / std_ic
        if mean_ic is not None and std_ic is not None and std_ic > 0
        else None
    )
    positive_ic_ratio = (
        sum(value > 0 for value in all_weekly_ic) / len(all_weekly_ic)
        if all_weekly_ic else None
    )
    positive_rank_ic_fold_ratio = (
        sum(value > 0 for value in fold_ic_means) / len(fold_ic_means)
        if fold_ic_means else None
    )
    positive_top_excess_fold_ratio = (
        sum(value > 0 for value in fold_top_excess_means) / len(fold_top_excess_means)
        if fold_top_excess_means else None
    )
    positive_top_bottom_fold_ratio = (
        sum(value > 0 for value in fold_top_bottom_means) / len(fold_top_bottom_means)
        if fold_top_bottom_means else None
    )
    pooled_coverage = total_matched / total_predictions if total_predictions else 0.0

    result: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "decision": "NOT_ASSESSED",
        "policy_id": None,
        "policy_fingerprint": None,
        "fold_count": len(fold_reports),
        "evaluated_fold_count": evaluated_folds,
        "signal_date_count": total_signal_dates,
        "rank_ic_observation_count": len(all_weekly_ic),
        "minimum_weekly_cross_section_size": min(cross_section_sizes) if cross_section_sizes else None,
        "mean_weekly_cross_section_size": mean(cross_section_sizes) if cross_section_sizes else None,
        "missing_rank_ic_date_count": missing_ic_dates,
        "mean_spearman_rank_ic": mean_ic,
        "median_spearman_rank_ic": median(all_weekly_ic) if all_weekly_ic else None,
        "rank_ic_sample_std": std_ic,
        "rank_icir": icir,
        "positive_spearman_rank_ic_ratio": positive_ic_ratio,
        "defined_rank_ic_fold_count": len(fold_ic_means),
        "mean_fold_mean_spearman_rank_ic": (
            mean(fold_ic_means) if fold_ic_means else None
        ),
        "positive_rank_ic_fold_ratio": positive_rank_ic_fold_ratio,
        "mean_top_n_excess_forward_return": (
            mean(all_weekly_top_excess) if all_weekly_top_excess else None
        ),
        "mean_top_minus_bottom_forward_return": (
            mean(all_weekly_top_bottom) if all_weekly_top_bottom else None
        ),
        "positive_top_n_excess_fold_ratio": positive_top_excess_fold_ratio,
        "positive_top_minus_bottom_fold_ratio": positive_top_bottom_fold_ratio,
        "prediction_rows": total_predictions,
        "matched_labeled_rows": total_matched,
        "label_coverage_rate": pooled_coverage,
        "folds": fold_details,
        "acceptance_policy": None,
        "criteria": {},
        "failed_criteria": [],
        "interpretation": (
            "Cross-fold validation stability is descriptive unless an explicit "
            "versioned policy is supplied. Weekly IC observations are not assumed "
            "independent; ICIR is descriptive and not a significance test. No "
            "test-period metrics are read."
        ),
    }
    if policy is None:
        return result
    if not isinstance(policy, ValidationRankingAcceptancePolicy):
        raise TypeError("policy must be a ValidationRankingAcceptancePolicy")

    result["policy_id"] = policy.policy_id
    result["policy_fingerprint"] = policy.fingerprint
    result["acceptance_policy"] = policy.to_dict()

    criteria: dict[str, dict[str, Any]] = {}

    def assess(name: str, observed: Any, minimum: float | int, *, available: bool = True) -> None:
        passed = available and observed is not None and observed >= minimum
        criteria[name] = {
            "observed": observed,
            "minimum": minimum,
            "passed": bool(passed),
            "status": "PASS" if passed else "FAIL",
        }

    assess(
        "minimum_fold_count",
        evaluated_folds,
        policy.minimum_fold_count,
    )
    assess(
        "minimum_signal_date_count",
        total_signal_dates,
        policy.minimum_signal_date_count,
    )
    assess(
        "minimum_rank_ic_observation_count",
        len(all_weekly_ic),
        policy.minimum_rank_ic_observation_count,
    )
    assess(
        "minimum_cross_section_size",
        min(cross_section_sizes) if cross_section_sizes else None,
        policy.minimum_cross_section_size,
    )
    assess(
        "minimum_label_coverage_rate",
        pooled_coverage,
        policy.minimum_label_coverage_rate,
        available=total_predictions > 0,
    )
    assess(
        "minimum_mean_spearman_rank_ic",
        mean_ic,
        policy.minimum_mean_spearman_rank_ic,
    )
    assess(
        "minimum_positive_spearman_rank_ic_ratio",
        positive_ic_ratio,
        policy.minimum_positive_spearman_rank_ic_ratio,
    )
    assess(
        "minimum_positive_top_n_excess_fold_ratio",
        positive_top_excess_fold_ratio,
        policy.minimum_positive_top_n_excess_fold_ratio,
    )
    if policy.minimum_positive_rank_ic_fold_ratio is not None:
        assess(
            "minimum_positive_rank_ic_fold_ratio",
            positive_rank_ic_fold_ratio,
            policy.minimum_positive_rank_ic_fold_ratio,
        )
    if policy.minimum_rank_icir is not None:
        assess("minimum_rank_icir", icir, policy.minimum_rank_icir)
    if policy.minimum_mean_top_n_excess_forward_return is not None:
        assess(
            "minimum_mean_top_n_excess_forward_return",
            result["mean_top_n_excess_forward_return"],
            policy.minimum_mean_top_n_excess_forward_return,
        )

    failures = [name for name, value in criteria.items() if not value["passed"]]
    result["criteria"] = criteria
    result["failed_criteria"] = failures
    if not failures:
        result["decision"] = "ACCEPTED"
    elif set(failures).intersection(
        {
            "minimum_fold_count",
            "minimum_signal_date_count",
            "minimum_rank_ic_observation_count",
            "minimum_cross_section_size",
            "minimum_label_coverage_rate",
        }
    ):
        result["decision"] = "INSUFFICIENT_EVIDENCE"
    else:
        result["decision"] = "REJECTED"
    result["interpretation"] = (
        "The explicit validation-only policy was applied to the supplied folds. "
        "ACCEPTED means only that the predeclared validation thresholds passed; "
        "it does not establish out-of-sample profitability. The untouched test "
        "windows remain the final evaluation and were not read by this function."
    )
    return result
