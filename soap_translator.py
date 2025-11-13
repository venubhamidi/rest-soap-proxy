"""
SOAP Translator
Handles runtime REST to SOAP translation
"""
from zeep import Client
from zeep.cache import SqliteCache
from zeep.transports import Transport
from zeep.exceptions import Fault as SOAPFault
from typing import Dict, Any, Optional
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

        # Get WSDL URL
        wsdl_url = service.wsdl_url

        # Get or create Zeep client
        zeep_client = self._get_zeep_client(wsdl_url, service_name)

        # Get operation
        try:
            service_proxy = zeep_client.service
            operation = getattr(service_proxy, operation_name)
        except AttributeError:
            raise ValueError(f"Operation '{operation_name}' not found in service '{service_name}'")

        # Execute SOAP call
        try:
            logger.debug(f"Calling SOAP operation: {operation_name}")
            result = operation(**parameters)
            logger.debug(f"SOAP call successful")

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

    def _get_zeep_client(self, wsdl_url: str, service_name: str) -> Client:
        """
        Get or create Zeep client (with in-memory caching)

        Args:
            wsdl_url: WSDL URL
            service_name: Service name (for logging)

        Returns:
            Zeep Client instance
        """
        if wsdl_url in self.zeep_clients:
            logger.debug(f"Using cached Zeep client for {service_name}")
            return self.zeep_clients[wsdl_url]

        logger.info(f"Loading WSDL for {service_name}: {wsdl_url}")

        try:
            client = Client(wsdl=wsdl_url, transport=self.transport)
            self.zeep_clients[wsdl_url] = client
            logger.info(f"WSDL loaded successfully for {service_name}")
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
