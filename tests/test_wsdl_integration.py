"""
Comprehensive integration tests for WSDL to OpenAPI conversion and SOAP proxy
"""
import pytest
import requests
import json
import sys
import os
from jsonschema import validate, ValidationError
from deepdiff import DeepDiff

# Add tests directory to path for conftest imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import (
    REST_URL, SOAP_URL, WSDL_URL, PROXY_URL,
    build_soap_envelope, parse_soap_response
)


class TestSchemaConversion:
    """Test WSDL to OpenAPI schema conversion accuracy"""

    def test_array_types_detected(self, converted_spec):
        """Verify list types are converted to arrays"""
        input_schema = converted_spec['operations'][0]['input_schema']
        output_schema = converted_spec['operations'][0]['output_schema']

        # Input arrays
        assert input_schema['properties']['recentClaims']['type'] == 'array', \
            "recentClaims should be an array"

        # Output arrays
        assert output_schema['properties']['factors']['type'] == 'array', \
            "factors should be an array"
        assert output_schema['properties']['nextSteps']['type'] == 'array', \
            "nextSteps should be an array"

    def test_wrapper_types_unwrapped(self, converted_spec):
        """Verify wrapper types (RecentClaimList, etc) are unwrapped to arrays"""
        input_schema = converted_spec['operations'][0]['input_schema']
        output_schema = converted_spec['operations'][0]['output_schema']

        # recentClaims should be array directly, not object with nested property
        recent_claims = input_schema['properties']['recentClaims']
        assert recent_claims['type'] == 'array', "recentClaims should be array type"
        assert 'items' in recent_claims, "recentClaims should have items"
        assert recent_claims['items']['type'] == 'object', "recentClaims items should be objects"

        # factors should be array directly
        factors = output_schema['properties']['factors']
        assert factors['type'] == 'array', "factors should be array type"
        assert 'items' in factors, "factors should have items"

    def test_required_fields_correct(self, converted_spec):
        """Verify required vs optional field detection"""
        input_schema = converted_spec['operations'][0]['input_schema']

        required = input_schema.get('required', [])

        # Required fields
        assert 'customerId' in required, "customerId should be required"
        assert 'policyId' in required, "policyId should be required"
        assert 'claimType' in required, "claimType should be required"
        assert 'incidentDate' in required, "incidentDate should be required"

        # Optional fields should NOT be in required
        assert 'estimatedAmount' not in required, "estimatedAmount should be optional"
        assert 'customerTenure' not in required, "customerTenure should be optional"
        assert 'policyAge' not in required, "policyAge should be optional"
        assert 'incidentLocation' not in required, "incidentLocation should be optional"
        assert 'recentClaims' not in required, "recentClaims should be optional"

    def test_nested_complex_types(self, converted_spec):
        """Verify nested complex types are properly converted"""
        input_schema = converted_spec['operations'][0]['input_schema']
        output_schema = converted_spec['operations'][0]['output_schema']

        # incidentLocation
        location = input_schema['properties']['incidentLocation']
        assert location['type'] == 'object', "incidentLocation should be object"
        assert 'city' in location['properties'], "incidentLocation should have city"
        assert 'state' in location['properties'], "incidentLocation should have state"
        assert 'zipCode' in location['properties'], "incidentLocation should have zipCode"

        # specialistAssignment
        specialist = output_schema['properties']['specialistAssignment']
        assert specialist['type'] == 'object', "specialistAssignment should be object"
        assert 'specialistId' in specialist['properties'], "specialistAssignment should have specialistId"
        assert 'name' in specialist['properties'], "specialistAssignment should have name"
        assert 'email' in specialist['properties'], "specialistAssignment should have email"
        assert 'phone' in specialist['properties'], "specialistAssignment should have phone"

    def test_primitive_types_mapped(self, converted_spec):
        """Verify XSD primitive types are correctly mapped to JSON types"""
        input_schema = converted_spec['operations'][0]['input_schema']
        output_schema = converted_spec['operations'][0]['output_schema']

        # String types
        assert input_schema['properties']['customerId']['type'] == 'string'
        assert input_schema['properties']['incidentDate']['type'] == 'string'

        # Number types (double -> number)
        assert input_schema['properties']['estimatedAmount']['type'] == 'number'
        assert input_schema['properties']['customerTenure']['type'] == 'number'
        assert input_schema['properties']['policyAge']['type'] == 'number'

        # Integer types
        assert output_schema['properties']['riskScore']['type'] == 'integer'
        assert output_schema['properties']['confidenceScore']['type'] == 'integer'

        # Boolean types
        assert output_schema['properties']['requiresManualReview']['type'] == 'boolean'


