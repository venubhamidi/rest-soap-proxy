# SOAP-to-REST Proxy Architecture

## Overview

This service acts as a transparent proxy that converts SOAP web services into REST APIs. It performs a one-time WSDL parsing and stores metadata in PostgreSQL, then handles runtime translation of REST/JSON requests to SOAP XML calls.

## Architecture Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WSDL Upload Phase                            │
│                         (One-time setup)                            │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │   Upload WSDL via Endpoint   │
                    │   POST /api/services/upload  │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │    wsdl_converter.py         │
                    │  - Parse WSDL using Zeep     │
                    │  - Extract all operations    │
                    │  - Convert XSD → JSON Schema │
                    │  - Generate OpenAPI 3.0 spec │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │    PostgreSQL Database       │
                    │                              │
                    │  Services Table:             │
                    │  - service_name              │
                    │  - wsdl_url                  │
                    │  - openapi_spec (JSON)       │
                    │  - gateway_id (optional)     │
                    │                              │
                    │  Operations Table:           │
                    │  - operation_name            │
                    │  - input_schema (JSON)       │
                    │  - output_schema (JSON)      │
                    │  - http_method               │
                    │  - endpoint_path             │
                    │                              │
                    │  WSDLCache Table:            │
                    │  - wsdl_content (XML)        │
                    │  - parsed_operations         │
                    └──────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      Runtime Request Phase                          │
│                   (For each REST API call)                          │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
        ┌───────────────────────────────────────────────┐
        │   REST Client sends JSON request              │
        │   POST /api/{service}/{operation}             │
        │   Content-Type: application/json              │
        │   Body: {"param1": "value1", ...}             │
        └───────────────────┬───────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────────────┐
        │   FastAPI Router (main.py)                    │
        │   - Route to operation handler                │
        │   - Extract service_name, operation_name      │
        └───────────────────┬───────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────────────┐
        │   soap_translator.py                          │
        │   1. Query database for operation metadata    │
        │   2. Get cached Zeep client (or create new)   │
        │   3. Normalize JSON params to Python objects  │
        │   4. Call SOAP service via Zeep              │
        │   5. Serialize SOAP response to JSON          │
        └───────────────────┬───────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────────────┐
        │   Zeep Library                                │
        │   - Construct SOAP envelope from params       │
        │   - Send HTTP POST with XML to SOAP endpoint  │
        │   - Parse SOAP XML response                   │
        │   - Return Python objects                     │
        └───────────────────┬───────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────────────┐
        │   Return JSON response to client              │
        │   Content-Type: application/json              │
        │   Body: {"result": {...}}                     │
        └───────────────────────────────────────────────┘
