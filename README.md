# SOAP-Proxy

**SOAP-to-REST Proxy with Authentication**

A secure, standalone service that converts WSDL SOAP services into REST APIs with OpenAPI specifications. Designed to integrate with MCP Gateway for use with Claude Desktop.

## 🔐 Default Credentials

- **Username:** `admin`
- **Password:** `Alekhya@123`

> ⚠️ **Important:** Change these credentials in production by modifying the authentication logic in `app.py`

## Features

- 🔐 **Secure Authentication** - Username/password login with session management
- 🔄 **WSDL to OpenAPI Conversion** - Automatic conversion of SOAP services to REST APIs
- 📥 **Downloadable OpenAPI Specs** - Export specs in YAML or JSON format
- 🔗 **MCP Gateway Integration** - Optional automatic registration with Claude MCP Gateway
- 🧼 **Runtime SOAP Translation** - Transparent REST ↔ SOAP conversion at runtime
- 💾 **PostgreSQL Storage** - Persistent service registry with operation metadata
- 🌐 **Web UI** - User-friendly interface with checkbox for auto-registration
- 🐳 **Railway Deployment** - Ready to deploy with Dockerfile
- 🔑 **API Key Support** - Optional API key authentication for programmatic access

## Architecture

```
User uploads WSDL
    ↓
SOAP Proxy converts to OpenAPI
    ↓
Stores in PostgreSQL
    ↓
[Optional] Auto-registers with MCP Gateway
    ↓
Claude uses services via MCP
    ↓
SOAP Proxy translates REST → SOAP at runtime
```

## Quick Start

### Local Development

1. **Clone repository**
```bash
cd soap-rest-proxy
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Set environment variables**
```bash
cp .env.example .env
# Edit .env with your database URL and gateway credentials
```

4. **Run application**
```bash
python app.py
```

5. **Open browser**
```
http://localhost:8080
```

### Railway Deployment

1. **Create new Railway project**
2. **Add PostgreSQL database** (Railway auto-configures `DATABASE_URL`)
3. **Set environment variables** in Railway dashboard (see Configuration section below)
4. **Deploy from GitHub** - Connect your repository
5. **Update `PROXY_BASE_URL`** with your actual Railway app URL after first deployment
6. **Redeploy** - Railway will use the Dockerfile automatically

## Configuration

### Environment Variables

Configure these in Railway dashboard or `.env` file for local development:

#### Required Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | ✅ Yes | - | PostgreSQL connection string (auto-provided by Railway) |
| `PORT` | ✅ Yes | `8080` | Application port (auto-provided by Railway) |
| `PROXY_BASE_URL` | ✅ Yes | `http://localhost:8080` | **Must set to your Railway app URL** (e.g., `https://soap-proxy.up.railway.app`) |
| `SECRET_KEY` | ✅ Yes (prod) | `dev-secret-key` | Flask session secret - generate random for production |

#### Optional Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GATEWAY_URL` | ❌ Optional | Empty | MCP Gateway URL (can also configure via UI after login) |
| `GATEWAY_TOKEN` | ❌ Optional | Empty | Gateway bearer token (can also configure via UI after login) |
| `API_KEY` | ❌ Optional | Empty | If set, requires `X-API-Key` header for all API requests |
| `ZEEP_CACHE_TIMEOUT` | ❌ Optional | `86400` | WSDL cache timeout in seconds (24 hours) |
| `WSDL_REQUEST_TIMEOUT` | ❌ Optional | `30` | WSDL request timeout in seconds |

### Example Railway Configuration

**Minimal setup (required):**
```bash
# Railway auto-provides these:
DATABASE_URL=postgresql://postgres:password@host:port/railway
PORT=8080

# You must set these:
PROXY_BASE_URL=https://your-app-name.up.railway.app
SECRET_KEY=generate-a-random-secret-key-here
```

**With Gateway integration (optional):**
```bash
# In addition to the above:
GATEWAY_URL=http://gateway:4444
GATEWAY_TOKEN=your-bearer-token-without-Bearer-prefix
```

### Generating SECRET_KEY

Generate a secure random secret key:

```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32
```

### Gateway Configuration via UI

Instead of setting `GATEWAY_URL` and `GATEWAY_TOKEN` as environment variables, users can configure them through the web UI after logging in:

1. Login to SOAP-Proxy
2. Click "⚙️ Gateway Configuration"
3. Enter Gateway URL and Bearer Token
4. Click "💾 Save Configuration"

This configuration is stored in the user session and persists during the browser session.

## Usage

### 1. Upload WSDL

**Via Web UI:**
- Navigate to http://your-app.railway.app
- Upload WSDL file or provide URL
- Check "Automatically register with MCP Gateway" (if configured)
- Click "Convert & Register"

**Result:**
- OpenAPI spec generated and stored
- Service operations registered
- (Optional) Automatically registered with Gateway

### 2. Download OpenAPI Spec

- Click "Download OpenAPI (YAML)" or "Download OpenAPI (JSON)"
- Use spec with existing OpenAPI tools or register manually

