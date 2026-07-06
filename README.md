# SOAP → MCP proxy

A native **MCP server** that turns SOAP services into MCP tools. Point it at one
or more WSDLs and every SOAP operation becomes an MCP tool, invokable by any MCP
client — in particular the [IBM ContextForge MCP Gateway](https://github.com/IBM/mcp-context-forge),
which federates this server and auto-discovers its tools via `tools/list`.

No database, no admin UI, no per-tool registration. The only state is a YAML
service registry; all schemas and tool definitions are rebuilt in memory at
startup and on hot-reload.

## How it works

```
MCP client (e.g. ContextForge) ──(streamable HTTP + Bearer)──► /mcp
                                                                 │
                                        ┌────────────────────────┴───────────┐
                                        │ FastAPI app                         │
                                        │  ├─ MCP server (mcp SDK)            │
                                        │  ├─ GET /health (unauthenticated)   │
                                        │  └─ ServiceRegistry (in-memory)     │
                                        └───────────┬─────────────────────────┘
                            services.yaml ──(mtime poll)──┤
                                                          ▼
                                          Zeep client per service
                                     (WS-Security / Basic / mTLS)
                                                          │
                                                          ▼
                                                Upstream SOAP services
```

Startup (and each hot-reload): parse each WSDL with Zeep → convert the input XSD
of every operation to a JSON Schema → register one MCP tool per operation. A
`tools/call` looks the tool up in the registry, invokes Zeep on a worker thread,
and serializes the result to JSON.

## Quick start

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional: require a bearer token from clients (recommended).
export PROXY_BEARER_TOKEN=secret-token

# The default services.yaml exposes the public dneonline calculator.
uvicorn soap_mcp.main:create_app --factory --host 0.0.0.0 --port 8080
```

Check health (unauthenticated):

```bash
curl -s localhost:8080/health | jq
# {"status":"ok","services":{"calculator":"loaded"}}
```

The MCP streamable-HTTP endpoint is at `POST /mcp` (requires the bearer token if
`PROXY_BEARER_TOKEN` is set).

## Configuration (`services.yaml`)

```yaml
settings:
  wsdl_request_timeout: 30   # seconds, Zeep transport / WSDL fetch
  operation_timeout: 60      # seconds, per SOAP call
  hot_reload: true           # watch this file and rebuild on change

services:
  - name: weather            # unique; [a-z0-9_-]+
    wsdl: ./wsdls/weather.wsdl   # local path OR https URL
    # wsdl_service: WeatherService   # pick when the WSDL has several
    # wsdl_port: WeatherPort
    auth:
      type: wsse_username    # none | wsse_username | basic | client_cert
      username: svc-weather
      password: ${WEATHER_SOAP_PASSWORD}
      use_digest: false
      add_timestamp: true
      timestamp_ttl: 300
    headers:                 # optional custom HTTP headers
      X-Route: internal
```

- `${VAR}` anywhere in a string is expanded from the environment at load time. A
  referenced-but-unset variable is a **startup error** naming the variable.
- Service names must be unique.
- A service whose WSDL fails to parse is **skipped** with a clear error log; the
  server still starts with the remaining services. `/health` reports per-service
  status (`loaded` / `failed: <reason>`).
- Edit `services.yaml` while the server runs (with `hot_reload: true`): the tool
  list updates on the next `tools/list` without a restart.

### Upstream auth modes

| `auth.type` | Fields | Effect |
|---|---|---|
| `none` (default) | — | No upstream auth |
| `wsse_username` | `username`, `password`, `use_digest`, `add_timestamp`, `timestamp_ttl` | WS-Security UsernameToken header (fresh WSU:Timestamp per request) |
| `basic` | `username`, `password` | HTTP Basic on the transport session |
| `client_cert` | `cert_path`, `key_path`, `ca_bundle_path` | mTLS client certificate |

## Adding a WSDL (walkthrough)

There is **no admin UI and no per-WSDL URL** — you register a service by adding
it to `services.yaml`, and every service is served from the one `/mcp` endpoint.

The server reads its config from `$SERVICES_CONFIG` if set, otherwise
`services.yaml` in the working directory (in Docker, `/app/services.yaml` — mount
your own over it).

**1. Add an entry** under `services:` (same indentation as the others):

```yaml
services:
  - name: calculator
    wsdl: http://www.dneonline.com/calculator.asmx?WSDL

  - name: weather                       # <-- new service; unique, [a-z0-9_-]+
    wsdl: https://example.com/weather?wsdl
    auth:
      type: basic
      username: ${WEATHER_USER}         # secrets via env, never inline
      password: ${WEATHER_PW}
```

Export any `${VAR}` you referenced (`export WEATHER_USER=… WEATHER_PW=…`) — a
missing one fails the (re)load naming that variable.

**2. Save.** With `hot_reload: true` the running server rebuilds within ~2s (no
restart). Without it, restart the process. In Docker, a bind-mounted file that
your editor replaces by inode may not trigger the poll — restart the container.

**3. Confirm it loaded** (unauthenticated):

```bash
curl -s localhost:8080/health | jq
# {"status":"ok","services":{"calculator":"loaded","weather":"loaded"}}
```

A bad WSDL shows `"weather":"failed: <reason>"` here; the other services keep
working. Each operation becomes a tool named `weather_<Operation>`; nothing new
to register — clients see it on their next `tools/list`.

## Calling a tool from an MCP client

Every tool lives at the single endpoint `POST /mcp` (streamable HTTP). A client
does the normal MCP handshake, then `tools/list` / `tools/call`. Example with the
official Python SDK:

```python
import anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

async def main():
    headers = {"Authorization": "Bearer secret-token"}  # your PROXY_BEARER_TOKEN
    async with streamablehttp_client("http://localhost:8080/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print([t.name for t in tools.tools])
            # ['calculator_Add', 'calculator_Subtract', ..., 'weather_GetForecast']

            result = await session.call_tool("calculator_Add", {"intA": 2, "intB": 3})
            print(result.content[0].text)   # -> 5

anyio.run(main)
```

- Arguments are a JSON object matching the tool's `inputSchema` (from the WSDL).
  A single-parameter operation also accepts the bare value.
- The result text is the SOAP response serialized to JSON; complex responses also
  populate `structuredContent`.
- A SOAP fault or transport error comes back as a normal result with
  `isError: true` (see the error contract below), not a transport failure.

For a gateway rather than a direct client, see
[Federating with ContextForge](#federating-with-contextforge) below — it federates
this same `/mcp` URL once and re-discovers tools automatically.

## Inbound auth

- `POST /mcp` requires `Authorization: Bearer $PROXY_BEARER_TOKEN` (compared with
  `hmac.compare_digest`); otherwise 401.
- `/health` is unauthenticated (liveness). 200 with per-service status; 503 only
  if the config itself failed to load.
- If `PROXY_BEARER_TOKEN` is unset, the endpoint runs **open** (dev mode) and
  logs a loud warning.

## Tool naming

- Tool name = `{service}_{operation}` (illegal MCP chars replaced with `_`).
- Sanitization collisions within a service get `_2`, `_3`, … suffixes.
- For a multi-binding WSDL (e.g. SOAP 1.1 + 1.2), operations are taken from a
  single port to avoid duplicate tools. Use `wsdl_port` to choose it.

## Federating with ContextForge

1. Run this proxy somewhere ContextForge can reach it (e.g. `https://<proxy>`).
2. In the ContextForge Admin UI: **Gateways → Add**:
   - **URL**: `https://<proxy>/mcp`
   - **Transport**: streamable HTTP
   - **Auth**: Bearer, value = your `PROXY_BEARER_TOKEN`
3. ContextForge calls `tools/list` and auto-discovers every operation as a tool.
   Invoking a tool federates the call back through `/mcp` → SOAP.

## Error handling (`tools/call`)

- **SOAP Fault** → tool result with `isError: true`; text includes the fault code
  and string. Never a protocol-level error.
- **Transport error** (timeout, connection refused, TLS) → `isError: true`,
  message names the failure class (no stack traces to clients).
- **Unknown tool** → `isError: true`.
- Full detail is logged server-side (one structured line per call: service,
  operation, duration, outcome). Request parameters and response bodies are
  **never logged** — SOAP payloads may carry PII/credentials.

## Docker

```bash
docker build -t soap-mcp .
docker run -p 8080:8080 \
  -e PROXY_BEARER_TOKEN=secret-token \
  -v "$PWD/services.yaml:/app/services.yaml:ro" \
  soap-mcp
```

## Tests

```bash
pytest
```

## Development notes

- Python 3.11+, FastAPI, the official `mcp` SDK, and Zeep (pinned to 4.3.x).
- SOAP calls run on a worker thread (`anyio.to_thread`) so the MCP event loop is
  never blocked; one Zeep client per service, built at load time and reused.
- WSDL URLs come from the operator-controlled config, not from request input.
  The server exposes no endpoint that fetches a caller-supplied URL.
