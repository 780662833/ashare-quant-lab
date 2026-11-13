import polars as pl

from ashare_quant.data.acceptance import _gate, _pit_coverage, render_markdown


def test_gate_fails_closed():
    gate = _gate("pit", False, {"reason": "missing evidence"})
    assert gate["status"] == "FAIL"


def test_markdown_report_shows_fail_and_never_unlocks_training():
    report = {
        "overall_status": "FAIL",
        "data_root": r"F:\\cnEquity",
        "window": {"start": "2018-01-01", "end": "2025-12-31"},
        "profile": "cn_a_sh_sz_research_v1",
        "scope": {"symbols": 100},
        "gates": [
            {"name": "historical_universe", "status": "PASS"},
            {"name": "hfq_adjustment", "status": "FAIL"},
        ],
        "training_dataset_unlocked": False,
    }
    markdown = render_markdown(report)
    assert "**FAIL**" in markdown
    assert "| hfq_adjustment | **FAIL** |" in markdown
    assert "训练数据集开放：否" in markdown


def test_pit_coverage_uses_cnequity_item_code_and_checks_every_pair():
    frame = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH", "000001.SZ"],
        "item_code": ["roe", "net_profit", "roe"],
        "pit_is_exact": [True, True, True],
        "pit_quality": ["strict", "strict", "strict"],
    })
    quality_ok, missing = _pit_coverage(
        frame, ["600000.SH", "000001.SZ"], ["roe", "net_profit"]
    )
    assert quality_ok is True
    assert missing == [{"symbol": "000001.SZ", "item": "net_profit"}]


def test_pit_coverage_rejects_noncanonical_item_alias():
    frame = pl.DataFrame({
        "symbol": ["600000.SH"],
        "item": ["roe"],
        "pit_is_exact": [True],
        "pit_quality": ["strict"],
    })
    quality_ok, missing = _pit_coverage(frame, ["600000.SH"], ["roe"])
    assert quality_ok is False
    assert missing == []


def test_pit_coverage_rejects_reconstructed_or_unknown_rows():
    frame = pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "item_code": ["roe", "roe"],
        "pit_is_exact": [True, False],
        "pit_quality": ["strict", "reconstructed"],
    })
    quality_ok, missing = _pit_coverage(frame, ["600000.SH", "000001.SZ"], ["roe"])
    assert quality_ok is False
    assert missing == []
