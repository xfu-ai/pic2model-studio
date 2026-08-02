import pytest

from aipic_to_model.domain.common import DomainErrorV1, ErrorCode
from aipic_to_model.domain.event_payloads import validate_event_payload


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "asset.created",
            {
                "asset_id": "a",
                "asset_type": "source_image",
                "asset_group": "input_images",
                "parent_asset_id": None,
            },
        ),
        (
            "workspace.action.requested",
            {"action_id": "a", "type": "select_rectangle", "workspace_mode": "rectangle_selection"},
        ),
    ],
)
def test_b01_event_payloads_are_closed_discriminated_contracts(event_type, payload):
    validate_event_payload(event_type, payload)
    with pytest.raises(DomainErrorV1) as error:
        validate_event_payload(event_type, {**payload, "path": "C:\\leak"})
    assert error.value.code == ErrorCode.SCHEMA_VALIDATION_FAILED
