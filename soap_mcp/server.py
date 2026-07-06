"""
Low-level MCP server construction.

Builds an ``mcp.server.lowlevel.Server`` whose ``tools/list`` is served from the
``ServiceRegistry``. ``tools/call`` is wired in Phase 4 (the invoker).
"""
from __future__ import annotations

import logging

import mcp.types as mcp_types
from mcp.server.lowlevel import Server, NotificationOptions

from .config import Settings
from .registry import ServiceRegistry

logger = logging.getLogger(__name__)

SERVER_NAME = "soap-mcp-proxy"


def build_mcp_server(registry: ServiceRegistry, settings: Settings) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return registry.tools()

    # Phase 4 registers @server.call_tool() via wire_invoker().
    from .invoker import wire_invoker

    wire_invoker(server, registry, settings)

    return server


def initialization_options(server: Server):
    return server.create_initialization_options(
        notification_options=NotificationOptions(tools_changed=True)
    )
