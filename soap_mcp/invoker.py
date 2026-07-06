"""
tools/call handler: turn an MCP tool invocation into a SOAP call.

Pipeline: normalize parameters → rewrap JSON arrays into WSDL wrapper structures
→ invoke Zeep (off the event loop) → serialize the result → MCP tool result.

Error contract (rewrite plan §9):
- SOAP Fault      → isError, text = fault code + string (never a protocol error).
- Transport error → isError, message names the failure class, no internals.
- Unknown tool    → isError, "unknown tool".
- Anything else   → isError with the message (Zeep validation names the element).

Ports ``_rewrap_list_parameters`` (fixed to resolve the binding via the
operation's stored port) and ``_normalize_parameters`` from ``soap_translator``.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import anyio
import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import SSLError, Timeout
from zeep.exceptions import Fault, TransportError

from .config import Settings
from .registry import ServiceRegistry, ToolEntry
from .serialization import serialize

logger = logging.getLogger(__name__)


def wire_invoker(server: Server, registry: ServiceRegistry, settings: Settings) -> None:
    # validate_input=False: defer validation to Zeep so its element-naming
    # messages reach the client (plan §9), rather than a generic jsonschema error.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> mcp_types.CallToolResult:
        return await invoke(registry, name, arguments or {})


async def invoke(
    registry: ServiceRegistry, tool_name: str, arguments: dict
) -> mcp_types.CallToolResult:
    entry = registry.lookup(tool_name)
    if entry is None:
        return _error(f"Unknown tool: {tool_name}")

    service_entry = registry.service(entry.service_name)
    if service_entry is None or service_entry.client is None:
        return _error(f"Service '{entry.service_name}' is not available")
    client = service_entry.client

    params = normalize_parameters(arguments, entry.input_schema)
    if isinstance(params, dict):
        params = rewrap_list_parameters(params, client, entry)

    proxy = client.bind(entry.service_wsdl_name, entry.port_name)
    operation = getattr(proxy, entry.operation_name, None)
    if operation is None:
        return _error(
            f"Operation '{entry.operation_name}' not found on port "
            f"'{entry.port_name}'"
        )

    started = time.perf_counter()
    outcome = "ok"
    try:
        result = await anyio.to_thread.run_sync(lambda: operation(**params))
    except Fault as f:
        outcome = "soap_fault"
        return _error(_format_fault(f))
    except Timeout:
        outcome = "timeout"
        return _error("upstream request timed out")
    except SSLError:
        outcome = "tls_error"
        return _error("TLS error contacting upstream service")
    except ReqConnectionError:
        outcome = "connection_error"
        return _error("could not connect to upstream service")
    except TransportError as e:
        outcome = "transport_error"
        status = getattr(e, "status_code", None)
        return _error(
            f"upstream transport error (HTTP {status})" if status
            else "upstream transport error"
        )
    except Exception as e:  # noqa: BLE001 - validation & unexpected caller errors
        outcome = "error"
        # Zeep validation errors name the offending element; safe to surface.
        return _error(f"{type(e).__name__}: {e}")
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "call service=%s operation=%s duration_ms=%.1f outcome=%s",
            entry.service_name,
            entry.operation_name,
            duration_ms,
            outcome,
        )

    serialized = serialize(result)
    text = json.dumps(serialized, indent=2, ensure_ascii=False, default=str)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent=serialized if isinstance(serialized, dict) else None,
        isError=False,
    )


def normalize_parameters(arguments: Any, input_schema: dict) -> Any:
    """Auto-wrap a bare scalar for single-parameter operations.

    MCP clients send an object; ContextForge tool invocations may pass a bare
    value. A dict passes through unchanged.
    """
    if isinstance(arguments, dict):
        return arguments

    properties = (input_schema or {}).get("properties", {})
    if len(properties) == 1:
        param_name = next(iter(properties))
        logger.debug("Auto-wrapping scalar into parameter '%s'", param_name)
        return {param_name: arguments}
    if len(properties) > 1:
        raise ValueError(
            f"operation requires multiple parameters {list(properties)}, "
            "but received a single value; provide an object"
        )
    return {}


def rewrap_list_parameters(
    parameters: dict, client, entry: ToolEntry
) -> dict:
    """Rewrap JSON arrays into WSDL wrapper structures.

    JSON Schema exposes ``{"recentClaims": [...]}`` but Zeep expects
    ``{"recentClaims": {"recentClaim": [...]}}``. The binding is resolved via the
    operation's stored service/port (fix vs. the old ``bindings.keys()[0]``).
    """
    try:
        service = client.wsdl.services[entry.service_wsdl_name]
        port = service.ports[entry.port_name]
        binding_op = port.binding.all().get(entry.operation_name)
        if not binding_op or not binding_op.input.body:
            return parameters

        input_type = binding_op.input.body.type
        if not hasattr(input_type, "elements"):
            return parameters

        transformed = dict(parameters)
        for element_name, element in input_type.elements:
            if element_name not in transformed:
                continue
            value = transformed[element_name]
            if not isinstance(value, list):
                continue
            if not hasattr(element, "type") or not hasattr(element.type, "elements"):
                continue

            inner_elements = list(element.type.elements)
            if len(inner_elements) == 1:
                inner_name, inner_element = inner_elements[0]
                max_occurs = getattr(inner_element, "max_occurs", 1)
                if max_occurs is None or max_occurs == "unbounded" or (
                    isinstance(max_occurs, int) and max_occurs > 1
                ):
                    logger.debug(
                        "Rewrapping list '%s' into '%s'", element_name, inner_name
                    )
                    transformed[element_name] = {inner_name: value}

        return transformed
    except Exception as e:  # noqa: BLE001 - never fail the call over rewrapping
        logger.warning("Could not rewrap parameters (using as-is): %s", e)
        return parameters


def _format_fault(fault: Fault) -> str:
    code = getattr(fault, "code", None)
    message = getattr(fault, "message", None) or str(fault)
    if code:
        return f"SOAP Fault [{code}]: {message}"
    return f"SOAP Fault: {message}"


def _error(text: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        isError=True,
    )
