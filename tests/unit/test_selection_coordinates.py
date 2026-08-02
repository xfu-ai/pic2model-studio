from aipic_to_model.domain.coordinates import image_to_view, normalize_rect, view_to_image


def test_b01_07_selection_coordinates_roundtrip():
    for scale in (0.25, 1, 4):
        for pan_x, pan_y in ((0, 0), (11, -8), (-320, 245)):
            point = image_to_view(40, 20, scale, pan_x, pan_y)
            assert view_to_image(*point, scale, pan_x, pan_y) == (40, 20)
    assert normalize_rect({"x": 0.1, "y": 0.2, "width": 3.2, "height": 4.3}, 10, 10)["width"] == 4
