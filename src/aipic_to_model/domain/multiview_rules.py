"""Deterministic B02 multiview region and quality gates."""

from __future__ import annotations

from typing import Any, Literal, cast

from .production_models import Issue, MultiviewValidation, SelectionRect

VIEWS = ("front", "side", "back")


def default_regions(width: int, height: int) -> dict[str, SelectionRect]:
    return {
        "front": SelectionRect(
            x=round(width * 0.05),
            y=round(height * 0.05),
            width=round(width * 0.30),
            height=round(height * 0.90),
        ),
        "side": SelectionRect(
            x=round(width * 0.36),
            y=round(height * 0.05),
            width=round(width * 0.30),
            height=round(height * 0.90),
        ),
        "back": SelectionRect(
            x=round(width * 0.67),
            y=round(height * 0.05),
            width=round(width * 0.30),
            height=round(height * 0.90),
        ),
    }


def validate_regions(
    regions: dict[str, SelectionRect], width: int, height: int
) -> MultiviewValidation:
    issues: list[Issue] = []
    for view in VIEWS:
        rect = regions.get(view)
        if rect is None:
            issues.append(_issue("MV_REGION_MISSING", view, "blocking", "缺少必需视图框。"))
            continue
        if rect.x + rect.width > width or rect.y + rect.height > height:
            issues.append(
                _issue("MV_REGION_OUT_OF_BOUNDS", view, "blocking", "视图框超出原图范围。")
            )
        if rect.width * rect.height < width * height * 0.02:
            issues.append(
                _issue("MV_REGION_TOO_SMALL", view, "blocking", "视图框小于原图面积的 2%。")
            )
    severity = "blocking" if any(issue.check_status == "blocking" for issue in issues) else "info"
    return MultiviewValidation(
        severity=severity, checks_run=[], issues=issues, can_continue=not issues
    )


def validate_quality(checks: dict[str, str]) -> MultiviewValidation:
    """Require individually evidenced results for all six B02 quality checks."""
    mapping = {
        "subject_scale": "MV_SUBJECT_SCALE",
        "direction": "MV_DIRECTION",
        "key_accessory": "MV_KEY_ACCESSORY",
        "truncation": "MV_TRUNCATION",
        "background": "MV_BACKGROUND",
        "resolution": "MV_RESOLUTION",
    }
    issues = [
        _issue(mapping[name], "set", status, f"{name} 检查结果：{status}。")
        for name, status in checks.items()
        if status != "passed"
    ]
    missing = [name for name in mapping if name not in checks]
    issues.extend(_issue(mapping[name], "set", "not_run", f"{name} 尚未运行。") for name in missing)
    severe = any(issue.check_status in {"blocking", "not_run"} for issue in issues)
    return MultiviewValidation(
        severity="blocking" if severe else "warning" if issues else "info",
        checks_run=cast(
            list[
                Literal[
                    "subject_scale",
                    "direction",
                    "key_accessory",
                    "truncation",
                    "background",
                    "resolution",
                ]
            ],
            [name for name in mapping if name in checks],
        ),
        issues=issues,
        can_continue=not severe,
    )


def _issue(code: str, view: str, status: str, explanation: str) -> Issue:
    return Issue(
        issue_id=f"{code}:{view}",
        code=cast(Any, code),
        view=cast(Any, view),
        check_status=cast(Any, status),
        explanation=explanation,
        evidence_summary=explanation,
        recommended_action="调整视图框或重新生成对应视图。",
    )
