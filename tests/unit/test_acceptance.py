from ashare_quant.data.acceptance import _gate, render_markdown


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
