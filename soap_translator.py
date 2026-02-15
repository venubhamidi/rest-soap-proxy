"""
SOAP Translator
Handles runtime REST to SOAP translation with security support
"""
from zeep import Client
from zeep.cache import SqliteCache
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken
from zeep.exceptions import Fault as SOAPFault
from requests import Session
from requests.auth import HTTPBasicAuth
from typing import Dict, Any, Optional, Tuple
import logging

from database import Service, WSDLCache, SessionLocal
from config import Config

logger = logging.getLogger(__name__)


class SOAPTranslator:
    """Translates REST/JSON requests to SOAP calls and back"""

    def __init__(self):
        """Initialize SOAP translator with caching"""
        # Setup Zeep cache
        self.cache = SqliteCache(timeout=Config.ZEEP_CACHE_TIMEOUT)
        self.transport = Transport(cache=self.cache, timeout=Config.WSDL_REQUEST_TIMEOUT)

        # In-memory Zeep client cache
        self.zeep_clients: Dict[str, Client] = {}

    def execute_operation(
        self,
        service_name: str,
        operation_name: str,
        parameters: Any
    ) -> Dict[str, Any]:
        """
        Execute SOAP operation via REST/JSON interface

        Args:
            service_name: Name of the service
            operation_name: Name of the operation
            parameters: JSON parameters for the operation (dict or simple value)

        Returns:
            JSON response from SOAP service

        Raises:
            ValueError: If service not found
            SOAPFault: If SOAP call fails
        """
        logger.info(f"Executing {service_name}.{operation_name}")
        logger.debug(f"Raw parameters: {parameters} (type: {type(parameters).__name__})")

        # Get service from database
        service = self._get_service(service_name)

        if not service:
            raise ValueError(f"Service '{service_name}' not found. Please register the WSDL first.")

        # Get operation metadata from database
        operation_metadata = self._get_operation(service, operation_name)

        if not operation_metadata:
            raise ValueError(f"Operation '{operation_name}' not found in service '{service_name}'")

        # Smart parameter handling: auto-wrap simple values for single-parameter operations
        parameters = self._normalize_parameters(parameters, operation_metadata)
        logger.debug(f"Normalized parameters: {parameters}")

        # Get WSDL URL and security config
        wsdl_url = service.wsdl_url
        security_config = service.security_config

        # Get or create Zeep client (with security applied)
        zeep_client = self._get_zeep_client(wsdl_url, service_name, security_config)

        # Get operation
        try:
            service_proxy = zeep_client.service
            operation = getattr(service_proxy, operation_name)
        except AttributeError:
            raise ValueError(f"Operation '{operation_name}' not found in service '{service_name}'")

        # Transform parameters to match WSDL wrapper structure
        if isinstance(parameters, dict):
            parameters = self._rewrap_list_parameters(parameters, zeep_client, operation_name)

        # Execute SOAP call
        try:
            logger.info(f"=" * 60)
            logger.info(f"SOAP REQUEST CONSTRUCTION")
            logger.info(f"Operation: {operation_name}")
            logger.info(f"Parameters being sent to SOAP: {parameters}")
            logger.info(f"Parameter keys: {list(parameters.keys()) if isinstance(parameters, dict) else 'N/A'}")

            # Log each parameter for debugging
            if isinstance(parameters, dict):
                for key, value in parameters.items():
                    logger.info(f"  {key}: {value} (type: {type(value).__name__})")

            logger.info(f"=" * 60)

            logger.debug(f"Calling SOAP operation: {operation_name}")
            result = operation(**parameters)
            logger.info(f"SOAP call successful")
            logger.info(f"Raw result type: {type(result)}")

            # Convert Zeep result to JSON-serializable format
            json_result = self._serialize_zeep_result(result)

            # Update WSDL cache access time
            self._update_cache_access(wsdl_url, service_name)

            return json_result

        except SOAPFault as e:
            logger.error(f"SOAP Fault: {e}")
            raise
        except Exception as e:
            logger.error(f"Error executing SOAP operation: {e}")
            raise

    def _rewrap_list_parameters(self, parameters: Dict[str, Any], zeep_client: Client, operation_name: str) -> Dict[str, Any]:
        """
        Rewrap list parameters to match WSDL wrapper structure.

        JSON Schema exposes arrays directly for usability:
            {"recentClaims": [{...}, {...}]}

        But Zeep/WSDL expects wrapper structure:
            {"recentClaims": {"recentClaim": [{...}, {...}]}}

        This method detects and rewraps such parameters.
        """
        try:
            # Get operation input type from Zeep
            binding_name = list(zeep_client.wsdl.bindings.keys())[0]
            binding = zeep_client.wsdl.bindings[binding_name]
            binding_operation = binding.get(operation_name)

            if not binding_operation or not binding_operation.input.body:
                return parameters

            input_type = binding_operation.input.body.type
            if not hasattr(input_type, 'elements'):
                return parameters

            transformed = dict(parameters)

            for element_tuple in input_type.elements:
                element_name = element_tuple[0]
                element = element_tuple[1]

                if element_name not in transformed:
                    continue

                value = transformed[element_name]

                # Only process if value is a list and element has a wrapper type
                if not isinstance(value, list):
                    continue

                if not hasattr(element, 'type') or not hasattr(element.type, 'elements'):
                    continue

                # Check if this is a wrapper type (single unbounded child element)
                inner_elements = list(element.type.elements)
                if len(inner_elements) == 1:
                    inner_name, inner_element = inner_elements[0]
                    max_occurs = getattr(inner_element, 'max_occurs', 1)

                    # Check if unbounded
                    if max_occurs is None or max_occurs == 'unbounded' or (isinstance(max_occurs, int) and max_occurs > 1):
                        logger.info(f"Rewrapping list parameter '{element_name}' into '{inner_name}'")
                        transformed[element_name] = {inner_name: value}

            return transformed

        except Exception as e:
            logger.warning(f"Could not rewrap parameters (using as-is): {e}")
            return parameters

    def _get_service(self, service_name: str) -> Optional[Service]:
        """Get service from database"""
        db = SessionLocal()
        try:
            service = db.query(Service).filter(Service.name == service_name).first()
            return service
        finally:
            db.close()

    def _get_operation(self, service: Service, operation_name: str):
        """Get operation metadata from database"""
        from database import Operation
        db = SessionLocal()
        try:
            operation = db.query(Operation).filter(
                Operation.service_id == service.id,
                Operation.name == operation_name
            ).first()
            return operation
        finally:
            db.close()

    def _normalize_parameters(self, parameters: Any, operation_metadata) -> Dict[str, Any]:
        """
        Normalize parameters for SOAP call.

        Smart handling:
        - If parameters is already a dict, return as-is
        - If parameters is a simple value (str, int, float, bool) and operation
          has only one parameter, auto-wrap it into an object

        Args:
            parameters: Raw parameters from request
            operation_metadata: Operation metadata from database

        Returns:
            Dictionary of parameters ready for SOAP call
        """
        # Already a dict - use as-is
        if isinstance(parameters, dict):
            return parameters

        # Get input schema
        input_schema = operation_metadata.input_schema
        properties = input_schema.get('properties', {})

        # Check if single parameter operation
        if len(properties) == 1:
            param_name = list(properties.keys())[0]
            logger.info(f"Auto-wrapping simple value into parameter '{param_name}'")
            return {param_name: parameters}

        # Multiple parameters but simple value provided - this is an error
        if len(properties) > 1:
            param_names = list(properties.keys())
            raise ValueError(
                f"Operation requires multiple parameters {param_names}, "
                f"but received a simple value. Please provide an object with all required parameters."
            )

        # No parameters expected
        return {}

    def _get_cache_key(self, wsdl_url: str, security_config: Optional[Dict]) -> str:
        """Build cache key that includes auth type so different configs get different clients"""
        auth_type = 'none'
        if security_config:
            auth_type = security_config.get('auth_type', 'none')
        return f"{wsdl_url}::{auth_type}"

    def _build_transport_and_wsse(
        self, security_config: Optional[Dict]
    ) -> Tuple[Transport, Optional[UsernameToken]]:
        """
        Build a Zeep Transport and optional WSSE plugin based on security config.

        Supports:
        - none: Default transport (no auth)
        - wsse_username: WS-Security UsernameToken header in SOAP envelope
        - basic_auth: HTTP Basic Auth on the transport session
        - client_cert: mTLS client certificate on the transport session

        Returns:
            Tuple of (Transport, wsse_plugin_or_None)
        """
        auth_type = (security_config or {}).get('auth_type', 'none')
        wsse_plugin = None

        if auth_type == 'none' or not security_config:
            return self.transport, None

        # Build a dedicated requests.Session for this security context
        session = Session()

        # Apply custom headers if provided
        custom_headers = security_config.get('custom_headers')
        if custom_headers and isinstance(custom_headers, dict):
            session.headers.update(custom_headers)

        if auth_type == 'wsse_username':
            wsse_conf = security_config.get('wsse', {})
            username = wsse_conf.get('username', '')
            password = wsse_conf.get('password', '')
            use_digest = wsse_conf.get('use_digest', False)
            add_timestamp = wsse_conf.get('add_timestamp', False)

            wsse_plugin = UsernameToken(
                username=username,
                password=password,
                use_digest=use_digest,
                timestamp_token=add_timestamp
            )
            logger.info(f"WS-Security UsernameToken configured (digest={use_digest}, timestamp={add_timestamp})")

        elif auth_type == 'basic_auth':
            ba_conf = security_config.get('basic_auth', {})
            session.auth = HTTPBasicAuth(
                ba_conf.get('username', ''),
                ba_conf.get('password', '')
            )
            logger.info("HTTP Basic Auth configured on transport")

        elif auth_type == 'client_cert':
            cert_conf = security_config.get('client_cert', {})
            cert_path = cert_conf.get('cert_path')
            key_path = cert_conf.get('key_path')
            ca_bundle = cert_conf.get('ca_bundle_path')

            if cert_path and key_path:
                session.cert = (cert_path, key_path)
            elif cert_path:
                session.cert = cert_path

            if ca_bundle:
                session.verify = ca_bundle

            logger.info("Client certificate auth configured on transport")

        transport = Transport(
            session=session,
            cache=self.cache,
            timeout=Config.WSDL_REQUEST_TIMEOUT
        )
        return transport, wsse_plugin

    def _get_zeep_client(self, wsdl_url: str, service_name: str, security_config: Optional[Dict] = None) -> Client:
        """
        Get or create Zeep client (with in-memory caching and security)

        Args:
            wsdl_url: WSDL URL
            service_name: Service name (for logging)
            security_config: Optional security configuration dict

        Returns:
            Zeep Client instance
        """
        cache_key = self._get_cache_key(wsdl_url, security_config)

        if cache_key in self.zeep_clients:
            logger.debug(f"Using cached Zeep client for {service_name}")
            return self.zeep_clients[cache_key]

        logger.info(f"Loading WSDL for {service_name}: {wsdl_url}")

        try:
            transport, wsse_plugin = self._build_transport_and_wsse(security_config)

            client = Client(
                wsdl=wsdl_url,
                transport=transport,
                wsse=wsse_plugin
            )
            self.zeep_clients[cache_key] = client
            logger.info(f"WSDL loaded successfully for {service_name} (auth={security_config.get('auth_type', 'none') if security_config else 'none'})")
            return client

        except Exception as e:
            logger.error(f"Error loading WSDL: {e}")
            raise

    def _serialize_zeep_result(self, result: Any) -> Any:
        """
        Convert Zeep result objects to JSON-serializable format

        Args:
            result: Zeep result object

        Returns:
            JSON-serializable Python object
        """
        # None
        if result is None:
            return None

        # Primitives
        if isinstance(result, (str, int, float, bool)):
            return result

        # Lists
        if isinstance(result, list):
            return [self._serialize_zeep_result(item) for item in result]

        # Zeep CompoundValue (complex types from SOAP)
        if hasattr(result, '__values__'):
            return {
                key: self._serialize_zeep_result(value)
                for key, value in result.__values__.items()
            }

        # Regular dicts
        if isinstance(result, dict):
            return {
                key: self._serialize_zeep_result(value)
                for key, value in result.items()
            }

        # Fallback: convert to string
        return str(result)

    def _update_cache_access(self, wsdl_url: str, service_name: str):
        """Update WSDL cache access time in database"""
        db = SessionLocal()
        try:
            cache_entry = db.query(WSDLCache).filter(WSDLCache.wsdl_url == wsdl_url).first()

            if cache_entry:
                # Update existing entry
                from sqlalchemy import func
                cache_entry.last_accessed = func.now()
            else:
                # Create new entry
                cache_entry = WSDLCache(
                    wsdl_url=wsdl_url,
                    service_name=service_name
                )
                db.add(cache_entry)

            db.commit()

        except Exception as e:
            logger.warning(f"Error updating WSDL cache: {e}")
            db.rollback()
        finally:
            db.close()

    def clear_client_cache(self):
        """Clear in-memory Zeep client cache"""
        logger.info("Clearing Zeep client cache")
        self.zeep_clients.clear()

    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            "cached_clients": len(self.zeep_clients),
            "cached_wsdls": list(self.zeep_clients.keys())
        }


# Global singleton instance
_soap_translator = None


def get_soap_translator() -> SOAPTranslator:
    """Get global SOAP translator instance (singleton)"""
    global _soap_translator
    if _soap_translator is None:
        _soap_translator = SOAPTranslator()
    return _soap_translator
