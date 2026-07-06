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
