"""Phase 4 verification: tools/call end-to-end with a mocked Zeep transport."""
import logging
import os
import textwrap

import anyio
import pytest
from requests import Response
from requests.auth import HTTPBasicAuth
from requests.exceptions import Timeout

from soap_mcp.config import load_config
from soap_mcp.invoker import invoke, normalize_parameters
from soap_mcp.registry import ServiceRegistry

logging.disable(logging.CRITICAL)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
INSURANCE = os.path.join(FIXTURES, "insurance.wsdl")

FRAUD_NS = "http://insurance.com/fraud"

SUCCESS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ns0:checkFraudRiskResponse xmlns:ns0="{FRAUD_NS}">
      <ns0:riskScore>0.87</ns0:riskScore>
      <ns0:decision>REVIEW</ns0:decision>
      <ns0:flagged>true</ns0:flagged>
    </ns0:checkFraudRiskResponse>
  </soap:Body>
</soap:Envelope>"""

FAULT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <soap:Fault>
      <faultcode>soap:Server</faultcode>
      <faultstring>Claim not found in system of record</faultstring>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>"""


def _response(body: str, status: int = 200) -> Response:
    r = Response()
    r.status_code = status
    r._content = body.encode("utf-8")
    r.headers["Content-Type"] = "text/xml; charset=utf-8"
    return r


def build_registry(tmp_path, auth_block: str = "") -> ServiceRegistry:
    body = f"""
    settings:
      hot_reload: false
    services:
      - name: insurance
        wsdl: {INSURANCE}
    """
    if auth_block:
        body += textwrap.indent(textwrap.dedent(auth_block), " " * 8)
    p = tmp_path / "services.yaml"
    p.write_text(textwrap.dedent(body))
    return ServiceRegistry.build(load_config(str(p)))


def patch_transport(registry, response=None, exc=None):
    """Patch the service client's transport.post; capture outgoing calls."""
    captured = {}
    client = registry.service("insurance").client

    def fake_post(address, message, headers):
        captured["address"] = address
        captured["message"] = (
            message.decode("utf-8") if isinstance(message, (bytes, bytearray)) else str(message)
        )
        captured["headers"] = dict(headers or {})
        if exc is not None:
            raise exc
        return response

    client.transport.post = fake_post
    return captured


def run(registry, tool, args):
    return anyio.run(invoke, registry, tool, args)


# --- success round-trip -----------------------------------------------------


def test_success_round_trip(tmp_path):
    registry = build_registry(tmp_path)
    patch_transport(registry, response=_response(SUCCESS_XML))
    result = run(
        registry,
        "insurance_checkFraudRisk",
        {"customerId": "C-1", "claimType": "THEFT"},
    )
    assert result.isError is False
    assert result.structuredContent["decision"] == "REVIEW"
    assert result.structuredContent["riskScore"] == 0.87
    assert result.structuredContent["flagged"] is True


def test_unknown_tool_is_error(tmp_path):
    registry = build_registry(tmp_path)
    result = run(registry, "insurance_nope", {})
    assert result.isError is True
    assert "Unknown tool" in result.content[0].text


# --- error contract ---------------------------------------------------------


def test_soap_fault_is_error(tmp_path):
    registry = build_registry(tmp_path)
    patch_transport(registry, response=_response(FAULT_XML, status=500))
    result = run(
        registry,
        "insurance_checkFraudRisk",
        {"customerId": "C-1", "claimType": "THEFT"},
    )
    assert result.isError is True
    assert "Fault" in result.content[0].text
    assert "Claim not found" in result.content[0].text


def test_timeout_is_error(tmp_path):
    registry = build_registry(tmp_path)
    patch_transport(registry, exc=Timeout("read timed out"))
    result = run(
        registry,
        "insurance_checkFraudRisk",
        {"customerId": "C-1", "claimType": "THEFT"},
    )
    assert result.isError is True
    assert "timed out" in result.content[0].text.lower()
    # no internals leaked
    assert "Traceback" not in result.content[0].text


# --- list rewrapping --------------------------------------------------------


def test_list_rewrapping_produces_wrapper_elements(tmp_path):
    registry = build_registry(tmp_path)
    captured = patch_transport(registry, response=_response(SUCCESS_XML))
    result = run(
        registry,
        "insurance_checkFraudRisk",
        {
            "customerId": "C-1",
            "claimType": "THEFT",
            "recentClaims": [
                {"id": "a", "claimId": "CLM-001"},
                {"id": "b", "claimId": "CLM-002"},
            ],
        },
    )
    assert result.isError is False
    msg = captured["message"]
    # rewrapped: wrapper <recentClaims> holding repeated <recentClaim> children
    assert "recentClaim" in msg
    assert "CLM-001" in msg and "CLM-002" in msg


# --- auth wiring ------------------------------------------------------------


def test_wsse_username_adds_security_header(tmp_path, monkeypatch):
    monkeypatch.setenv("PW", "s3cret")
    registry = build_registry(
        tmp_path,
        """
        auth:
          type: wsse_username
          username: svc
          password: ${PW}
          add_timestamp: true
        """,
    )
    captured = patch_transport(registry, response=_response(SUCCESS_XML))
    run(registry, "insurance_checkFraudRisk", {"customerId": "C-1", "claimType": "THEFT"})
    msg = captured["message"]
    assert "Security" in msg
    assert "UsernameToken" in msg
    assert "svc" in msg
    assert "Timestamp" in msg


def test_basic_auth_configured_on_session(tmp_path):
    registry = build_registry(
        tmp_path,
        """
        auth:
          type: basic
          username: u
          password: p
        """,
    )
    session = registry.service("insurance").client.transport.session
    assert isinstance(session.auth, HTTPBasicAuth)
    assert session.auth.username == "u"
    assert session.auth.password == "p"


def test_client_cert_configured_on_session(tmp_path):
    registry = build_registry(
        tmp_path,
        """
        auth:
          type: client_cert
          cert_path: /etc/certs/client.pem
          key_path: /etc/certs/client.key
          ca_bundle_path: /etc/certs/ca.pem
        """,
    )
    session = registry.service("insurance").client.transport.session
    assert session.cert == ("/etc/certs/client.pem", "/etc/certs/client.key")
    assert session.verify == "/etc/certs/ca.pem"


def test_custom_headers_configured_on_session(tmp_path):
    registry = build_registry(
        tmp_path,
        """
        headers:
          X-Route: internal
        """,
    )
    session = registry.service("insurance").client.transport.session
    assert session.headers.get("X-Route") == "internal"


# --- normalize (scalar auto-wrap) -------------------------------------------


def test_normalize_wraps_scalar_for_single_param():
    schema = {"type": "object", "properties": {"message": {"type": "string"}}}
    assert normalize_parameters("hi", schema) == {"message": "hi"}


def test_normalize_passes_dict_through():
    schema = {"type": "object", "properties": {"a": {}, "b": {}}}
    assert normalize_parameters({"a": 1}, schema) == {"a": 1}


def test_normalize_rejects_scalar_for_multi_param():
    schema = {"type": "object", "properties": {"a": {}, "b": {}}}
    with pytest.raises(ValueError):
        normalize_parameters("x", schema)
