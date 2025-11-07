import importlib
import sys

import pytest

from src.config import Config
from src.mcp.errors import MCPTransportError


@pytest.fixture
def server_module(monkeypatch):
    """Load src.server with autostart disabled for isolated testing."""
    monkeypatch.setenv("CHL_SKIP_MCP_AUTOSTART", "1")
    monkeypatch.delenv("CHL_MCP_HTTP_MODE", raising=False)
    if "src.server" in sys.modules:
        del sys.modules["src.server"]
    server = importlib.import_module("src.server")
    server.api_client = None
    server.HTTP_MODE = "http"
    server._categories_cache["payload"] = None
    server._categories_cache["expires"] = 0.0
    return server


def test_config_http_mode_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("CHL_MCP_HTTP_MODE", raising=False)
    monkeypatch.setenv("CHL_USE_API", "1")
    monkeypatch.setenv("CHL_EXPERIENCE_ROOT", str(tmp_path))
    cfg = Config()
    assert cfg.mcp_http_mode == "http"
    assert cfg.use_api is True


def test_config_http_mode_respects_legacy_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("CHL_MCP_HTTP_MODE", raising=False)
    monkeypatch.setenv("CHL_USE_API", "0")
    monkeypatch.setenv("CHL_EXPERIENCE_ROOT", str(tmp_path))
    cfg = Config()
    assert cfg.mcp_http_mode == "direct"
    assert cfg.use_api is False


def test_config_http_mode_auto(monkeypatch, tmp_path):
    monkeypatch.setenv("CHL_MCP_HTTP_MODE", "auto")
    monkeypatch.setenv("CHL_EXPERIENCE_ROOT", str(tmp_path))
    cfg = Config()
    assert cfg.mcp_http_mode == "auto"
    assert cfg.use_api is True


def test_request_with_fallback_returns_direct_on_transport(server_module, monkeypatch):
    class FailingClient:
        def request(self, *args, **kwargs):
            raise MCPTransportError("boom")

    server = server_module
    server.api_client = FailingClient()
    server.HTTP_MODE = "auto"

    captured = {}

    def fake_direct(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"via": "direct"}

    monkeypatch.setattr(server, "_call_direct_handler", fake_direct)

    result, source = server._request_with_fallback(
        "GET",
        "/api/v1/categories/",
        fallback_name="list_categories",
        fallback_kwargs={"foo": "bar"},
    )

    assert source == "direct"
    assert result == {"via": "direct"}
    assert captured["name"] == "list_categories"
    assert captured["kwargs"] == {"foo": "bar"}


def test_request_with_fallback_raises_in_http_mode(server_module):
    class FailingClient:
        def request(self, *args, **kwargs):
            raise MCPTransportError("boom")

    server = server_module
    server.api_client = FailingClient()
    server.HTTP_MODE = "http"

    with pytest.raises(MCPTransportError):
        server._request_with_fallback(
            "GET",
            "/api/v1/categories/",
            fallback_name="list_categories",
        )


def test_list_categories_uses_cache(server_module):
    class CountingClient:
        def __init__(self):
            self.calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            return {"categories": [{"code": "PGS", "name": "Playbooks"}]}

    client = CountingClient()
    server = server_module
    server.api_client = client
    server.HTTP_MODE = "http"

    first = server.list_categories()
    second = server.list_categories()

    assert client.calls == 1
    assert first == second == {"categories": [{"code": "PGS", "name": "Playbooks"}]}
