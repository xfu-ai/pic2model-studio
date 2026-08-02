from aipic_to_model.domain.build_info import APP_NAME, APP_VERSION, about


def test_b01_01_build_info_has_no_legacy_expiry_or_contact_blocker():
    assert about() == {"name": APP_NAME, "version": APP_VERSION}
    assert APP_NAME == "FormWeaver Studio"
