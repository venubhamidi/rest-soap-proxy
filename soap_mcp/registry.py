"""
ServiceRegistry: builds MCP tool definitions from the config at startup.

For each configured service it loads the WSDL with Zeep, selects a service/port,
converts each operation's input XSD to a JSON Schema, and produces one MCP
``Tool`` per operation. It keeps an explicit map ``tool_name → ToolEntry`` so the
invoker never has to re-derive the operation by splitting the tool name.

A service whose WSDL fails to load is skipped with a clear error and marked
``failed`` in the health report; the registry still serves the rest.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import mcp.types as mcp_types
from zeep import Client

from .config import AppConfig, ServiceConfig, Settings
from .converter import xsd_to_json_schema
from .security import build_client  # Phase 4 supplies security; see security.py

logger = logging.getLogger(__name__)

_ILLEGAL_TOOL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


@dataclass
class ToolEntry:
    """Everything needed to list and later invoke one operation as an MCP tool."""

    tool_name: str
    service_name: str
    operation_name: str
    service_wsdl_name: str
    port_name: str
    input_schema: dict
    description: str
    tool: mcp_types.Tool


@dataclass
class ServiceEntry:
    name: str
    status: str  # "loaded" or "failed: <reason>"
    config: Optional[ServiceConfig] = None
    client: Optional[Client] = None
    service_wsdl_name: Optional[str] = None
    port_name: Optional[str] = None
    tools: list[ToolEntry] = field(default_factory=list)


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, ServiceEntry] = {}
        self._tools: dict[str, ToolEntry] = {}
        self._settings: Settings = Settings()

    # --- build ---------------------------------------------------------------

    @classmethod
    def build(cls, config: AppConfig) -> "ServiceRegistry":
        registry = cls()
        registry._settings = config.settings
        for svc_cfg in config.services:
            entry = registry._make_entry(svc_cfg, config.settings)
            registry._install(entry)
        logger.info(
            "Registry built: %d services, %d tools",
            len(registry._services),
            len(registry._tools),
        )
        return registry

    def reload(self, config: AppConfig) -> bool:
        """Rebuild in place from new config, reusing unchanged services.

        A service is rebuilt only if its config changed (or global settings
        changed); otherwise its existing Zeep client is reused, avoiding a WSDL
        re-parse. Returns True if the set of tool names changed.
        """
        settings_changed = self._settings != config.settings
        old_services = self._services
        old_tool_names = set(self._tools)

        new_services: dict[str, ServiceEntry] = {}
        new_tools: dict[str, ToolEntry] = {}
        for svc_cfg in config.services:
            old = old_services.get(svc_cfg.name)
            if (
                not settings_changed
                and old is not None
                and old.status == "loaded"
                and old.config == svc_cfg
            ):
                entry = old  # reuse client + tools
            else:
                entry = self._make_entry(svc_cfg, config.settings)
            new_services[svc_cfg.name] = entry
            for te in entry.tools:
                new_tools[te.tool_name] = te

        self._services = new_services
        self._tools = new_tools
        self._settings = config.settings
        return set(new_tools) != old_tool_names

    def _install(self, entry: ServiceEntry) -> None:
        self._services[entry.name] = entry
        for te in entry.tools:
            self._tools[te.tool_name] = te

    def _make_entry(self, svc_cfg: ServiceConfig, settings: Settings) -> ServiceEntry:
        try:
            client = build_client(svc_cfg, settings)
        except Exception as e:  # noqa: BLE001 - partial availability by design
            reason = f"{type(e).__name__}: {e}"
            logger.error("Service '%s' failed to load WSDL: %s", svc_cfg.name, reason)
            return ServiceEntry(name=svc_cfg.name, status=f"failed: {reason}", config=svc_cfg)

        try:
            wsdl_service_name, port_name, binding_ops = self._select_port(client, svc_cfg)
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {e}"
            logger.error("Service '%s' failed to select port: %s", svc_cfg.name, reason)
            return ServiceEntry(name=svc_cfg.name, status=f"failed: {reason}", config=svc_cfg)

        entry = ServiceEntry(
            name=svc_cfg.name,
            status="loaded",
            config=svc_cfg,
            client=client,
            service_wsdl_name=wsdl_service_name,
            port_name=port_name,
        )

        used_names: set[str] = set()
        for op_name, binding_op in binding_ops.items():
            tool_entry = self._build_tool(
                svc_cfg.name, wsdl_service_name, port_name, op_name, binding_op, used_names
            )
            entry.tools.append(tool_entry)
            used_names.add(tool_entry.tool_name)

        logger.info(
            "Service '%s' loaded: %d tools (service=%s port=%s)",
            svc_cfg.name,
            len(entry.tools),
            wsdl_service_name,
            port_name,
        )
        return entry

    def _select_port(self, client: Client, svc_cfg: ServiceConfig):
        """Pick (service_name, port_name, {op_name: binding_op}) from the WSDL.

        ``wsdl_service``/``wsdl_port`` override the defaults (first of each).
        Operations are taken from a single port to avoid duplicate tools for
        multi-binding WSDLs (e.g. SOAP 1.1 + 1.2).
        """
        services = client.wsdl.services
        if svc_cfg.wsdl_service:
            if svc_cfg.wsdl_service not in services:
                raise ValueError(
                    f"wsdl_service '{svc_cfg.wsdl_service}' not in WSDL "
                    f"(have: {list(services)})"
                )
            wsdl_service_name = svc_cfg.wsdl_service
        else:
            wsdl_service_name = next(iter(services))
        service = services[wsdl_service_name]

        if svc_cfg.wsdl_port:
            if svc_cfg.wsdl_port not in service.ports:
                raise ValueError(
                    f"wsdl_port '{svc_cfg.wsdl_port}' not in service "
                    f"'{wsdl_service_name}' (have: {list(service.ports)})"
                )
            port_name = svc_cfg.wsdl_port
        else:
            port_name = next(iter(service.ports))
        port = service.ports[port_name]

        return wsdl_service_name, port_name, port.binding.all()

    def _build_tool(
        self,
        service_name: str,
        wsdl_service_name: str,
        port_name: str,
        op_name: str,
        binding_op,
        used_names: set[str],
    ) -> ToolEntry:
        body = binding_op.input.body
        input_schema = xsd_to_json_schema(body.type) if body else {"type": "object"}

        documentation = getattr(binding_op.abstract, "documentation", None)
        description = documentation or _generate_tool_description(
            service_name, op_name, input_schema
        )

        tool_name = self._sanitized_tool_name(service_name, op_name, used_names)

        tool = mcp_types.Tool(
            name=tool_name,
            description=description,
            inputSchema=input_schema,
        )
        return ToolEntry(
            tool_name=tool_name,
            service_name=service_name,
            operation_name=op_name,
            service_wsdl_name=wsdl_service_name,
            port_name=port_name,
            input_schema=input_schema,
            description=description,
            tool=tool,
        )

    def _sanitized_tool_name(
        self, service_name: str, op_name: str, used_names: set[str]
    ) -> str:
        base = f"{service_name}_{_ILLEGAL_TOOL_CHARS.sub('_', op_name)}"
        if base not in used_names:
            return base
        # Sanitization collision within a service → append _2, _3, ...
        suffix = 2
        while f"{base}_{suffix}" in used_names:
            suffix += 1
        collided = f"{base}_{suffix}"
        logger.warning(
            "Tool name collision for '%s' in service '%s' → '%s'",
            op_name,
            service_name,
            collided,
        )
        return collided

    # --- queries -------------------------------------------------------------

    def tools(self) -> list[mcp_types.Tool]:
        return [entry.tool for entry in self._tools.values()]

    def lookup(self, tool_name: str) -> Optional[ToolEntry]:
        return self._tools.get(tool_name)

    def service(self, name: str) -> Optional[ServiceEntry]:
        return self._services.get(name)

    def health(self) -> dict[str, str]:
        return {name: entry.status for name, entry in self._services.items()}


def _generate_tool_description(
    service_name: str, operation_name: str, input_schema: dict
) -> str:
    """Ported from gateway_client._generate_tool_description."""
    properties = (input_schema or {}).get("properties", {})
    base_desc = f"{service_name} - {operation_name}"

    if not properties:
        return f"{base_desc}. No parameters required."

    if len(properties) == 1:
        param_name = next(iter(properties))
        param_type = properties[param_name].get("type", "value")
        return (
            f"{base_desc}. Parameter: {param_name} ({param_type}). "
            f'You can pass just the value directly, or use {{"{param_name}": value}}.'
        )

    required = set(input_schema.get("required", []))
    param_list = []
    for name, schema in properties.items():
        param_type = schema.get("type", "value")
        marker = " (required)" if name in required else " (optional)"
        param_list.append(f"{name}: {param_type}{marker}")
    return f"{base_desc}. Parameters: {', '.join(param_list)}"
