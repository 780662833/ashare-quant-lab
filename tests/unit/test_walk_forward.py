from datetime import date, timedelta

import pytest

from ashare_quant.research.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
)


def make_sessions(count=100):
    sessions = []
    current = date(2024, 1, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    return sessions


def test_walk_forward_purges_label_horizon_and_embargo_between_partitions():
    sessions = make_sessions(80)
    folds = build_walk_forward_folds(
        sessions,
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=3,
            label_horizon_sessions=2,
            embargo_sessions=1,
        ),
    )
    first = folds[0]
    assert first.train_start == sessions[0]
    assert first.train_end == sessions[11]
    assert first.train_label_maturity == sessions[14]
    assert first.validation_start == sessions[16]
    assert first.validation_end == sessions[18]
    assert first.validation_label_maturity == sessions[21]
    assert first.test_start == sessions[23]
    assert first.test_end == sessions[25]
    assert first.label_entry_lag_sessions == 1


def test_walk_forward_test_windows_do_not_overlap():
    folds = build_walk_forward_folds(
        make_sessions(100),
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=5,
            label_horizon_sessions=1,
            embargo_sessions=1,
        ),
    )
    for previous, current in zip(folds, folds[1:]):
        assert current.test_start > previous.test_end


def test_expanding_training_window_grows_between_folds():
    folds = build_walk_forward_folds(
        make_sessions(110),
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=3,
            label_horizon_sessions=1,
            embargo_sessions=0,
            expanding_train=True,
        ),
    )
    assert len(folds) >= 2
    assert folds[1].train_sessions > folds[0].train_sessions
    assert folds[1].train_start == folds[0].train_start


def test_rolling_training_window_keeps_fixed_length():
    folds = build_walk_forward_folds(
        make_sessions(110),
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=3,
            label_horizon_sessions=1,
            embargo_sessions=0,
            expanding_train=False,
        ),
    )
    assert len(folds) >= 2
    assert [fold.train_sessions for fold in folds] == [12] * len(folds)
    assert folds[1].train_start > folds[0].train_start


def test_default_step_advances_by_test_window_and_is_non_overlapping():
    folds = build_walk_forward_folds(
        make_sessions(100),
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=4,
            label_horizon_sessions=0,
            embargo_sessions=0,
        ),
    )
    for previous, current in zip(folds, folds[1:]):
        assert current.test_start == (
            make_sessions(100)[
                make_sessions(100).index(previous.test_start) + 4
            ]
        )


def test_walk_forward_rejects_insufficient_sessions():
    with pytest.raises(ValueError, match="Insufficient"):
        build_walk_forward_folds(
            make_sessions(20),
            WalkForwardConfig(
                min_train_sessions=12,
                validation_sessions=3,
                test_sessions=3,
                label_horizon_sessions=2,
                embargo_sessions=1,
            ),
        )


def test_walk_forward_rejects_duplicate_and_unsorted_calendars():
    sessions = make_sessions(80)
    with pytest.raises(ValueError, match="unique"):
        build_walk_forward_folds(sessions + [sessions[0]])
    with pytest.raises(ValueError, match="chronological"):
        build_walk_forward_folds([sessions[1], sessions[0], *sessions[2:]])


def test_walk_forward_rejects_step_that_would_overlap_test_periods():
    with pytest.raises(ValueError, match="avoid test overlap"):
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=5,
            step_sessions=4,
        )


def test_walk_forward_config_rejects_negative_horizon_or_embargo():
    with pytest.raises(ValueError, match="non-negative"):
        WalkForwardConfig(label_horizon_sessions=-1)
    with pytest.raises(ValueError, match="non-negative"):
        WalkForwardConfig(embargo_sessions=-1)
    with pytest.raises(ValueError, match="non-negative"):
        WalkForwardConfig(label_entry_lag_sessions=-1)


def test_folds_are_serializable_and_do_not_unlock_training():
    folds = build_walk_forward_folds(
        make_sessions(80),
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=3,
        ),
    )
    assert folds[0].to_dict()["fold_id"] == "fold_001"
    # Fold planning is not a dataset builder or a training admission token.
    assert not hasattr(folds[0], "training_dataset_unlocked")



def test_default_weekly_label_maturity_includes_next_open_entry_lag():
    sessions = make_sessions(100)
    folds = build_walk_forward_folds(
        sessions,
        WalkForwardConfig(
            min_train_sessions=12,
            validation_sessions=3,
            test_sessions=3,
            # Defaults mirror weekly open-to-open labels and one-session embargo.
        ),
    )
    first = folds[0]
    assert first.label_horizon_sessions == 5
    assert first.label_entry_lag_sessions == 1
    assert first.train_label_maturity == sessions[17]
    # Maturity is +6 sessions after train_end; the next partition starts
    # after one additional embargo session.
    assert sessions.index(first.validation_start) == sessions.index(first.train_end) + 8
