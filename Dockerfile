FROM python:3.11-slim

WORKDIR /app

# System deps for lxml / XML processing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY soap_mcp ./soap_mcp
COPY services.yaml ./services.yaml

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Streamable-HTTP MCP endpoint at /mcp, health at /health.
ENV PORT=8080
EXPOSE 8080

# create_app() is a factory: load config + build the registry at startup.
CMD ["sh", "-c", "uvicorn soap_mcp.main:create_app --factory --host 0.0.0.0 --port ${PORT}"]
