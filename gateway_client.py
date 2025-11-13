"""
MCP Gateway Client
Handles registration and management of tools with MCP Gateway
"""
import requests
from typing import Dict, List, Optional
from datetime import datetime
import logging

from database import Service, Operation
from config import Config

logger = logging.getLogger(__name__)


class GatewayClient:
    """Client for MCP Gateway API"""

    def __init__(self, gateway_url: str = None, bearer_token: str = None):
        """
        Args:
            gateway_url: MCP Gateway URL (defaults to Config.GATEWAY_URL)
            bearer_token: Bearer token for authentication (defaults to Config.GATEWAY_TOKEN)
        """
        self.gateway_url = (gateway_url or Config.GATEWAY_URL).rstrip('/')
        self.bearer_token = bearer_token or Config.GATEWAY_TOKEN

        if not self.gateway_url or not self.bearer_token:
            raise ValueError("Gateway URL and bearer token are required")

        self.headers = {
            'Authorization': f'Bearer {self.bearer_token}',
            'Content-Type': 'application/json'
        }

    def register_service(
        self,
        service: Service,
        proxy_base_url: str,
        db_session
    ) -> Dict:
        """
        Register service with MCP Gateway

        Args:
            service: Service model instance
            proxy_base_url: Base URL of SOAP proxy
            db_session: Database session for updating service

        Returns:
            Dict with registration results:
            {
                "server_uuid": "...",
                "mcp_endpoint": "...",
                "tools_registered": 5
            }
        """
        logger.info(f"Registering service {service.name} with Gateway")

        if service.gateway_registered:
            raise ValueError(f"Service {service.name} is already registered with Gateway")

        tool_ids = []

        # Register each operation as a tool
        for operation in service.operations:
            tool_id = self._register_tool(
                service_name=service.name,
                operation=operation,
                proxy_base_url=proxy_base_url
            )

            tool_ids.append(tool_id)

            # Update operation with gateway_tool_id
            operation.gateway_tool_id = tool_id

        logger.info(f"Registered {len(tool_ids)} tools with Gateway")

        # Create virtual MCP server
        server_uuid, mcp_endpoint = self._create_virtual_server(
            service_name=service.name,
            description=service.description or f"SOAP service: {service.name}",
            tool_ids=tool_ids
        )

        logger.info(f"Created virtual server: {server_uuid}")

        # Update service in database
        service.gateway_registered = True
        service.gateway_server_uuid = server_uuid
        service.gateway_mcp_endpoint = mcp_endpoint
        service.gateway_registered_at = datetime.utcnow()

        db_session.commit()

        return {
            "server_uuid": str(server_uuid),
            "mcp_endpoint": mcp_endpoint,
            "tools_registered": len(tool_ids)
        }

    def _register_tool(
        self,
        service_name: str,
        operation: Operation,
        proxy_base_url: str
    ) -> str:
        """
        Register a single tool with Gateway

        Returns:
            Tool ID from Gateway
        """
        path = f"/soap/{service_name}/{operation.name}"
        url = f"{proxy_base_url.rstrip('/')}{path}"

        tool_data = {
            "tool": {
                "name": f"{service_name}_{operation.name}",
                "url": url,
                "description": f"SOAP operation: {operation.name}",
                "integration_type": "REST",
                "request_type": "POST",
                "input_schema": operation.input_schema or {"type": "object"}
            }
        }

        logger.debug(f"Registering tool: {tool_data['tool']['name']}")

        try:
            response = requests.post(
                f"{self.gateway_url}/tools",
                headers=self.headers,
                json=tool_data,
                timeout=30
            )
            response.raise_for_status()

            result = response.json()
            tool_id = result.get('id') or result.get('tool_id')

            if not tool_id:
                raise ValueError(f"Gateway did not return tool ID: {result}")

            logger.debug(f"Tool registered with ID: {tool_id}")
            return tool_id

        except requests.exceptions.RequestException as e:
            logger.error(f"Error registering tool: {e}")
            raise

    def _create_virtual_server(
        self,
        service_name: str,
        description: str,
        tool_ids: List[str]
    ) -> tuple[str, str]:
        """
        Create virtual MCP server

        Returns:
            Tuple of (server_uuid, mcp_endpoint)
        """
        server_data = {
            "server": {
                "name": service_name.lower().replace(' ', '-'),
                "description": description,
                "associatedTools": tool_ids
            }
        }

        logger.debug(f"Creating virtual server: {server_data['server']['name']}")

        try:
            response = requests.post(
                f"{self.gateway_url}/servers",
                headers=self.headers,
                json=server_data,
                timeout=30
            )
            response.raise_for_status()

            result = response.json()
            server_uuid = result.get('id') or result.get('uuid')

            if not server_uuid:
                raise ValueError(f"Gateway did not return server UUID: {result}")

            mcp_endpoint = f"{self.gateway_url}/servers/{server_uuid}/mcp"

            logger.debug(f"Server created: {server_uuid}")
            return server_uuid, mcp_endpoint

        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating virtual server: {e}")
            raise

    def unregister_service(self, service: Service, db_session) -> bool:
        """
        Unregister service from Gateway

        Args:
            service: Service model instance
            db_session: Database session for updating service

        Returns:
            True if successful
        """
        logger.info(f"Unregistering service {service.name} from Gateway")

        if not service.gateway_registered:
            raise ValueError(f"Service {service.name} is not registered with Gateway")

        try:
            # Delete virtual server (this also deletes associated tools in Gateway)
            if service.gateway_server_uuid:
                response = requests.delete(
                    f"{self.gateway_url}/servers/{service.gateway_server_uuid}",
                    headers=self.headers,
                    timeout=30
                )
                response.raise_for_status()

            # Update service in database
            service.gateway_registered = False
            service.gateway_server_uuid = None
            service.gateway_mcp_endpoint = None
            service.gateway_registered_at = None

            # Clear gateway_tool_id from operations
            for operation in service.operations:
                operation.gateway_tool_id = None

            db_session.commit()

            logger.info(f"Service {service.name} unregistered successfully")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Error unregistering service: {e}")
            raise

    def test_connection(self) -> bool:
        """
        Test connection to Gateway

        Returns:
            True if connection successful
        """
        try:
            response = requests.get(
                f"{self.gateway_url}/health",
                headers=self.headers,
                timeout=10
            )
            return response.status_code == 200
        except:
            return False
