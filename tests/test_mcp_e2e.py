"""Phase 3 verification: registry → MCP tools/list, name sanitization, bearer gate."""
import logging
import os
import textwrap

import pytest
from fastapi.testclient import TestClient

from mcp.shared.memory import create_connected_server_and_client_session

from soap_mcp.config import load_config
from soap_mcp.registry import ServiceRegistry
from soap_mcp.server import build_mcp_server

logging.disable(logging.CRITICAL)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
INSURANCE = os.path.join(FIXTURES, "insurance.wsdl")
CALCULATOR = os.path.join(FIXTURES, "calculator.wsdl")


def make_registry(tmp_path, body: str) -> ServiceRegistry:
    p = tmp_path / "services.yaml"
    p.write_text(textwrap.dedent(body))
    return ServiceRegistry.build(load_config(str(p)))


@pytest.fixture
def registry(tmp_path):
    return make_registry(
        tmp_path,
        f"""
        settings:
          hot_reload: false
        services:
          - name: insurance
            wsdl: {INSURANCE}
          - name: calc
            wsdl: {CALCULATOR}
        """,
    )


def test_registry_builds_tools(registry):
    names = {t.name for t in registry.tools()}
    assert "insurance_checkFraudRisk" in names
    assert "insurance_echo" in names
    # calculator has 2 ports; only the first port's ops become tools (deduped)
    assert "calc_Add" in names
    assert "calc_Subtract" in names
    # no duplicate SOAP 1.2 tools
    assert not any(n.endswith("_2") for n in names)


def test_tool_has_valid_input_schema(registry):
    entry = registry.lookup("insurance_checkFraudRisk")
    assert entry is not None
    assert entry.operation_name == "checkFraudRisk"
    assert entry.port_name  # port recorded for later invocation
    assert entry.tool.inputSchema["properties"]["recentClaims"]["type"] == "array"


def test_health_reports_loaded(registry):
    assert registry.health() == {"insurance": "loaded", "calc": "loaded"}


def test_bad_wsdl_service_skipped_others_survive(tmp_path):
    registry = make_registry(
        tmp_path,
        f"""
        services:
          - name: broken
            wsdl: {os.path.join(FIXTURES, "does_not_exist.wsdl")}
          - name: calc
            wsdl: {CALCULATOR}
        """,
    )
    health = registry.health()
    assert health["calc"] == "loaded"
    assert health["broken"].startswith("failed:")
    # calc tools still present despite broken sibling
    assert any(t.name.startswith("calc_") for t in registry.tools())


async def _list_tool_names(server):
    async with create_connected_server_and_client_session(server) as client:
        result = await client.list_tools()
        return {t.name for t in result.tools}


def test_mcp_client_lists_tools(registry):
    import anyio

    from soap_mcp.config import Settings

    server = build_mcp_server(registry, Settings())
    names = anyio.run(_list_tool_names, server)
    assert "insurance_checkFraudRisk" in names
    assert "calc_Add" in names


# --- bearer gate (HTTP layer) ----------------------------------------------


def _app_with_token(tmp_path, monkeypatch, token: str | None):
    p = tmp_path / "services.yaml"
    p.write_text(
        textwrap.dedent(
            f"""
            settings:
              hot_reload: false
            services:
              - name: calc
                wsdl: {CALCULATOR}
            """
        )
    )
    if token is None:
        monkeypatch.delenv("PROXY_BEARER_TOKEN", raising=False)
    else:
        monkeypatch.setenv("PROXY_BEARER_TOKEN", token)
    from soap_mcp.main import create_app

    return create_app(str(p))


def test_missing_bearer_is_401(tmp_path, monkeypatch):
    app = _app_with_token(tmp_path, monkeypatch, "secret-token")
    with TestClient(app) as client:
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_wrong_bearer_is_401(tmp_path, monkeypatch):
    app = _app_with_token(tmp_path, monkeypatch, "secret-token")
    with TestClient(app) as client:
        r = client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r.status_code == 401


def test_correct_bearer_passes_gate(tmp_path, monkeypatch):
    app = _app_with_token(tmp_path, monkeypatch, "secret-token")
    with TestClient(app) as client:
        r = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer secret-token",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        # Past the gate: not a 401 (may be a protocol error for missing session,
        # but auth succeeded).
        assert r.status_code != 401


def test_health_unauthenticated(tmp_path, monkeypatch):
    app = _app_with_token(tmp_path, monkeypatch, "secret-token")
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["services"] == {"calc": "loaded"}
