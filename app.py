"""
SOAP-to-REST Proxy Service
Main Flask application
"""
from flask import Flask, request, jsonify, render_template, send_file
import json
import yaml
import tempfile
import os
from io import BytesIO
import logging
from sqlalchemy.exc import IntegrityError

from config import Config
from database import init_db, SessionLocal, Service, Operation
from wsdl_converter import WSDLConverter
from gateway_client import GatewayClient
from soap_translator import get_soap_translator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Initialize database
init_db()

# Get SOAP translator
soap_translator = get_soap_translator()


@app.route('/')
def index():
    """Main web UI"""
    return render_template('index.html', gateway_configured=Config.gateway_configured())


@app.route('/health')
def health():
    """Health check endpoint"""
    from sqlalchemy import text
    db = SessionLocal()
    try:
        # Test database connection
        db.execute(text('SELECT 1'))
        db_status = 'healthy'
    except Exception as e:
        db_status = f'unhealthy: {str(e)}'
    finally:
        db.close()

    return jsonify({
        'status': 'healthy',
        'database': db_status,
        'gateway_configured': Config.gateway_configured(),
        'cache_stats': soap_translator.get_cache_stats()
    })


@app.route('/api/convert', methods=['POST'])
def convert_wsdl():
    """
    Convert WSDL to OpenAPI and register service

    Form data:
        - wsdl_file: WSDL file upload (optional)
        - wsdl_url: WSDL URL (optional)
        - service_name: Service name override (optional)
        - auto_register_gateway: Boolean (optional, default: false)
    """
    logger.info("Received WSDL conversion request")

    # Get WSDL source (file or URL)
    wsdl_file = request.files.get('wsdl_file')
    wsdl_url = request.form.get('wsdl_url')
    service_name = request.form.get('service_name')
    auto_register = request.form.get('auto_register_gateway', 'false').lower() == 'true'

    if not wsdl_file and not wsdl_url:
        return jsonify({'error': 'Please provide either wsdl_file or wsdl_url'}), 400

    # Save uploaded file to temp location
    wsdl_path = None
    if wsdl_file:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.wsdl', delete=False) as tmp:
            wsdl_file.save(tmp)
            wsdl_path = tmp.name
    else:
        wsdl_path = wsdl_url

    db = SessionLocal()

    try:
        # Convert WSDL to OpenAPI
        converter = WSDLConverter(Config.PROXY_BASE_URL)
        result = converter.convert(wsdl_path, service_name)

        # Check if service already exists
        existing = db.query(Service).filter(Service.name == result['service_name']).first()
        if existing:
            return jsonify({'error': f"Service '{result['service_name']}' already exists"}), 409

        # Create service record
        service = Service(
            name=result['service_name'],
            wsdl_url=result['wsdl_url'],
            description=result['description'],
            openapi_spec=result['openapi_spec']
        )
        db.add(service)
        db.flush()  # Get service.id

        # Create operation records
        for op in result['operations']:
            operation = Operation(
                service_id=service.id,
                name=op['name'],
                soap_action=op['soap_action'],
                input_schema=op['input_schema'],
                output_schema=op['output_schema']
            )
            db.add(operation)

        db.commit()
        db.refresh(service)

        logger.info(f"Service {service.name} registered successfully")

        response_data = {
            'success': True,
            'service_id': str(service.id),
            'service_name': service.name,
            'operations_count': len(result['operations']),
            'gateway_registered': False
        }

        # Auto-register with Gateway if requested
        if auto_register and Config.gateway_configured():
            try:
                gateway_client = GatewayClient()
                gateway_result = gateway_client.register_service(
                    service=service,
                    proxy_base_url=Config.PROXY_BASE_URL,
                    db_session=db
                )

                response_data['gateway_registered'] = True
                response_data['mcp_endpoint'] = gateway_result['mcp_endpoint']
                response_data['gateway_server_uuid'] = gateway_result['server_uuid']

                logger.info(f"Service auto-registered with Gateway: {gateway_result['mcp_endpoint']}")

            except Exception as e:
                logger.error(f"Auto-registration with Gateway failed: {e}")
                response_data['gateway_error'] = str(e)

        return jsonify(response_data)

    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        return jsonify({'error': 'Service already exists or database constraint violated'}), 409

    except Exception as e:
        db.rollback()
        logger.error(f"Error converting WSDL: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

    finally:
        # Clean up temp file
        if wsdl_file and wsdl_path and os.path.exists(wsdl_path):
            os.unlink(wsdl_path)
        db.close()


@app.route('/api/services', methods=['GET'])
def list_services():
    """List all registered services"""
    db = SessionLocal()
    try:
        services = db.query(Service).all()
        return jsonify({
            'services': [s.to_dict() for s in services],
            'count': len(services)
        })
    finally:
        db.close()


@app.route('/api/services/<service_id>', methods=['GET'])
def get_service(service_id):
    """Get service details"""
    db = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id).first()

        if not service:
            return jsonify({'error': 'Service not found'}), 404

        service_dict = service.to_dict()
        service_dict['operations'] = [op.to_dict() for op in service.operations]

        return jsonify(service_dict)

    finally:
        db.close()


