from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode


def test_b01_11_capabilities_are_one_time_scoped_and_expire(tmp_path: Path):
    capabilities = HostCapabilityStore()
    issued = capabilities.issue(tmp_path / "file.png", "import", "project-a")
    assert (
        capabilities.resolve_once(issued, "import", "project-a")
        == (tmp_path / "file.png").resolve()
    )
    for operation, project in (("import", "project-a"), ("export", "project-a")):
        with pytest.raises(DomainErrorV1) as error:
            capabilities.resolve_once(issued, operation, project)
        assert error.value.code == ErrorCode.SECURITY_CAPABILITY_INVALID
    wrong_project = capabilities.issue(tmp_path / "file.png", "import", "project-a")
    with pytest.raises(DomainErrorV1):
        capabilities.resolve_once(wrong_project, "import", "project-b")
    expired = capabilities.issue(tmp_path / "file.png", "import", "project-a")
    capabilities._items[expired].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(DomainErrorV1):
        capabilities.resolve_once(expired, "import", "project-a")