class TestRoundTrip:
    """Test round-trip through SOAP proxy"""

    def test_minimal_payload(self, minimal_payload):
        """Minimal payload with only required fields should succeed"""
        # Skip if proxy not available
        try:
            response = requests.post(PROXY_URL, json=minimal_payload, timeout=30)
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        result = response.json()
        assert 'riskScore' in result, "Response should have riskScore"
        assert 'riskLevel' in result, "Response should have riskLevel"

    def test_full_payload(self, full_payload):
        """Full payload with all optional fields should succeed"""
        try:
            response = requests.post(PROXY_URL, json=full_payload, timeout=30)
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        result = response.json()
        assert 'riskScore' in result
        assert 'factors' in result

    def test_high_risk_customer(self, high_risk_payload):
        """CUST-12345 should return HIGH risk"""
        try:
            response = requests.post(PROXY_URL, json=high_risk_payload, timeout=30)
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        assert response.status_code == 200

        result = response.json()
        assert result['riskLevel'] == 'HIGH', f"Expected HIGH risk, got {result['riskLevel']}"
        assert result['riskScore'] >= 70, f"Expected high score, got {result['riskScore']}"
        assert result['requiresManualReview'] == True

    def test_array_fields_accepted(self):
        """Payload with array fields (recentClaims) should work correctly"""
        payload = {
            "customerId": "CUST-ARRAY-TEST",
            "policyId": "POL-TEST-001",
            "claimType": "AUTO_COLLISION",
            "incidentDate": "2025-11-17",
            "recentClaims": [
                {
                    "claimId": "CLM-001",
                    "claimType": "AUTO_COLLISION",
                    "incidentDate": "2024-01-15",
                    "paidAmount": 1000.00
                },
                {
                    "claimId": "CLM-002",
                    "claimType": "AUTO_THEFT",
                    "incidentDate": "2024-06-20",
                    "paidAmount": 5000.00
                }
            ]
        }

        try:
            response = requests.post(PROXY_URL, json=payload, timeout=30)
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"


class TestResponseMapping:
    """Test SOAP XML response maps correctly to JSON"""

    def test_soap_vs_proxy_response_match(self, high_risk_payload):
        """SOAP direct and proxy should return equivalent data"""
        # Call SOAP directly
        soap_xml = build_soap_envelope(high_risk_payload)
        try:
            soap_response = requests.post(
                SOAP_URL,
                data=soap_xml,
                headers={'Content-Type': 'text/xml'},
                timeout=30
            )
            soap_data = parse_soap_response(soap_response.text)
        except requests.exceptions.ConnectionError:
            pytest.skip("SOAP service not available")

        # Call via proxy
        try:
            proxy_response = requests.post(PROXY_URL, json=high_risk_payload, timeout=30)
            proxy_data = proxy_response.json()
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        # Compare key fields
        assert soap_data.get('riskScore') == proxy_data.get('riskScore'), \
            f"riskScore mismatch: SOAP={soap_data.get('riskScore')}, Proxy={proxy_data.get('riskScore')}"
        assert soap_data.get('riskLevel') == proxy_data.get('riskLevel'), \
            f"riskLevel mismatch: SOAP={soap_data.get('riskLevel')}, Proxy={proxy_data.get('riskLevel')}"
        assert soap_data.get('recommendation') == proxy_data.get('recommendation'), \
            f"recommendation mismatch"

    def test_array_serialization(self, high_risk_payload):
        """Arrays in response should serialize correctly"""
        try:
            response = requests.post(PROXY_URL, json=high_risk_payload, timeout=30)
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        result = response.json()

        # factors should be a list
        assert isinstance(result.get('factors'), list), \
            f"factors should be list, got {type(result.get('factors'))}"

        # nextSteps should be a list
        assert isinstance(result.get('nextSteps'), list), \
            f"nextSteps should be list, got {type(result.get('nextSteps'))}"

        # Each factor should have required fields
        if result.get('factors'):
            for factor in result['factors']:
                assert 'factor' in factor, "Each factor should have 'factor' field"
                assert 'impact' in factor, "Each factor should have 'impact' field"
                assert 'description' in factor, "Each factor should have 'description' field"

    def test_nested_object_serialization(self, high_risk_payload):
        """Nested objects should serialize correctly"""
        try:
            response = requests.post(PROXY_URL, json=high_risk_payload, timeout=30)
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        result = response.json()

        # High risk should have specialistAssignment
        if result.get('riskLevel') == 'HIGH':
            specialist = result.get('specialistAssignment')
            assert specialist is not None, "High risk should have specialistAssignment"
            assert 'specialistId' in specialist
            assert 'name' in specialist
            assert 'email' in specialist
            assert 'phone' in specialist


