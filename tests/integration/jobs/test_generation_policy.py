from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.image_provider_routing import ImageProviderSelection
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.local_inference import LocalProviderHealth
from aipic_to_model.infrastructure.sqlite.connection import connect


class _SequenceProbe:
    def __init__(self, *availability: bool) -> None:
        self._availability = list(availability)

    def probe(self, profile):
        available = self._availability.pop(0) if len(self._availability) > 1 else self._availability[0]
        return LocalProviderHealth(
            profile_id=profile.profile_id,
            engine=profile.engine,
            model_id=profile.model_id,
            configured=available,
            available=available,
            reason=None if available else "runtime_not_configured",
            capabilities=profile.capabilities,
        )


def _setup(tmp_path: Path):
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Generation policy")
    prompt_path = tmp_path / "prompt.json"
    prompt_path.write_text(
        json.dumps(
            {
                "analysis_zh": "测试",
                "analysis_en": "test",
                "prompt_zh": "陶瓷机器人",
                "prompt_en": "a ceramic robot",
            }
        ),
        encoding="utf-8",
    )
    prompt = dependencies.assets.import_file(
        root,
        project.id,
        prompt_path,
        "prompt",
        "generation-policy-prompt",
    )
    image_path = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "teal").save(image_path)
    image = dependencies.assets.import_file(
        root,
        project.id,
        image_path,
        "source_image",
        "generation-policy-source",
    )
    return dependencies, root, project.id, str(prompt["id"]), str(image["id"])


def _tool_call(database: Path, call_id: str) -> dict[str, object]:
    connection = connect(database)
    try:
        row = connection.execute(
            """SELECT arguments_json,provider_profile,risk_level,status
            FROM tool_calls WHERE id=?""",
            (call_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return {
        "arguments": json.loads(str(row["arguments_json"])),
        "provider_profile": row["provider_profile"],
        "risk_level": row["risk_level"],
        "status": row["status"],
    }


def test_local_image_route_is_frozen_before_persistence_without_approval(tmp_path: Path) -> None:
    dependencies, root, project_id, prompt_id, _image_id = _setup(tmp_path)
    dependencies.settings.update_app(
        dependencies.app_db,
        {"image_generation_backend": "local"},
    )
    local_monitor = cast(Any, dependencies.local_provider_monitor)
    local_monitor._probes["image/local/z-image-turbo"] = _SequenceProbe(True)

    result = dependencies.registry.execute(
        root,
        project_id,
        "image.generate",
        "1.0.0",
        {
            "prompt_asset_id": prompt_id,
            "provider_profile": "image-generation/auto",
            "channel": "auto",
            "model": "auto",
            "candidate_count": 1,
        },
        "local-image-request",
    )

    assert result.status == "queued" and result.job is not None
    assert result.job["provider"] == "image/local/z-image-turbo"
    stored = _tool_call(root / "project.sqlite3", result.tool_call_id)
    assert stored["provider_profile"] == "image/local/z-image-turbo"
    assert stored["risk_level"] == "local_reversible"
    assert stored["arguments"] == {
        "prompt_asset_id": prompt_id,
        "provider_profile": "image/local/z-image-turbo",
        "channel": "z_image",
        "model": "Z-Image-Turbo",
        "candidate_count": 1,
    }
    assert dependencies.jobs.get(
        root / "project.sqlite3", job_id=result.job["job_id"]
    ).resume_class.value == "local_restartable"


def test_remote_image_route_is_concrete_and_parameter_bound_before_approval(
    tmp_path: Path,
) -> None:
    dependencies, root, project_id, prompt_id, _image_id = _setup(tmp_path)
    dependencies.settings.update_app(
        dependencies.app_db,
        {"image_generation_backend": "remote"},
    )
    remote_monitor = cast(Any, dependencies.image_provider_monitor)
    remote_monitor.resolve_route = lambda _mode: ImageProviderSelection(
        "meshy/default", "meshy", "nano-banana"
    )

    result = dependencies.registry.execute(
        root,
        project_id,
        "image.generate",
        "1.0.0",
        {
            "prompt_asset_id": prompt_id,
            "provider_profile": "image-generation/auto",
            "channel": "auto",
            "model": "auto",
            "candidate_count": 2,
        },
        "remote-image-request",
    )

    assert result.status == "awaiting_ui_action" and result.ui_action is not None
    stored = _tool_call(root / "project.sqlite3", result.tool_call_id)
    assert stored["provider_profile"] == "meshy/default"
    assert stored["risk_level"] == "external_paid"
    assert stored["arguments"] == {
        "prompt_asset_id": prompt_id,
        "provider_profile": "meshy/default",
        "channel": "meshy",
        "model": "nano-banana",
        "candidate_count": 2,
    }
    approval = dependencies.b02_runtime._approvals.get(
        root / "project.sqlite3", approval_id=result.ui_action["action_id"]
    )
    assert approval.provider_profile == "meshy/default"


def test_local_single_image_3d_is_immediate_but_multiview_remains_paid(
    tmp_path: Path,
) -> None:
    dependencies, root, project_id, _prompt_id, image_id = _setup(tmp_path)
    dependencies.settings.update_app(
        dependencies.app_db,
        {"model3d_generation_backend": "local"},
    )
    local_monitor = cast(Any, dependencies.local_provider_monitor)
    local_monitor._probes["model3d/local/triposr"] = _SequenceProbe(True)

    local = dependencies.registry.execute(
        root,
        project_id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "image",
            "image_asset_id": image_id,
            "provider_profile": "tripo3d/default",
            "model": "v3.1-20260211",
            "parameters": {"pbr": True},
        },
        "local-3d-request",
    )
    assert local.status == "queued" and local.job is not None
    assert local.job["provider"] == "model3d/local/triposr"
    stored = _tool_call(root / "project.sqlite3", local.tool_call_id)
    assert stored["risk_level"] == "local_reversible"
    assert cast(dict[str, object], stored["arguments"])["model"] == "stabilityai/TripoSR"
    assert cast(dict[str, object], stored["arguments"])["parameters"] == {
        "pbr": False,
        "texture": True,
    }

    paid = dependencies.registry.execute(
        root,
        project_id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "multiview",
            "multiview_set_id": "set-1",
            "view_asset_ids": {"front": "front", "side": "side", "back": "back"},
            "provider_profile": "model3d/local/triposr",
            "model": "stabilityai/TripoSR",
            "parameters": {},
        },
        "multiview-3d-request",
    )
    assert paid.status == "awaiting_ui_action"
    paid_stored = _tool_call(root / "project.sqlite3", paid.tool_call_id)
    assert paid_stored["provider_profile"] == "tripo3d/default"
    assert paid_stored["risk_level"] == "external_paid"


