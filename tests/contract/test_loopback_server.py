import pytest

from aipic_to_model.api.server import LoopbackServerConfig, bind_loopback_socket


def test_b01_11_sidecar_listener_is_loopback_with_os_selected_port_only():
    assert LoopbackServerConfig().host == "127.0.0.1"
    assert LoopbackServerConfig().port == 0
    with pytest.raises(ValueError):
        LoopbackServerConfig(host="0.0.0.0")
    with pytest.raises(ValueError):
        LoopbackServerConfig(port=8765)


def test_b01_11_actual_sidecar_socket_is_os_selected_ipv4_loopback():
    listener = bind_loopback_socket()
    try:
        host, port = listener.getsockname()
        assert host == "127.0.0.1" and port > 0
    finally:
        listener.close()