class TestContractValidation:
    """Test request/response validates against generated OpenAPI schema"""

    def test_request_validates_against_schema(self, converted_spec, full_payload):
        """Test payload should validate against input schema"""
        input_schema = converted_spec['operations'][0]['input_schema']

        try:
            validate(instance=full_payload, schema=input_schema)
        except ValidationError as e:
            pytest.fail(f"Payload failed validation: {e.message}")

    def test_response_validates_against_schema(self, converted_spec, high_risk_payload):
        """Actual response should validate against output schema"""
        output_schema = converted_spec['operations'][0]['output_schema']

        try:
            response = requests.post(PROXY_URL, json=high_risk_payload, timeout=30)
            result = response.json()
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        try:
            validate(instance=result, schema=output_schema)
        except ValidationError as e:
            pytest.fail(f"Response failed validation: {e.message}")

    def test_invalid_payload_missing_required(self, converted_spec):
        """Payload missing required fields should fail validation"""
        input_schema = converted_spec['operations'][0]['input_schema']

        invalid_payload = {
            "customerId": "CUST-TEST"
            # Missing policyId, claimType, incidentDate
        }

        with pytest.raises(ValidationError):
            validate(instance=invalid_payload, schema=input_schema)


class TestServiceParity:
    """Test all three services return consistent results"""

    def test_high_risk_parity(self, high_risk_payload):
        """All three services should return identical results for CUST-12345"""
        results = {}

        # 1. REST service direct
        try:
            rest_response = requests.post(REST_URL, json=high_risk_payload, timeout=30)
            results['rest'] = rest_response.json()
        except requests.exceptions.ConnectionError:
            pytest.skip("REST service not available")

        # 2. SOAP service direct
        soap_xml = build_soap_envelope(high_risk_payload)
        try:
            soap_response = requests.post(
                SOAP_URL,
                data=soap_xml,
                headers={'Content-Type': 'text/xml'},
                timeout=30
            )
            results['soap'] = parse_soap_response(soap_response.text)
        except requests.exceptions.ConnectionError:
            pytest.skip("SOAP service not available")

        # 3. SOAP via proxy
        try:
            proxy_response = requests.post(PROXY_URL, json=high_risk_payload, timeout=30)
            results['proxy'] = proxy_response.json()
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        # Compare risk scores
        rest_score = results['rest'].get('riskScore') or results['rest'].get('risk_score')
        soap_score = results['soap'].get('riskScore')
        proxy_score = results['proxy'].get('riskScore')

        assert rest_score == soap_score == proxy_score, \
            f"riskScore mismatch: REST={rest_score}, SOAP={soap_score}, Proxy={proxy_score}"

        # Compare risk levels
        rest_level = results['rest'].get('riskLevel') or results['rest'].get('risk_level')
        soap_level = results['soap'].get('riskLevel')
        proxy_level = results['proxy'].get('riskLevel')

        assert rest_level == soap_level == proxy_level, \
            f"riskLevel mismatch: REST={rest_level}, SOAP={soap_level}, Proxy={proxy_level}"

    def test_low_risk_parity(self, minimal_payload):
        """Normal customer should return LOW risk consistently"""
        results = {}

        # REST
        try:
            rest_response = requests.post(REST_URL, json=minimal_payload, timeout=30)
            results['rest'] = rest_response.json()
        except requests.exceptions.ConnectionError:
            pytest.skip("REST service not available")

        # SOAP direct
        soap_xml = build_soap_envelope(minimal_payload)
        try:
            soap_response = requests.post(
                SOAP_URL,
                data=soap_xml,
                headers={'Content-Type': 'text/xml'},
                timeout=30
            )
            results['soap'] = parse_soap_response(soap_response.text)
        except requests.exceptions.ConnectionError:
            pytest.skip("SOAP service not available")

        # Proxy
        try:
            proxy_response = requests.post(PROXY_URL, json=minimal_payload, timeout=30)
            results['proxy'] = proxy_response.json()
        except requests.exceptions.ConnectionError:
            pytest.skip("Proxy not available")

        # All should be LOW risk
        rest_level = results['rest'].get('riskLevel') or results['rest'].get('risk_level')
        soap_level = results['soap'].get('riskLevel')
        proxy_level = results['proxy'].get('riskLevel')

        assert rest_level == 'LOW', f"REST expected LOW, got {rest_level}"
        assert soap_level == 'LOW', f"SOAP expected LOW, got {soap_level}"
        assert proxy_level == 'LOW', f"Proxy expected LOW, got {proxy_level}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
