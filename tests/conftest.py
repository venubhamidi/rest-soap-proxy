"""
Shared fixtures and configuration for WSDL integration tests
"""
import pytest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Service URLs
REST_URL = "https://fraud-detection-service.up.railway.app/fraud/check"
SOAP_URL = "https://fraud-detection-soap-service.up.railway.app/ws"
WSDL_URL = "https://fraud-detection-soap-service.up.railway.app/ws/fraud.wsdl"
PROXY_URL = "https://rest-soap-proxy-production.up.railway.app/soap/FraudDetectionPortService/checkFraudRisk"


@pytest.fixture
def minimal_payload():
    """Minimal valid payload with only required fields"""
    return {
        "customerId": "CUST-99999",
        "policyId": "POL-AUTO-1234",
        "claimType": "AUTO_COLLISION",
        "incidentDate": "2025-11-17"
    }


@pytest.fixture
def high_risk_payload():
    """Payload that triggers high risk response (CUST-12345)"""
    return {
        "customerId": "CUST-12345",
        "policyId": "POL-AUTO-7890",
        "claimType": "AUTO_COLLISION",
        "incidentDate": "2025-11-17"
    }


@pytest.fixture
def full_payload():
    """Full payload with all optional fields"""
    return {
        "customerId": "CUST-67890",
        "policyId": "POL-AUTO-5555",
        "claimType": "AUTO_COLLISION",
        "incidentDate": "2025-11-17",
        "estimatedAmount": 5000.00,
        "incidentLocation": {
            "city": "Cary",
            "state": "NC",
            "zipCode": "27513"
        },
        "customerTenure": 2.5,
        "policyAge": 1.0,
        "recentClaims": [
            {
                "claimId": "CLM-001",
                "claimType": "AUTO_COLLISION",
                "incidentDate": "2024-06-15",
                "paidAmount": 3500.00
            }
        ]
    }


@pytest.fixture
def wsdl_converter():
    """Initialize WSDL converter"""
    from wsdl_converter import WSDLConverter
    return WSDLConverter(PROXY_URL.rsplit('/soap/', 1)[0])


@pytest.fixture
def converted_spec(wsdl_converter):
    """Convert WSDL to OpenAPI spec"""
    return wsdl_converter.convert(WSDL_URL)


def build_soap_envelope(payload):
    """Build SOAP XML envelope from JSON payload"""
    def dict_to_xml(d, parent_ns="fraud"):
        xml_parts = []
        for key, value in d.items():
            if isinstance(value, dict):
                inner = dict_to_xml(value, parent_ns)
                xml_parts.append(f"<{parent_ns}:{key}>{inner}</{parent_ns}:{key}>")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        inner = dict_to_xml(item, parent_ns)
                        # Use singular form for list items
                        item_name = key[:-1] if key.endswith('s') else key
                        xml_parts.append(f"<{parent_ns}:{item_name}>{inner}</{parent_ns}:{item_name}>")
                    else:
                        xml_parts.append(f"<{parent_ns}:{key}>{item}</{parent_ns}:{key}>")
            elif isinstance(value, bool):
                xml_parts.append(f"<{parent_ns}:{key}>{'true' if value else 'false'}</{parent_ns}:{key}>")
            else:
                xml_parts.append(f"<{parent_ns}:{key}>{value}</{parent_ns}:{key}>")
        return "".join(xml_parts)

    body_content = dict_to_xml(payload)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:fraud="http://insurance.com/fraud">
  <soap:Body>
    <fraud:checkFraudRiskRequest>
      {body_content}
    </fraud:checkFraudRiskRequest>
  </soap:Body>
</soap:Envelope>'''


def parse_soap_response(xml_text):
    """Parse SOAP XML response to dictionary"""
    from lxml import etree

    # Parse XML
    root = etree.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)

    # Find response element
    ns = {'fraud': 'http://insurance.com/fraud'}
    response = root.find('.//fraud:checkFraudRiskResponse', ns)

    if response is None:
        # Try without namespace
        response = root.find('.//{http://insurance.com/fraud}checkFraudRiskResponse')

    def element_to_dict(element):
        result = {}
        for child in element:
            # Get local name without namespace
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag

            if len(child) > 0:
                # Has children - check if it's a list
                if tag in result:
                    # Already exists, convert to list
                    if not isinstance(result[tag], list):
                        result[tag] = [result[tag]]
                    result[tag].append(element_to_dict(child))
                else:
                    result[tag] = element_to_dict(child)
            else:
                # Leaf node
                value = child.text
                # Type conversion
                if value == 'true':
                    value = True
                elif value == 'false':
                    value = False
                elif value and value.isdigit():
                    value = int(value)

                if tag in result:
                    # Already exists, convert to list
                    if not isinstance(result[tag], list):
                        result[tag] = [result[tag]]
                    result[tag].append(value)
                else:
                    result[tag] = value

        return result

    return element_to_dict(response) if response is not None else {}
