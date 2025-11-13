# Deployment Guide - SOAP-to-REST Proxy

## 🎉 Project Complete!

Your SOAP-to-REST Proxy service is ready to deploy to Railway.

---

## 📁 Project Structure

```
soap-rest-proxy/
├── app.py                    ✅ Flask application with all routes
├── config.py                 ✅ Configuration management
├── database.py               ✅ PostgreSQL models
├── wsdl_converter.py         ✅ WSDL → OpenAPI conversion
├── gateway_client.py         ✅ MCP Gateway integration
├── soap_translator.py        ✅ Runtime REST ↔ SOAP translation
├── requirements.txt          ✅ Python dependencies
├── Dockerfile               ✅ Railway deployment config
├── .env.example             ✅ Environment template
├── .gitignore               ✅ Git ignore rules
├── README.md                ✅ Documentation
├── templates/
│   └── index.html           ✅ Web UI with checkbox
└── static/
    ├── css/style.css        ✅ Styling
    └── js/app.js            ✅ Frontend logic
```

---

## 🚀 Deploy to Railway

### Step 1: Initialize Git Repository

```bash
cd /Users/vb/development/AgenticAI/soap-rest-proxy

git init
git add .
git commit -m "Initial commit: SOAP-to-REST Proxy service"
```

### Step 2: Create Railway Project

1. Go to https://railway.app
2. Click "New Project"
3. Select "Deploy from GitHub repo" (or "Empty Project")

### Step 3: Connect Repository

**Option A: GitHub**
```bash
# Create GitHub repository
gh repo create soap-rest-proxy --public --source=. --remote=origin --push

# Railway will auto-detect Dockerfile
```

**Option B: Railway CLI**
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Deploy
railway up
```

### Step 4: Set Environment Variables

In Railway dashboard, add these variables:

```bash
# Database (already configured on Railway)
DATABASE_URL=postgresql://postgres:SOcEkxsIXEwwtyANuFcazdzvwSrKODqe@centerbeam.proxy.rlwy.net:18737/railway

# Application (Railway auto-provides PORT)
PROXY_BASE_URL=https://your-app-name.up.railway.app

# MCP Gateway Integration (optional)
GATEWAY_URL=http://gateway:4444
GATEWAY_TOKEN=your-bearer-token-here
```

### Step 5: Deploy

Railway will automatically:
1. Detect Dockerfile
2. Build Docker image
3. Deploy container
4. Assign public URL

---

## 🧪 Test Deployment

### 1. Check Health

```bash
curl https://your-app.railway.app/health
```

**Expected response:**
```json
{
  "status": "healthy",
  "database": "healthy",
  "gateway_configured": true,
  "cache_stats": {
    "cached_clients": 0,
    "cached_wsdls": []
  }
}
```

### 2. Test with Calculator WSDL

**Via Web UI:**
1. Open https://your-app.railway.app
2. Enter WSDL URL: `http://www.dneonline.com/calculator.asmx?wsdl`
3. Check "Automatically register with MCP Gateway" ✅
4. Click "Convert & Register"

**Via API:**
```bash
curl -X POST https://your-app.railway.app/api/convert \
  -F "wsdl_url=http://www.dneonline.com/calculator.asmx?wsdl" \
  -F "auto_register_gateway=true"
```

### 3. Test SOAP Operation

```bash
curl -X POST https://your-app.railway.app/soap/Calculator/Add \
  -H "Content-Type: application/json" \
  -d '{"intA": 5, "intB": 3}'
```

**Expected response:**
```json
{
  "AddResult": 8
}
```

---

## 🔗 Claude Desktop Integration

### 1. Get MCP Endpoint

After registering a WSDL with auto-registration, copy the MCP endpoint:
```
http://gateway:4444/servers/abc-123-def-456/mcp
```

### 2. Configure Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "calculator": {
      "url": "http://gateway:4444/servers/abc-123-def-456/mcp",
      "headers": {
        "Authorization": "Bearer your-gateway-token"
      }
    }
  }
}
```

### 3. Use in Claude

```
User: "Add 15 and 27 using the calculator"

Claude: [Uses Calculator/Add tool]

Result: 42
```

---

## 📊 Features Implemented

### ✅ Core Functionality
- [x] WSDL file upload
- [x] WSDL URL input
- [x] OpenAPI 3.0 generation
- [x] PostgreSQL storage
- [x] Runtime REST → SOAP translation
- [x] SOAP → REST response conversion

### ✅ Gateway Integration
- [x] Auto-registration checkbox
- [x] Manual registration button
- [x] Unregister functionality
- [x] Gateway status tracking

### ✅ Web UI
- [x] WSDL upload form
- [x] Service list with status badges
- [x] OpenAPI download (YAML/JSON)
- [x] Delete service
- [x] Gateway registration controls

### ✅ API Endpoints
- [x] POST /api/convert
- [x] GET /api/services
- [x] GET /api/services/{id}
- [x] DELETE /api/services/{id}
- [x] GET /api/services/{id}/openapi.{format}
- [x] POST /api/services/{id}/register-gateway
- [x] DELETE /api/services/{id}/unregister-gateway
- [x] POST /soap/{service}/{operation}
- [x] GET /health

### ✅ Database
- [x] Services table with gateway tracking
- [x] Operations table with tool IDs
- [x] WSDL cache table
- [x] Automatic table creation

### ✅ Deployment
- [x] Dockerfile for Railway
- [x] Environment configuration
- [x] Health checks
- [x] Logging

---

## 🐛 Troubleshooting

### Database Connection Failed

**Check:**
```bash
# Verify DATABASE_URL is set
railway variables

# Test connection
railway run python -c "from database import engine; engine.connect()"
```

### WSDL Parse Error

**Common issues:**
- WSDL URL not accessible
- Invalid XML format
- Unsupported SOAP version

**Debug:**
```python
from zeep import Client
client = Client('http://your-wsdl-url')
print(client.service)
```

### Gateway Registration Failed

**Check:**
- GATEWAY_URL and GATEWAY_TOKEN are set
- Gateway is accessible from Railway
- Bearer token is valid

### Port Already in Use (Local)

```bash
# Use different port
PORT=8081 python app.py
```

---

## 📝 Next Steps

1. **Deploy to Railway** ✅
2. **Test with public SOAP services**
3. **Register with MCP Gateway**
4. **Configure Claude Desktop**
5. **Use SOAP services via Claude**

---

## 🎯 Success Criteria

- [ ] Service deploys to Railway successfully
- [ ] Health check returns "healthy"
- [ ] Can upload WSDL and generate OpenAPI
- [ ] Can download OpenAPI specs
- [ ] REST → SOAP translation works
- [ ] Gateway auto-registration works
- [ ] Claude can invoke SOAP operations

---

## 📚 Resources

- **Railway Docs**: https://docs.railway.app
- **Zeep Documentation**: https://docs.python-zeep.org
- **OpenAPI Spec**: https://swagger.io/specification/
- **MCP Protocol**: https://github.com/anthropics/mcp

---

**Ready to deploy?** Run:

```bash
cd /Users/vb/development/AgenticAI/soap-rest-proxy
git init
git add .
git commit -m "Initial commit"
railway init
railway up
```

🚀 **Your SOAP-to-REST Proxy will be live!**