### 3. Use SOAP Operations via REST

**Runtime endpoint:**
```
POST /soap/{service_name}/{operation_name}
Content-Type: application/json

{
  "param1": "value1",
  "param2": "value2"
}
```

**Example:**
```bash
curl -X POST http://your-app.railway.app/soap/WeatherService/GetWeather \
  -H "Content-Type: application/json" \
  -d '{"city": "San Francisco", "country": "US"}'
```

### 4. Claude Desktop Integration

If auto-registered with Gateway, add MCP endpoint to Claude Desktop config:

```json
{
  "mcpServers": {
    "weather-service": {
      "url": "http://gateway:4444/servers/abc-123/mcp",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

## API Endpoints

### Management API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/convert` | POST | Upload WSDL and convert to OpenAPI |
| `/api/services` | GET | List all registered services |
| `/api/services/{id}` | GET | Get service details |
| `/api/services/{id}` | DELETE | Delete service |
| `/api/services/{id}/openapi.yaml` | GET | Download OpenAPI (YAML) |
| `/api/services/{id}/openapi.json` | GET | Download OpenAPI (JSON) |
| `/api/services/{id}/register-gateway` | POST | Manually register with Gateway |
| `/api/services/{id}/unregister-gateway` | DELETE | Unregister from Gateway |

### Runtime API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/soap/{service}/{operation}` | POST | Execute SOAP operation via REST |
| `/health` | GET | Health check + DB status |

## Database Schema

### Services Table
- `id` - UUID primary key
- `name` - Service name (unique)
- `wsdl_url` - Original WSDL URL
- `description` - Service description
- `openapi_spec` - Generated OpenAPI spec (JSONB)
- `gateway_registered` - Gateway registration status
- `gateway_server_uuid` - Gateway server UUID
- `gateway_mcp_endpoint` - MCP endpoint URL
- `gateway_registered_at` - Registration timestamp

### Operations Table
- `id` - UUID primary key
- `service_id` - Foreign key to services
- `name` - Operation name
- `soap_action` - SOAP action header
- `input_schema` - JSON schema for input (JSONB)
- `output_schema` - JSON schema for output (JSONB)
- `gateway_tool_id` - Gateway tool ID

## Configuration

### Environment Variables

```bash
# Database (required)
DATABASE_URL=postgresql://user:pass@host:port/database

# Application
PORT=8080
PROXY_BASE_URL=https://your-app.railway.app

# MCP Gateway (optional - for auto-registration)
GATEWAY_URL=http://gateway:4444
GATEWAY_TOKEN=your-bearer-token

# Zeep Configuration
ZEEP_CACHE_TIMEOUT=86400  # 24 hours
WSDL_REQUEST_TIMEOUT=30   # 30 seconds
```

## Example: Calculator Service

### 1. Register WSDL

**WSDL URL:**
```
http://www.dneonline.com/calculator.asmx?wsdl
```

### 2. Generated OpenAPI

```yaml
openapi: 3.0.0
info:
  title: Calculator
  version: 1.0.0
paths:
  /soap/Calculator/Add:
    post:
      operationId: Add
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                intA:
                  type: integer
                intB:
                  type: integer
```

### 3. Execute Operation

```bash
curl -X POST http://your-app.railway.app/soap/Calculator/Add \
  -H "Content-Type: application/json" \
  -d '{"intA": 5, "intB": 3}'

# Response:
{"AddResult": 8}
```

## Troubleshooting

### Database Connection Error

Check DATABASE_URL environment variable and PostgreSQL connectivity:
```bash
curl http://your-app.railway.app/health
```

### WSDL Parse Error

- Ensure WSDL URL is publicly accessible
- Check WSDL is valid XML
- Verify SOAP version (1.1 or 1.2)

### Gateway Registration Failed

- Verify GATEWAY_URL and GATEWAY_TOKEN are set
- Check Gateway is running and accessible
- Review Gateway logs for errors

### SOAP Execution Error

- Verify parameters match input schema
- Check SOAP service is accessible
- Review application logs

## Development

### Run Tests
```bash
# Install test dependencies
pip install pytest pytest-cov

# Run tests
pytest
```

### Code Structure

```
soap-rest-proxy/
├── app.py                 # Flask application
├── config.py              # Configuration
├── database.py            # PostgreSQL models
├── wsdl_converter.py      # WSDL → OpenAPI
├── gateway_client.py      # MCP Gateway integration
├── soap_translator.py     # Runtime REST ↔ SOAP
├── templates/             # Web UI templates
├── static/                # CSS/JS assets
└── Dockerfile            # Railway deployment
```

## License

MIT

## Contributing

Pull requests welcome! Please ensure:
- Code follows PEP 8
- Tests pass
- Documentation updated

## Support

For issues or questions:
- Create GitHub issue
- Check logs: `docker logs <container-id>`
- Review Railway deployment logs

---

**Built with:**
- Python 3.11
- Flask
- SQLAlchemy
- Zeep (SOAP client)
- PostgreSQL
- Railway (deployment)
