from __future__ import annotations

import pytest

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.common import DomainErrorV1


def test_image_provider_priority_and_probe_interval_are_strict(tmp_path) -> None:
    app_db = tmp_path / "app.sqlite3"
    dependencies = compose_local_app(HostCapabilityStore(), app_db)

    saved = dependencies.settings.update_app(
        app_db,
        {
            "image_provider_priority": ["meshy/default", "tripo3d/default"],
            "provider_probe_interval_seconds": 600,
        },
    )

    assert saved["image_provider_priority"] == ["meshy/default", "tripo3d/default"]
    assert saved["provider_probe_interval_seconds"] == 600
    with pytest.raises(DomainErrorV1):
        dependencies.settings.update_app(
            app_db,
            {"image_provider_priority": ["meshy/default", "meshy/default"]},
        )
    with pytest.raises(DomainErrorV1):
        dependencies.settings.update_app(
            app_db,
            {"provider_probe_interval_seconds": 10},
        )
