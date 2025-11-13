"""
Configuration module for SOAP-to-REST Proxy
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Application configuration"""

    # Database
    DATABASE_URL = os.getenv(
        'DATABASE_URL',
        'postgresql://postgres:SOcEkxsIXEwwtyANuFcazdzvwSrKODqe@centerbeam.proxy.rlwy.net:18737/railway'
    )

    # Application
    PORT = int(os.getenv('PORT', 8080))
    HOST = os.getenv('HOST', '0.0.0.0')
    DEBUG = os.getenv('FLASK_ENV') == 'development'

    # SOAP Proxy Base URL (for OpenAPI spec generation)
    PROXY_BASE_URL = os.getenv('PROXY_BASE_URL', 'http://localhost:8080')

    # MCP Gateway (optional)
    GATEWAY_URL = os.getenv('GATEWAY_URL', '')
    GATEWAY_TOKEN = os.getenv('GATEWAY_TOKEN', '')

    # Zeep Configuration
    ZEEP_CACHE_TIMEOUT = int(os.getenv('ZEEP_CACHE_TIMEOUT', 86400))  # 24 hours
    WSDL_REQUEST_TIMEOUT = int(os.getenv('WSDL_REQUEST_TIMEOUT', 30))  # 30 seconds

    @classmethod
    def gateway_configured(cls):
        """Check if Gateway integration is configured"""
        return bool(cls.GATEWAY_URL and cls.GATEWAY_TOKEN)