def test_auto_route_replay_keeps_the_first_frozen_provider(tmp_path: Path) -> None:
    dependencies, root, project_id, prompt_id, _image_id = _setup(tmp_path)
    dependencies.settings.update_app(
        dependencies.app_db,
        {"image_generation_backend": "auto"},
    )
    local_monitor = cast(Any, dependencies.local_provider_monitor)
    local_monitor._probes["image/local/z-image-turbo"] = _SequenceProbe(
        True, False
    )
    remote_monitor = cast(Any, dependencies.image_provider_monitor)
    remote_monitor.resolve_route = lambda _mode: ImageProviderSelection(
        "meshy/default", "meshy", "nano-banana"
    )
    arguments = {
        "prompt_asset_id": prompt_id,
        "provider_profile": "image-generation/auto",
        "channel": "auto",
        "model": "auto",
        "candidate_count": 1,
    }

    first = dependencies.registry.execute(
        root,
        project_id,
        "image.generate",
        "1.0.0",
        arguments,
        "stable-auto-request",
    )
    replayed = dependencies.registry.execute(
        root,
        project_id,
        "image.generate",
        "1.0.0",
        arguments,
        "stable-auto-request",
    )

    assert first.job is not None and replayed.job is not None
    assert replayed.tool_call_id == first.tool_call_id
    assert replayed.job["job_id"] == first.job["job_id"]
    connection = connect(root / "project.sqlite3")
    try:
        calls = connection.execute("SELECT COUNT(*) AS count FROM tool_calls").fetchone()
    finally:
        connection.close()
    assert calls is not None and calls["count"] == 1
