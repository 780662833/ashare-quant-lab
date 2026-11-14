from __future__ import annotations

import polars as pl
import pytest

from ashare_quant.research.admission import TrainingDatasetAdmission, _frame_fingerprint
from ashare_quant.research.qlib_dataset import build_qlib_panel_rows


def frames(*, entry_dates=("2024-01-08", "2024-01-08")):
    features = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05"],
        "instrument": ["600000.SH", "000001.SZ"],
        "return_5": [0.01, 0.02],
        "pit_roe": [0.1, 0.2],
    })
    labels = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05"],
        "instrument": ["600000.SH", "000001.SZ"],
        "label_entry_date": list(entry_dates),
        "label_end_date": ["2024-01-12", "2024-01-12"],
        "forward_return": [0.03, -0.01],
    })
    return features, labels


def admission_for(features, labels):
    return TrainingDatasetAdmission(
        admitted=True,
        report_id="qlib-entry-contract",
        start_date="2024-01-01",
        end_date="2024-01-31",
        universe_profile="synthetic",
        training_cutoff="2024-01-15",
        feature_rows=features.height,
        label_rows=labels.height,
        feature_columns=("return_5", "pit_roe"),
        label_horizon_sessions=5,
        source_fingerprint="a" * 64,
        feature_fingerprint=_frame_fingerprint(
            features, keys=["signal_date", "instrument"]
        ),
        label_fingerprint=_frame_fingerprint(
            labels, keys=["signal_date", "instrument"]
        ),
        rejection_reasons=(),
    )


def test_qlib_panel_rows_requires_and_retains_execution_aligned_label_contract():
    features, labels = frames()
    admission = admission_for(features, labels)
    index, columns, targets = build_qlib_panel_rows(
        features=features, labels=labels, admission=admission
    )
    assert index == [
        ("2024-01-05", "000001.SZ"),
        ("2024-01-05", "600000.SH"),
    ]
    assert columns == ["return_5", "pit_roe"]
    assert targets == [-0.01, 0.03]


def test_qlib_panel_rows_rejects_legacy_labels_without_entry_date():
    features, labels = frames()
    legacy = labels.drop("label_entry_date")
    admission = admission_for(features, legacy)
    with pytest.raises(ValueError, match="labels do not match the admitted label schema"):
        build_qlib_panel_rows(
            features=features, labels=legacy, admission=admission
        )


@pytest.mark.parametrize(
    "entry_dates",
    [
        ("2024-01-05", "2024-01-08"),
        ("2024-01-13", "2024-01-08"),
    ],
)
def test_qlib_panel_rows_rejects_entry_dates_outside_signal_to_maturity_window(entry_dates):
    features, labels = frames(entry_dates=entry_dates)
    admission = admission_for(features, labels)
    with pytest.raises(ValueError, match="invalid execution entry date"):
        build_qlib_panel_rows(
            features=features, labels=labels, admission=admission
        )


def test_qlib_panel_rows_rejects_noncanonical_entry_date():
    features, labels = frames(entry_dates=("2024-1-8", "2024-01-08"))
    admission = admission_for(features, labels)
    with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
        build_qlib_panel_rows(
            features=features, labels=labels, admission=admission
        )
