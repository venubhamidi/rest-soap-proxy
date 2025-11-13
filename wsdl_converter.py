"""
WSDL to OpenAPI Converter
Parses WSDL files and generates OpenAPI 3.0 specifications
"""
from zeep import Client
from zeep.wsdl import Document
import yaml
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class WSDLConverter:
    """Converts WSDL to OpenAPI 3.0 specification"""

    def __init__(self, proxy_base_url: str):
        """
        Args:
            proxy_base_url: Base URL of SOAP proxy (e.g., http://localhost:8080)
        """
        self.proxy_base_url = proxy_base_url.rstrip('/')

    def parse_wsdl(self, wsdl_path: str) -> tuple[Client, Document]:
        """
        Parse WSDL file or URL

        Args:
            wsdl_path: Path to WSDL file or URL

        Returns:
            Tuple of (Zeep Client, WSDL Document)
        """
        logger.info(f"Parsing WSDL from: {wsdl_path}")
        client = Client(wsdl_path)
        return client, client.wsdl

    def convert(self, wsdl_path: str, service_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Convert WSDL to OpenAPI spec

        Args:
            wsdl_path: Path to WSDL file or URL
            service_name: Optional service name override

        Returns:
            Dict containing:
            - openapi_spec: OpenAPI 3.0 specification
            - service_name: Detected service name
            - operations: List of operations with metadata
        """
        client, wsdl_doc = self.parse_wsdl(wsdl_path)

        # Get service info
        if not service_name:
            service_name = list(wsdl_doc.services.keys())[0]

        service = wsdl_doc.services[service_name]
        description = getattr(service, 'documentation', None) or f"SOAP service converted from WSDL"

        logger.info(f"Converting service: {service_name}")

        # Extract operations
        operations = self.extract_operations(wsdl_doc, service)

        # Generate OpenAPI spec
        openapi_spec = self.generate_openapi_spec(
            service_name=service_name,
            description=description,
            operations=operations,
            wsdl_url=wsdl_path
        )

        return {
            'openapi_spec': openapi_spec,
            'service_name': service_name,
            'description': description,
            'operations': operations,
            'wsdl_url': wsdl_path
        }

    def extract_operations(self, wsdl_doc: Document, service) -> List[Dict]:
        """
        Extract all operations from WSDL service

        Returns:
            List of operation dictionaries with metadata
        """
        operations = []

        for port_name, port in service.ports.items():
            binding = port.binding

            for operation_name, binding_operation in binding.all().items():
                # Extract input schema
                input_schema = self.extract_input_schema(binding_operation)

                # Extract output schema
                output_schema = self.extract_output_schema(binding_operation)

                operation_info = {
                    'name': operation_name,
                    'port_name': port_name,
                    'soap_action': binding_operation.soapaction or '',
                    'documentation': getattr(binding_operation.abstract, 'documentation', '') or '',
                    'input_schema': input_schema,
                    'output_schema': output_schema
                }

                operations.append(operation_info)

        logger.info(f"Extracted {len(operations)} operations")
        return operations

    def extract_input_schema(self, binding_operation) -> Dict:
        """Extract input schema from WSDL operation"""
        input_body = binding_operation.input.body

        if not input_body:
            return {"type": "object", "properties": {}}

        element_type = input_body.type
        return self.xsd_to_json_schema(element_type)

    def extract_output_schema(self, binding_operation) -> Dict:
        """Extract output schema from WSDL operation"""
        output_body = binding_operation.output.body

        if not output_body:
            return {"type": "object"}

        element_type = output_body.type
        return self.xsd_to_json_schema(element_type)

    def xsd_to_json_schema(self, xsd_type) -> Dict:
        """
        Convert XSD type to JSON Schema

        Args:
            xsd_type: Zeep XSD type object

        Returns:
            JSON Schema dictionary
        """
        # Type mapping
        XSD_TO_JSON_TYPE = {
            'string': 'string',
            'int': 'integer',
            'integer': 'integer',
            'long': 'integer',
            'short': 'integer',
            'byte': 'integer',
            'decimal': 'number',
            'float': 'number',
            'double': 'number',
            'boolean': 'boolean',
            'date': 'string',
            'dateTime': 'string',
            'time': 'string',
            'base64Binary': 'string',
            'hexBinary': 'string',
            'anyURI': 'string',
        }

        type_name = getattr(xsd_type, 'name', None)

        # Simple type
        if type_name in XSD_TO_JSON_TYPE:
            schema = {"type": XSD_TO_JSON_TYPE[type_name]}
            if type_name in ['date', 'dateTime', 'time']:
                schema['format'] = type_name
            return schema

        # Complex type with elements
        if hasattr(xsd_type, 'elements'):
            schema = {
                "type": "object",
                "properties": {},
                "required": []
            }

            for element in xsd_type.elements:
                element_name = element[0]
                element_obj = element[1]
                element_type_obj = element_obj.type

                # Recursively convert element type
                schema['properties'][element_name] = self.xsd_to_json_schema(element_type_obj)

                # Add description if available
                if hasattr(element_obj, 'documentation') and element_obj.documentation:
                    schema['properties'][element_name]['description'] = element_obj.documentation

                # Check if required
                if not element_obj.is_optional:
                    schema['required'].append(element_name)

            if not schema['required']:
                del schema['required']

            return schema

        # Array type
        if hasattr(xsd_type, 'item_type'):
            return {
                "type": "array",
                "items": self.xsd_to_json_schema(xsd_type.item_type)
            }

        # Fallback for unknown types
        return {"type": "object"}

    def generate_openapi_spec(
        self,
        service_name: str,
        description: str,
        operations: List[Dict],
        wsdl_url: str
    ) -> Dict:
        """
        Generate OpenAPI 3.0 specification

        Args:
            service_name: Service name
            description: Service description
            operations: List of operations
            wsdl_url: Original WSDL URL

        Returns:
            OpenAPI 3.0 specification dictionary
        """
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": service_name,
                "description": description,
                "version": "1.0.0",
                "x-wsdl-url": wsdl_url
            },
            "servers": [
                {
                    "url": self.proxy_base_url,
                    "description": "SOAP-to-REST Proxy Server"
                }
            ],
            "paths": {},
            "components": {
                "schemas": {}
            }
        }

        # Add paths for each operation
        for operation in operations:
            path = f"/soap/{service_name}/{operation['name']}"

            openapi_spec["paths"][path] = {
                "post": {
                    "operationId": operation['name'],
                    "summary": operation['documentation'] or f"SOAP operation: {operation['name']}",
                    "description": operation['documentation'] or f"Execute SOAP operation {operation['name']}",
                    "tags": [service_name],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": operation['input_schema']
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Successful SOAP response",
                            "content": {
                                "application/json": {
                                    "schema": operation['output_schema']
                                }
                            }
                        },
                        "500": {
                            "description": "SOAP fault or error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "error": {"type": "string"},
                                            "detail": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    },
                    # Store SOAP metadata as extension
                    "x-soap-metadata": {
                        "soap_action": operation['soap_action'],
                        "port_name": operation['port_name']
                    }
                }
            }

        return openapi_spec

    def export_yaml(self, openapi_spec: Dict) -> str:
        """Export OpenAPI spec as YAML string"""
        return yaml.dump(openapi_spec, default_flow_style=False, sort_keys=False)
