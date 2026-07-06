"""Phase 2 verification: XSD → JSON Schema conversion against WSDL fixtures."""
import logging
import os

import pytest
from jsonschema import Draft202012Validator
from zeep import Client

from soap_mcp.converter import xsd_to_json_schema

logging.disable(logging.CRITICAL)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
INSURANCE = os.path.join(FIXTURES, "insurance.wsdl")
CALCULATOR = os.path.join(FIXTURES, "calculator.wsdl")


def iter_operation_input_schemas(wsdl_path):
    """Yield (operation_name, input_schema) for every binding operation."""
    client = Client(wsdl_path)
    for service in client.wsdl.services.values():
        for port in service.ports.values():
            for op_name, binding_op in port.binding.all().items():
                body = binding_op.input.body
                schema = xsd_to_json_schema(body.type) if body else {"type": "object"}
                yield op_name, schema


@pytest.mark.parametrize("wsdl_path", [INSURANCE, CALCULATOR])
def test_every_operation_produces_a_valid_schema(wsdl_path):
    count = 0
    for op_name, schema in iter_operation_input_schemas(wsdl_path):
        # The core Phase 2 guarantee: it's a legal JSON Schema.
        Draft202012Validator.check_schema(schema)
        count += 1
    assert count > 0


@pytest.fixture(scope="module")
def insurance_schemas():
    return dict(iter_operation_input_schemas(INSURANCE))


def test_wrapper_list_unwrapped_to_array(insurance_schemas):
    props = insurance_schemas["checkFraudRisk"]["properties"]
    # RecentClaimList (single unbounded child) → array of the child type.
    assert props["recentClaims"]["type"] == "array"
    assert props["recentClaims"]["items"]["type"] == "object"
    assert "claimId" in props["recentClaims"]["items"]["properties"]
    # StringList → array of strings.
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}


def test_nested_complex_type(insurance_schemas):
    loc = insurance_schemas["checkFraudRisk"]["properties"]["incidentLocation"]
    assert loc["type"] == "object"
    assert set(loc["properties"]) == {"city", "state", "zipCode"}
    assert loc["required"] == ["city", "state"]  # zipCode is minOccurs=0


def test_required_reflects_min_occurs(insurance_schemas):
    schema = insurance_schemas["checkFraudRisk"]
    assert set(schema["required"]) == {"customerId", "claimType"}


def test_xsd_attribute_marked_and_required(insurance_schemas):
    claim = insurance_schemas["checkFraudRisk"]["properties"]["recentClaims"]["items"]
    assert claim["properties"]["id"]["x-xsd-attribute"] is True
    # attribute use="required" → present in required list
    assert "id" in claim["required"]


def test_extension_merges_base_and_derived(insurance_schemas):
    premium = insurance_schemas["checkFraudRisk"]["properties"]["premiumClaim"]
    # base Claim fields + derived premiumTier all present
    assert "claimId" in premium["properties"]
    assert "premiumTier" in premium["properties"]


def test_datetime_format_preserved(insurance_schemas):
    claim = insurance_schemas["checkFraudRisk"]["properties"]["recentClaims"]["items"]
    assert claim["properties"]["filedAt"]["format"] == "dateTime"


def test_nillable_scalar_allows_null(insurance_schemas):
    resp_client = Client(INSURANCE)
    # find checkFraudRisk output type to check nillable 'flagged'
    for service in resp_client.wsdl.services.values():
        for port in service.ports.values():
            binding_op = port.binding.all()["checkFraudRisk"]
            out_schema = xsd_to_json_schema(binding_op.output.body.type)
            assert out_schema["properties"]["flagged"]["type"] == ["boolean", "null"]
            return


def test_calculator_scalar_params(insurance_schemas):
    schemas = dict(iter_operation_input_schemas(CALCULATOR))
    add = schemas["Add"]
    assert add["properties"]["intA"]["type"] == "integer"
    assert set(add["required"]) == {"intA", "intB"}


# --- §7 type-table additions (unit-level, via a name-only stub) --------------


class _SimpleType:
    """Minimal stand-in exposing only `.name`, like a Zeep builtin type."""

    def __init__(self, name):
        self.name = name


@pytest.mark.parametrize(
    "name,expected",
    [
        ("base64Binary", {"type": "string", "format": "byte"}),
        ("anyType", {}),
        ("duration", {"type": "string"}),
        ("gYear", {"type": "string"}),
        ("QName", {"type": "string"}),
        ("date", {"type": "string", "format": "date"}),
        ("int", {"type": "integer"}),
        ("decimal", {"type": "number"}),
    ],
)
def test_type_table_additions(name, expected):
    assert xsd_to_json_schema(_SimpleType(name)) == expected
