"""
FastAPI app factory for the SOAP → MCP proxy.

Loads config, builds the service registry, and mounts the MCP streamable-HTTP
app at ``/mcp`` behind a static bearer-token gate. ``/health`` is unauthenticated.
"""
from __future__ import annotations

import contextlib
import hmac
import logging
import os

import anyio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .config import ConfigError, load_config
from .registry import ServiceRegistry
from .server import build_mcp_server

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "services.yaml"
BEARER_ENV = "PROXY_BEARER_TOKEN"


def create_app(config_path: str | None = None) -> FastAPI:
    config_path = config_path or os.environ.get("SERVICES_CONFIG", DEFAULT_CONFIG_PATH)

    config = None
    config_error: str | None = None
    registry: ServiceRegistry | None = None
    try:
        config = load_config(config_path)
        registry = ServiceRegistry.build(config)
        logger.info("Loaded config from %s (%d services)", config_path, len(config.services))
    except ConfigError as e:
        config_error = str(e)
        logger.error("Failed to load config from %s: %s", config_path, e)

    bearer_token = os.environ.get(BEARER_ENV)
    if not bearer_token:
        logger.warning(
            "=" * 60 + "\n"
            "  %s is not set — the /mcp endpoint is running OPEN (dev mode).\n"
            "  Set %s to require a bearer token from clients.\n" + "=" * 60,
            BEARER_ENV,
            BEARER_ENV,
        )

    # Build the MCP server + streamable-HTTP session manager (only if config ok).
    mcp_server = None
    session_manager: StreamableHTTPSessionManager | None = None
    if registry is not None:
        mcp_server = build_mcp_server(registry, config.settings)
        session_manager = StreamableHTTPSessionManager(app=mcp_server)

    hot_reload = registry is not None and config.settings.hot_reload

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with contextlib.AsyncExitStack() as stack:
            if session_manager is not None:
                await stack.enter_async_context(session_manager.run())
            if hot_reload:
                tg = await stack.enter_async_context(anyio.create_task_group())
                # Cancel the infinite reload loop before the task group's
                # __aexit__ (which would otherwise wait for it forever).
                stack.callback(tg.cancel_scope.cancel)
                tg.start_soon(_reload_loop, config_path, registry)
            yield

    app = FastAPI(title="SOAP → MCP proxy", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.config_error = config_error
    app.state.registry = registry
    app.state.mcp_server = mcp_server
    app.state.bearer_token = bearer_token

    @app.get("/health")
    def health() -> JSONResponse:
        if registry is None:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "error": config_error},
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "services": registry.health()},
        )

    if session_manager is not None:
        mcp_asgi = _make_mcp_asgi(session_manager, bearer_token)
        app.mount("/mcp", mcp_asgi)

    return app


RELOAD_POLL_SECONDS = 2.0


async def _reload_loop(config_path: str, registry: ServiceRegistry) -> None:
    """Poll the config file's mtime; on change, reload the registry in place.

    Clients see the new tool set on their next tools/list. A config that fails
    to load is logged and the previous registry is kept (partial availability).
    """
    last_mtime = _safe_mtime(config_path)
    while True:
        await anyio.sleep(RELOAD_POLL_SECONDS)
        mtime = _safe_mtime(config_path)
        if mtime is None or mtime == last_mtime:
            continue
        last_mtime = mtime
        try:
            new_config = load_config(config_path)
        except ConfigError as e:
            logger.error("Hot reload skipped — config invalid: %s", e)
            continue
        changed = registry.reload(new_config)
        if changed:
            logger.info("Config changed — tool list updated (%d tools)", len(registry.tools()))
        else:
            logger.info("Config changed — no tool changes")


def _safe_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _make_mcp_asgi(session_manager: StreamableHTTPSessionManager, bearer_token: str | None):
    """ASGI app for /mcp that enforces the bearer gate then delegates to MCP."""

    async def mcp_asgi(scope, receive, send):
        if scope["type"] == "http" and bearer_token:
            if not _authorized(scope, bearer_token):
                await _send_401(send)
                return
        await session_manager.handle_request(scope, receive, send)

    return mcp_asgi


def _authorized(scope, bearer_token: str) -> bool:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            expected = f"Bearer {bearer_token}".encode()
            return hmac.compare_digest(value, expected)
    return False


async def _send_401(send) -> None:
    body = b'{"error":"unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
