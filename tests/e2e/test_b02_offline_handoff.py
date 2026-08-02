from __future__ import annotations

from pathlib import Path

from scripts.generate_b02_evidence import generate


def test_b02_executable_offline_pipeline_generates_redacted_evidence(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    manifest = generate(output)
    assert manifest["validation"]["executable_offline_pipeline"] == "passed"
    assert manifest["validation"]["provider_create_calls"] == 1
    assert manifest["validation"]["manual_quality_confirmation"] == "passed"
    assert manifest["validation"]["restart_recovery"] == "passed"
    assert manifest["validation"]["restart_provider_create_calls"] == 0
    assert manifest["validation"]["job_count"] == 2
    assert manifest["contains_secrets"] is False
    assert {
        "manifest.json",
        "job-timeline.json",
        "outbox.json",
        "provenance.json",
        "inspection.json",
        "manual-quality.json",
        "restart-recovery.json",
        "conversion.json",
        "smoke-summary.json",
    } == {path.name for path in output.iterdir()}
