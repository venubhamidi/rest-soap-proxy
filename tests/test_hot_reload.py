"""Phase 5 verification: registry reload diffs services and reuses clients."""
import logging
import os
import textwrap

from soap_mcp.config import load_config
from soap_mcp.registry import ServiceRegistry

logging.disable(logging.CRITICAL)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
INSURANCE = os.path.join(FIXTURES, "insurance.wsdl")
CALCULATOR = os.path.join(FIXTURES, "calculator.wsdl")


def write(tmp_path, body: str) -> str:
    p = tmp_path / "services.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_adding_a_service_updates_tools_and_reuses_client(tmp_path):
    path = write(
        tmp_path,
        f"""
        services:
          - name: insurance
            wsdl: {INSURANCE}
        """,
    )
    registry = ServiceRegistry.build(load_config(path))
    assert set(registry.health()) == {"insurance"}
    insurance_client_before = registry.service("insurance").client

    # Add calc service.
    write(
        tmp_path,
        f"""
        services:
          - name: insurance
            wsdl: {INSURANCE}
          - name: calc
            wsdl: {CALCULATOR}
        """,
    )
    changed = registry.reload(load_config(path))
    assert changed is True
    assert set(registry.health()) == {"insurance", "calc"}
    assert any(t.name.startswith("calc_") for t in registry.tools())
    # unchanged service reused its client (no WSDL re-parse)
    assert registry.service("insurance").client is insurance_client_before


def test_removing_a_service_drops_its_tools(tmp_path):
    path = write(
        tmp_path,
        f"""
        services:
          - name: insurance
            wsdl: {INSURANCE}
          - name: calc
            wsdl: {CALCULATOR}
        """,
    )
    registry = ServiceRegistry.build(load_config(path))
    assert any(t.name.startswith("calc_") for t in registry.tools())

    write(
        tmp_path,
        f"""
        services:
          - name: insurance
            wsdl: {INSURANCE}
        """,
    )
    changed = registry.reload(load_config(path))
    assert changed is True
    assert set(registry.health()) == {"insurance"}
    assert not any(t.name.startswith("calc_") for t in registry.tools())


def test_app_lifespan_starts_and_stops_cleanly(tmp_path, monkeypatch):
    """With hot_reload on, entering/exiting the app lifespan must not hang."""
    from fastapi.testclient import TestClient

    from soap_mcp.main import create_app

    monkeypatch.setenv("PROXY_BEARER_TOKEN", "tok")
    path = write(
        tmp_path,
        f"""
        settings:
          hot_reload: true
        services:
          - name: insurance
            wsdl: {INSURANCE}
        """,
    )
    app = create_app(path)
    with TestClient(app) as client:  # enters lifespan (session mgr + reload loop)
        assert client.get("/health").json()["services"] == {"insurance": "loaded"}
    # exiting the context cancels the reload loop and shuts down cleanly


def test_no_change_returns_false(tmp_path):
    path = write(
        tmp_path,
        f"""
        services:
          - name: insurance
            wsdl: {INSURANCE}
        """,
    )
    registry = ServiceRegistry.build(load_config(path))
    changed = registry.reload(load_config(path))
    assert changed is False