@app.route('/api/services/<service_id>', methods=['DELETE'])
def delete_service(service_id):
    """Delete service and unregister from Gateway if needed"""
    db = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id).first()

        if not service:
            return jsonify({'error': 'Service not found'}), 404

        # Unregister from Gateway if registered
        if service.gateway_registered and Config.gateway_configured():
            try:
                gateway_client = GatewayClient()
                gateway_client.unregister_service(service, db)
                logger.info(f"Service unregistered from Gateway: {service.name}")
            except Exception as e:
                logger.error(f"Error unregistering from Gateway: {e}")

        # Delete service (cascade deletes operations)
        db.delete(service)
        db.commit()

        logger.info(f"Service deleted: {service.name}")

        return jsonify({'success': True})

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting service: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        db.close()


@app.route('/api/services/<service_id>/openapi.<format>', methods=['GET'])
def download_openapi(service_id, format):
    """Download OpenAPI spec in YAML or JSON format"""
    db = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id).first()

        if not service:
            return jsonify({'error': 'Service not found'}), 404

        openapi_spec = service.openapi_spec

        if format == 'yaml':
            content = yaml.dump(openapi_spec, default_flow_style=False, sort_keys=False)
            mimetype = 'application/x-yaml'
            filename = f"{service.name}-openapi.yaml"
        elif format == 'json':
            content = json.dumps(openapi_spec, indent=2)
            mimetype = 'application/json'
            filename = f"{service.name}-openapi.json"
        else:
            return jsonify({'error': 'Invalid format. Use yaml or json'}), 400

        return send_file(
            BytesIO(content.encode('utf-8')),
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename
        )

    finally:
        db.close()


@app.route('/api/services/<service_id>/register-gateway', methods=['POST'])
def register_with_gateway(service_id):
    """Manually register service with MCP Gateway"""
    if not Config.gateway_configured():
        return jsonify({'error': 'Gateway not configured. Set GATEWAY_URL and GATEWAY_TOKEN'}), 400

    db = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id).first()

        if not service:
            return jsonify({'error': 'Service not found'}), 404

        if service.gateway_registered:
            return jsonify({'error': 'Service already registered with Gateway'}), 409

        # Register with Gateway
        gateway_client = GatewayClient()
        result = gateway_client.register_service(
            service=service,
            proxy_base_url=Config.PROXY_BASE_URL,
            db_session=db
        )

        logger.info(f"Service manually registered with Gateway: {result['mcp_endpoint']}")

        return jsonify({
            'success': True,
            **result
        })

    except Exception as e:
        db.rollback()
        logger.error(f"Error registering with Gateway: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        db.close()


@app.route('/api/services/<service_id>/unregister-gateway', methods=['DELETE'])
def unregister_from_gateway(service_id):
    """Unregister service from MCP Gateway"""
    if not Config.gateway_configured():
        return jsonify({'error': 'Gateway not configured'}), 400

    db = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id).first()

        if not service:
            return jsonify({'error': 'Service not found'}), 404

        if not service.gateway_registered:
            return jsonify({'error': 'Service not registered with Gateway'}), 409

        # Unregister from Gateway
        gateway_client = GatewayClient()
        gateway_client.unregister_service(service, db)

        logger.info(f"Service unregistered from Gateway: {service.name}")

        return jsonify({'success': True})

    except Exception as e:
        db.rollback()
        logger.error(f"Error unregistering from Gateway: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        db.close()


@app.route('/soap/<service_name>/<operation_name>', methods=['POST'])
def execute_soap_operation(service_name, operation_name):
    """
    Execute SOAP operation via REST interface

    This is the runtime endpoint that Gateway calls
    """
    logger.info(f"SOAP execution request: {service_name}.{operation_name}")

    try:
        # Get JSON parameters from request body
        parameters = request.json or {}

        # Execute SOAP operation
        result = soap_translator.execute_operation(
            service_name=service_name,
            operation_name=operation_name,
            parameters=parameters
        )

        return jsonify(result)

    except ValueError as e:
        logger.error(f"Value error: {e}")
        return jsonify({'error': str(e)}), 404

    except Exception as e:
        logger.error(f"Error executing SOAP operation: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': str(e),
            'detail': traceback.format_exc() if Config.DEBUG else None
        }), 500


if __name__ == '__main__':
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG
    )