```

## Database Schema

### Services Table
Stores high-level service information:
- `service_name`: Unique identifier for the service
- `wsdl_url`: Original WSDL endpoint
- `openapi_spec`: Complete OpenAPI 3.0 specification (JSON)
- `gateway_id`: Optional MCP gateway registration ID
- `created_at`: Timestamp of service registration

### Operations Table
Stores metadata for each SOAP operation:
- `operation_name`: Name of the SOAP operation
- `service_id`: Foreign key to Services table
- `input_schema`: JSON Schema for request parameters
- `output_schema`: JSON Schema for response structure
- `http_method`: REST HTTP method (GET/POST/PUT/DELETE)
- `endpoint_path`: REST endpoint path
- `soap_action`: SOAP action header value

### WSDLCache Table
Caches parsed WSDL for performance:
- `wsdl_url`: Cache key
- `wsdl_content`: Raw WSDL XML
- `parsed_operations`: Pre-parsed operation metadata (JSON)
- `expires_at`: Cache expiration timestamp

## Key Components

### 1. wsdl_converter.py
**Purpose**: One-time WSDL parsing and conversion to OpenAPI

**Key Functions**:
- `parse_wsdl(wsdl_url)`: Downloads and parses WSDL using Zeep
- `extract_operations()`: Extracts all SOAP operations from WSDL
- `xsd_to_json_schema()`: Converts XML Schema types to JSON Schema
- `generate_openapi_spec()`: Creates OpenAPI 3.0 specification

**Process**:
1. Create Zeep WSDL client
2. Iterate through all services and ports
3. For each operation:
   - Extract input message parameters
   - Extract output message structure
   - Convert XSD types to JSON Schema types
   - Map to REST HTTP methods
4. Generate complete OpenAPI spec with paths, schemas, components

### 2. soap_translator.py
**Purpose**: Runtime translation of REST requests to SOAP calls

**Key Functions**:
- `execute_operation(service_name, operation_name, parameters)`: Main entry point
- `get_or_create_zeep_client()`: Client pooling/caching
- `normalize_parameters()`: Convert JSON to Python objects matching WSDL types
- `serialize_response()`: Convert Zeep response objects to JSON

**Process**:
1. Query database for service and operation metadata
2. Retrieve or create cached Zeep client for WSDL
3. Normalize JSON parameters to match expected WSDL types
4. Invoke SOAP operation through Zeep library
5. Serialize complex SOAP response objects to JSON
6. Return JSON response

**Caching Strategy**:
- Zeep clients are cached in memory per WSDL URL
- Avoids re-parsing WSDL on every request
- Significant performance improvement for repeated calls

### 3. database.py
**Purpose**: PostgreSQL schema and database operations

**Features**:
- SQLAlchemy ORM models
- Async database operations
- Connection pooling
- Migration support

### 4. main.py
**Purpose**: FastAPI application and routing

**Endpoints**:
- `POST /api/services/upload`: Upload new WSDL and register service
- `GET /api/services`: List all registered services
- `GET /api/services/{service_name}`: Get OpenAPI spec for service
- `POST /api/{service_name}/{operation_name}`: Execute SOAP operation via REST

## What Gets Stored in the Database?

### During WSDL Upload:
1. **Services Table**:
   - Service name and WSDL URL
   - Complete OpenAPI specification (JSON blob)
   - Gateway registration ID (if registered with MCP gateway)

2. **Operations Table** (one row per SOAP operation):
   - Operation name
   - JSON Schema for input parameters
   - JSON Schema for output response
   - REST endpoint mapping (path + HTTP method)
   - SOAP action header value

3. **WSDLCache Table**:
   - Raw WSDL XML content
   - Pre-parsed operation metadata
   - Expiration timestamp for cache invalidation

### NOT Stored in Database:
- Actual request/response data (runtime only)
- Zeep client instances (cached in memory)
- SOAP XML envelopes (generated on-the-fly)

## What Happens During a REST Request?

### Step-by-Step Flow:

1. **Client sends REST request**:
   ```json
   POST /api/risk-assessment/calculateRisk
   {
     "policyNumber": "POL-2024-001",
     "claimAmount": 5000
   }
   ```

2. **FastAPI routes to operation handler**:
   - Extracts `service_name` = "risk-assessment"
   - Extracts `operation_name` = "calculateRisk"

3. **soap_translator queries database**:
   ```sql
   SELECT input_schema, output_schema, soap_action
   FROM operations
   WHERE service_id = (SELECT id FROM services WHERE service_name = 'risk-assessment')
   AND operation_name = 'calculateRisk'
   ```

4. **Get or create Zeep client**:
   - Check in-memory cache for WSDL client
   - If not cached, retrieve WSDL from WSDLCache table
   - Create new Zeep client and cache it

5. **Normalize parameters**:
   - Convert JSON types to Python objects
   - Match parameter names to WSDL expectations
   - Handle nested objects and arrays

6. **Execute SOAP call via Zeep**:
   ```python
   service = zeep_client.service
   result = service.calculateRisk(
       policyNumber="POL-2024-001",
       claimAmount=5000
   )
   ```

7. **Zeep constructs and sends SOAP envelope**:
   ```xml
   <soap:Envelope>
     <soap:Body>
       <calculateRisk>
         <policyNumber>POL-2024-001</policyNumber>
         <claimAmount>5000</claimAmount>
       </calculateRisk>
     </soap:Body>
   </soap:Envelope>
   ```

8. **SOAP service responds with XML**:
   ```xml
   <soap:Envelope>
     <soap:Body>
       <calculateRiskResponse>
         <riskScore>72.5</riskScore>
         <riskLevel>MEDIUM</riskLevel>
       </calculateRiskResponse>
     </soap:Body>
   </soap:Envelope>
   ```

9. **Zeep parses response to Python object**:
   ```python
   {
       'riskScore': 72.5,
       'riskLevel': 'MEDIUM'
   }
   ```

10. **soap_translator serializes to JSON**:
    ```json
    {
      "riskScore": 72.5,
      "riskLevel": "MEDIUM"
    }
    ```

11. **FastAPI returns JSON response to client**

## Performance Optimizations

1. **WSDL Caching**:
   - WSDLs are parsed once and cached in database
   - Avoids expensive XML parsing on every request

2. **Zeep Client Pooling**:
   - Zeep clients are cached in memory per WSDL
   - Reused across multiple requests
   - Eliminates WSDL re-parsing overhead

3. **Database Query Optimization**:
   - Operation metadata retrieved once per request
   - Could be further cached in memory if needed

4. **Async Operations**:
   - FastAPI async/await for concurrent request handling
   - Database operations use async SQLAlchemy

## Error Handling

The service handles various error scenarios:

- **WSDL parsing errors**: Invalid WSDL returns 400 Bad Request
- **SOAP faults**: Translated to HTTP 500 with fault details in JSON
- **Missing operations**: Returns 404 Not Found
- **Parameter validation**: JSON Schema validation before SOAP call
- **Network errors**: Timeout and connection errors properly handled

## MCP Gateway Integration

When a service is uploaded, it can optionally be registered with an MCP gateway:

1. Service uploads WSDL to proxy
2. Proxy generates OpenAPI spec
3. Proxy registers with MCP gateway, sending:
   - Service name
   - OpenAPI specification
   - Callback endpoint for requests
4. MCP gateway assigns `gateway_id` and makes service discoverable
5. Claude Desktop can discover and call service via MCP

## Summary

**At upload time**: WSDL → Parse → Extract metadata → Store OpenAPI + schemas in DB

**At runtime**: REST request → Query DB for metadata → Create SOAP call via Zeep → Return JSON response

The database stores only metadata (schemas, OpenAPI specs), not runtime data. The actual SOAP translation happens in memory using Zeep library with cached clients for performance.
