# Rewrite Plan: SOAP → MCP Proxy (no-DB architecture)

**Status:** Approved design, ready for implementation.
**Branch:** `nodb` (this branch). The old implementation on `secured`/`main` stays as reference — port logic from it, do not import its modules.

## 1. Goal

Rewrite the SOAP-to-REST proxy as a **native MCP server**: each SOAP operation
defined in a registered WSDL becomes an MCP tool, invokable by any MCP client —
in particular the IBM ContextForge MCP Gateway (https://github.com/IBM/mcp-context-forge),
which will federate this server and auto-discover its tools.

Replaces the current architecture (Flask + Postgres + per-tool REST registration
into ContextForge via its `/tools` and `/servers` APIs).

## 2. Decisions already made (do not re-litigate)

| Decision | Choice | Rationale |
|---|---|---|
| MCP integration | Native MCP server, streamable HTTP transport | ContextForge federates external MCP servers under **Gateways** and auto-discovers tools via `tools/list`. One registration replaces all per-tool bookkeeping. |
| Stack | Python 3.11+, FastAPI, official `mcp` Python SDK, Zeep | Zeep is the strongest WSDL/XSD library; the hard-won XSD conversion logic in the old repo ports directly. |
| Persistence | **None.** YAML config file + WSDL files/URLs. All derived data (schemas, operations) rebuilt in memory at startup. | Only true state is the service registry — a handful of entries. Kills the DB, the admin UI, the login page, and the secrets-at-rest problem. |
| Upstream SOAP auth | Per-service, declared in config, secrets via `${ENV_VAR}` references | Supports: none, WS-Security UsernameToken (digest + timestamp), HTTP Basic, mTLS client cert, custom headers. |
| Inbound auth (gateway → proxy) | Single static bearer token from env (`PROXY_BEARER_TOKEN`) | ContextForge attaches auth headers when federating a gateway. If the env var is unset, log a loud warning and run open (dev mode). |
| REST facade | **Not in scope.** MCP only. | OpenAPI export can be added later if a non-MCP consumer appears. |
| Runtime registration UI | **Not in scope.** Adding a service = edit YAML (hot-reload) | Deliberate operational model; SQLite is the future escape hatch if self-service registration is ever needed. |

## 3. Architecture

```
ContextForge Gateway ──(streamable HTTP + Bearer token)──► /mcp endpoint
                                                              │
                                              ┌───────────────┴──────────────┐
                                              │  FastAPI app                 │
                                              │  ├─ MCP server (mcp SDK)     │
                                              │  ├─ GET /health              │
                                              │  └─ registry (in-memory)     │
                                              └───────┬──────────────────────┘
                        services.yaml ──(load/watch)──┤
                                                      ▼
                                          Zeep client per service
                                          (WS-Security / Basic / mTLS)
                                                      │
                                                      ▼
                                              Upstream SOAP services
```

Tool lifecycle: startup (or config hot-reload) → parse each WSDL with Zeep →
convert XSD input types to JSON Schema → register one MCP tool per operation →
emit `tools/list_changed` on any change. `tools/call` → registry lookup by tool
name → Zeep invocation → serialize result to JSON → MCP tool result.

## 4. Repository layout (target)

```
soap_mcp/
  __init__.py
  main.py            # FastAPI app factory, mounts MCP streamable-HTTP app, /health
  config.py          # load + validate services.yaml, ${ENV} expansion (pydantic models)
  registry.py        # ServiceRegistry: name → (zeep client, operations, tool defs); hot-reload
  converter.py       # XSD type → JSON Schema  (PORT from wsdl_converter.xsd_to_json_schema)
  invoker.py         # tools/call handler: rewrap lists, call Zeep, serialize result
  security.py        # build Transport+wsse from auth config (PORT from soap_translator)
  serialization.py   # Zeep result → JSON (PORT _serialize_zeep_result; add Decimal/date/bytes)
services.yaml        # example config (no real secrets)
tests/
  fixtures/*.wsdl    # reuse existing tests/ fixtures where present
  test_converter.py
  test_config.py
  test_invoker.py    # mocked transport
  test_mcp_e2e.py    # in-process MCP client ↔ server round trip
Dockerfile
requirements.txt
README.md            # rewrite; document ContextForge federation steps
```

Old top-level files (`app.py`, `database.py`, `gateway_client.py`,
`soap_translator.py`, `wsdl_converter.py`, `templates/`, `static/`,
`architecture.md`, `DEPLOYMENT.md`) are **deleted on this branch** once their
logic is ported (Phase 5). Until then leave them in place as the porting source.

## 5. Config format

```yaml
# services.yaml
settings:
  wsdl_request_timeout: 30        # seconds, Zeep transport
  operation_timeout: 60           # seconds, per SOAP call
  hot_reload: true                # watch this file for changes

services:
  - name: weather                 # unique; [a-z0-9_-]+ (validated)
    wsdl: ./wsdls/weather.wsdl    # local path OR https URL
    # optional: pick a service/port when the WSDL has several
    # wsdl_service: WeatherService
    # wsdl_port: WeatherPort
    auth:
      type: wsse_username         # none | wsse_username | basic | client_cert
      username: svc-weather
      password: ${WEATHER_SOAP_PASSWORD}
      use_digest: false
      add_timestamp: true
      timestamp_ttl: 300
    headers:                      # optional custom HTTP headers
      X-Route: internal

  - name: claims
    wsdl: https://internal.example.com/claims?wsdl
    auth:
      type: basic
      username: ${CLAIMS_USER}
      password: ${CLAIMS_PASSWORD}

  - name: legacy
    wsdl: ./wsdls/legacy.wsdl
    auth:
      type: client_cert
      cert_path: /etc/certs/client.pem
      key_path: /etc/certs/client.key
      ca_bundle_path: /etc/certs/ca.pem   # optional
```

Rules:
- `${VAR}` anywhere in a string is expanded from the environment at load time.
  A referenced-but-unset variable is a **startup error** naming the variable
  (never silently empty — the old code's `.get('password', '')` pattern hid this).
- Service names must be unique; duplicates are a startup error.
- A service whose WSDL fails to parse is **skipped with a clear error log**;
  the server still starts with the remaining services (partial availability
  beats total outage). `/health` reports per-service status.

## 6. Tool naming and mapping

- Tool name = `{service_name}_{sanitized_operation_name}`.
- Sanitize to MCP-legal `[a-zA-Z0-9_-]`: replace illegal chars with `_`.
- Keep an explicit map `tool_name → (service_name, real_operation_name, port)`
  in the registry. **Never derive the operation by splitting the tool name** —
  operation names may themselves contain `_`.
- On sanitization collision within a service: append `_2`, `_3`, … and log a warning.
- Tool description = WSDL operation documentation, else generated summary of
  parameters (port `_generate_tool_description` from `gateway_client.py`).
- Tool `inputSchema` = converted input XSD (see §7).

## 7. Code to PORT from the old implementation (the crown jewels)

These encode real fixes against real WSDLs — port them with their behavior
intact, then add tests around them:

1. **`wsdl_converter.py::xsd_to_json_schema`** → `converter.py`
   - XSD→JSON type table, wrapper-list unwrapping (`_is_wrapper_list_type`),
     choice→oneOf, enum facets, extension/base-type merging, XSD attributes,
     nillable→`[type, "null"]`, min/max_occurs→required/array,
     circular-reference guard, qualified-name resolution (`_resolve_type_name`).
   - Additions while porting: map `duration`, `gYear*`, `QName`, `anyType`
     (→ permissive `{}`), `base64Binary` → `{"type":"string","format":"byte"}`.

2. **`soap_translator.py::TimestampedUsernameToken`** → `security.py`
   - Fresh WSU:Timestamp per request (zeep 4.3 compat fix). Keep as-is.

3. **`soap_translator.py::_build_transport_and_wsse`** → `security.py`
   - Rework input to the typed config models from §5; same four auth modes.

4. **`soap_translator.py::_rewrap_list_parameters`** → `invoker.py`
   - JSON arrays → WSDL wrapper structures. **Fix while porting:** resolve the
     binding via the operation's stored port (registry knows it), not
     `bindings.keys()[0]`.

5. **`soap_translator.py::_serialize_zeep_result`** → `serialization.py`
   - Add explicit handling for `decimal.Decimal` (→ float or str),
     `datetime/date/time` (→ ISO string), and `bytes` (→ base64 str) before the
     `str()` fallback.

6. **`soap_translator.py::_normalize_parameters`** → `invoker.py`
   - Auto-wrap a bare scalar for single-parameter operations. (MCP clients
     mostly send objects, but ContextForge tool invocations may not.)

## 8. Code that is deliberately NOT ported

- All of `database.py`, `gateway_client.py` (registration/unregistration,
  tool-ID tracking), Flask app, login/session handling, admin cache endpoints,
  WSDLCache, security-config REST API, templates/static UI.
- The old repo's known bugs die with it: decorator-order auth bypass, temp-file
  WSDL path stored then deleted, stale zeep-cache eviction key, global SQLite
  cache nuked on single-service delete.

## 9. Error handling contract (tools/call)

- **SOAP Fault** → MCP tool result with `isError: true`; text content includes
  fault code + fault string (these are the useful part for an LLM to react to).
  Never a protocol-level error for a fault.
- **Transport error** (timeout, connection refused, TLS) → `isError: true`,
  message names the failure class, not internals. No stack traces to clients.
- **Unknown tool** → standard MCP invalid-params error.
- **Schema-invalid arguments** → let Zeep raise, return `isError: true` with the
  validation message (Zeep's messages name the offending element).
- Log full detail server-side (structured, one line per call: service,
  operation, duration, outcome). **Never log request parameter values or
  response bodies at INFO** — SOAP payloads carry PII/credentials.

## 10. Inbound auth

- Middleware on the MCP endpoint: require `Authorization: Bearer $PROXY_BEARER_TOKEN`,
  compare with `hmac.compare_digest`. 401 otherwise.
- `/health` is unauthenticated (liveness). It returns 200 with per-service
  status (`loaded` / `failed: <reason>`), and 503 only if config itself failed
  to load.
- If `PROXY_BEARER_TOKEN` unset: startup warning banner, endpoint open (dev mode).

## 11. Implementation phases (each ends verifiable)

Phase 1 — Skeleton + config
- `config.py` (pydantic models, ${ENV} expansion, validation errors listed in §5),
  `main.py` with `/health`, empty registry.
- Verify: `pytest tests/test_config.py` — valid file loads; unset env var,
  duplicate name, bad auth type each fail with a message naming the problem.

Phase 2 — Converter port
- Port `xsd_to_json_schema` + helpers into `converter.py` with the §7 additions.
- Verify: `tests/test_converter.py` against the WSDL fixtures in `tests/`
  (reuse existing fixtures from the old test suite; they encode the tricky
  cases — wrapper lists, nested types, attributes). Every operation of every
  fixture produces a schema that `jsonschema.Draft202012Validator.check_schema`
  accepts.

Phase 3 — Registry + MCP server
- `registry.py` builds tool defs from config at startup; wire `mcp` SDK
  streamable-HTTP app into FastAPI; implement `tools/list`; bearer middleware.
- Verify: `tests/test_mcp_e2e.py` — in-process MCP client lists tools for a
  fixture WSDL; names sanitized; wrong/missing bearer → 401.

Phase 4 — Invoker
- `invoker.py` + `security.py` + `serialization.py` (ports per §7, fixes noted
  there). `tools/call` end to end with error contract §9.
- Verify: `tests/test_invoker.py` with a mocked Zeep transport (respond with
  canned SOAP XML): success round-trip, SOAP fault → isError, timeout →
  isError, list rewrapping, each auth type produces the right
  envelope/headers (assert on the outgoing request).

Phase 5 — Hot reload + cleanup + docs
- File-watch on services.yaml (mtime poll is fine) → diff registry → rebuild
  changed services → `tools/list_changed`.
- Delete old implementation files (§4 list), rewrite `requirements.txt`
  (fastapi, uvicorn, mcp, zeep, pydantic, pyyaml — pin versions; drop flask,
  sqlalchemy, psycopg2, gunicorn), rewrite Dockerfile (uvicorn, non-root user,
  PORT env), rewrite README including the ContextForge federation walkthrough
  (Admin UI → Gateways → Add: URL `https://<proxy>/mcp`, transport
  streamable-HTTP, auth bearer).
- Verify: edit services.yaml while server runs → tool list changes without
  restart; `docker build` succeeds; fresh venv + `pip install -r
  requirements.txt` + full `pytest` green.

Phase 6 — Live validation
- Run the server against a public SOAP service (e.g. dneonline calculator WSDL:
  http://www.dneonline.com/calculator.asmx?WSDL) and, if available, register it
  in a local ContextForge instance; invoke a tool through the gateway.
- Verify: an MCP `tools/call Add {intA: 2, intB: 3}` returns 5 through the
  full chain.

## 12. Non-goals / later

- REST/OpenAPI facade, runtime registration API or UI, SQLite registry,
  per-request credential pass-through (design allows adding a `pass_through`
  auth type later: map MCP-request headers → WS-Security), OAuth2
  client-credentials upstream auth, multi-replica coordination.

## 13. Notes for the implementing agent

- Zeep is sync; run SOAP calls via `anyio.to_thread` / `run_in_executor` so the
  MCP event loop isn't blocked. One Zeep client per service, built at load
  time, reused (they are thread-safe for concurrent calls in practice; if in
  doubt serialize calls per service with a lock and note it).
- Pin `zeep` to the version actually tested (old repo claims 4.3 fixes but
  pins 4.2.1 — resolve this: install 4.3.x and run the fixture tests).
- SSRF note: WSDL URLs come from the operator-controlled config file, not from
  request input, so URL fetching is acceptable here. Do not add any endpoint
  that fetches a caller-supplied URL.
- Keep it small. The whole runtime should land well under ~1500 lines. If a
  module grows past that, something from §8 is creeping back in.
