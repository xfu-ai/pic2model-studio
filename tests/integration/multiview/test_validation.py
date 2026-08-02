from __future__ import annotations

from aipic_to_model.domain.multiview_rules import (
    default_regions,
    validate_quality,
    validate_regions,
)


def test_default_regions_are_valid_and_not_run_blocks_3d() -> None:
    regions = default_regions(1000, 1000)
    assert validate_regions(regions, 1000, 1000).can_continue
    report = validate_quality({"subject_scale": "passed", "direction": "passed"})
    assert not report.can_continue
    assert {issue.code for issue in report.issues} >= {
        "MV_KEY_ACCESSORY",
        "MV_TRUNCATION",
        "MV_BACKGROUND",
        "MV_RESOLUTION",
    }


def test_each_quality_issue_has_its_own_actionable_result() -> None:
    report = validate_quality(
        {
            "subject_scale": "warning",
            "direction": "blocking",
            "key_accessory": "passed",
            "truncation": "passed",
            "background": "passed",
            "resolution": "passed",
        }
    )
    assert not report.can_continue
    assert [(issue.code, issue.check_status) for issue in report.issues] == [
        ("MV_SUBJECT_SCALE", "warning"),
        ("MV_DIRECTION", "blocking"),
    ]
