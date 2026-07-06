"""
XSD type → JSON Schema conversion.

Ported from ``wsdl_converter.WSDLConverter.xsd_to_json_schema`` and its helpers.
Behavior is preserved (wrapper-list unwrapping, choice→oneOf, enum facets,
extension merging, XSD attributes, nillable, cardinality, circular-ref guard,
qualified-name resolution). Additions per the rewrite plan §7: ``base64Binary``
→ ``{"type":"string","format":"byte"}``; ``anyType`` → permissive ``{}``;
``duration``/``gYear*``/``QName`` mapped to string.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# XSD built-in type (local name) → JSON Schema "type".
XSD_TO_JSON_TYPE: dict[str, str] = {
    "string": "string",
    "normalizedString": "string",
    "token": "string",
    "language": "string",
    "Name": "string",
    "NCName": "string",
    "NMTOKEN": "string",
    "ID": "string",
    "IDREF": "string",
    "int": "integer",
    "integer": "integer",
    "long": "integer",
    "short": "integer",
    "byte": "integer",
    "unsignedLong": "integer",
    "unsignedInt": "integer",
    "unsignedShort": "integer",
    "unsignedByte": "integer",
    "positiveInteger": "integer",
    "nonNegativeInteger": "integer",
    "negativeInteger": "integer",
    "nonPositiveInteger": "integer",
    "decimal": "number",
    "float": "number",
    "double": "number",
    "boolean": "boolean",
    "date": "string",
    "dateTime": "string",
    "time": "string",
    "duration": "string",
    "gYear": "string",
    "gYearMonth": "string",
    "gMonth": "string",
    "gMonthDay": "string",
    "gDay": "string",
    "base64Binary": "string",
    "hexBinary": "string",
    "anyURI": "string",
    "QName": "string",
}

# XSD type → JSON Schema "format" annotation, where a useful one exists.
XSD_FORMATS: dict[str, str] = {
    "date": "date",
    "dateTime": "dateTime",
    "time": "time",
    "base64Binary": "byte",
}


def resolve_type_name(xsd_type: Any) -> Optional[str]:
    """Extract the local type name from an XSD type, handling qualified names."""
    type_name = getattr(xsd_type, "name", None)

    if type_name is None:
        return None

    if isinstance(type_name, str) and type_name.startswith("{"):
        # {http://namespace}localname
        return type_name.split("}")[-1]

    if isinstance(type_name, str) and ":" in type_name:
        # prefixed name: xs:string -> string
        return type_name.split(":")[-1]

    return type_name


def _is_wrapper_list_type(xsd_type: Any) -> tuple[bool, Any]:
    """
    A wrapper type containing exactly one unbounded child element should be
    unwrapped to a JSON array. Returns (is_wrapper, inner_element_or_None).
    """
    if not hasattr(xsd_type, "elements"):
        return False, None

    elements = list(xsd_type.elements)

    if len(elements) == 1:
        _element_name, element_obj = elements[0]
        max_occurs = getattr(element_obj, "max_occurs", 1)
        if _is_unbounded(max_occurs):
            return True, element_obj

    return False, None


def _is_unbounded(max_occurs: Any) -> bool:
    return (
        max_occurs is None
        or max_occurs == "unbounded"
        or (isinstance(max_occurs, int) and max_occurs > 1)
    )


def xsd_to_json_schema(xsd_type: Any, visited: Optional[set] = None) -> dict:
    """
    Convert a Zeep XSD type object to a JSON Schema dict.

    Supports wrapper-type unwrapping (lists), unbounded arrays, required/optional
    detection, qualified-name resolution, and circular-reference protection.
    """
    if visited is None:
        visited = set()

    type_name = resolve_type_name(xsd_type)

    # Simple/built-in types.
    if type_name:
        if type_name == "anyType":
            return {}
        if type_name in XSD_TO_JSON_TYPE:
            schema: dict = {"type": XSD_TO_JSON_TYPE[type_name]}
            fmt = XSD_FORMATS.get(type_name)
            if fmt:
                schema["format"] = fmt
            return schema

    # Circular reference protection.
    type_id = id(xsd_type)
    if type_id in visited:
        logger.warning("Circular reference detected for type: %s", type_name)
        return {"type": "object", "description": f"Circular reference to {type_name}"}
    visited = visited | {type_id}

    # Wrapper list types (e.g. RecentClaimList, StringList) → array.
    is_wrapper, inner_element = _is_wrapper_list_type(xsd_type)
    if is_wrapper:
        return {
            "type": "array",
            "items": xsd_to_json_schema(inner_element.type, visited),
        }

    # XSD choice → JSON Schema oneOf.
    type_class_name = type(xsd_type).__name__
    if "Choice" in type_class_name or (hasattr(xsd_type, "is_choice") and xsd_type.is_choice):
        if hasattr(xsd_type, "elements"):
            options = []
            for element_name, element_obj in xsd_type.elements:
                options.append(
                    {
                        "type": "object",
                        "properties": {
                            element_name: xsd_to_json_schema(element_obj.type, visited)
                        },
                        "required": [element_name],
                    }
                )
            if options:
                return {"oneOf": options}

    # Enumeration restrictions → enum.
    if hasattr(xsd_type, "facets") and xsd_type.facets:
        enum_values: list = []
        for facet_name, facet in xsd_type.facets.items():
            if facet_name == "enumeration":
                if hasattr(facet, "values"):
                    enum_values = list(facet.values)
                elif isinstance(facet, (list, tuple)):
                    enum_values = list(facet)
        if enum_values:
            return {"type": "string", "enum": enum_values}

    # Extension of a base type → merge base properties with this type's elements.
    if hasattr(xsd_type, "extension") and xsd_type.extension:
        base_schema = xsd_to_json_schema(xsd_type.extension, visited)
        if hasattr(xsd_type, "elements"):
            if base_schema.get("type") == "object" and "properties" in base_schema:
                for element_name, element_obj in xsd_type.elements:
                    base_schema["properties"][element_name] = xsd_to_json_schema(
                        element_obj.type, visited
                    )
                    min_occurs = getattr(element_obj, "min_occurs", 1)
                    if min_occurs and min_occurs > 0:
                        base_schema.setdefault("required", []).append(element_name)
        return base_schema

    # Complex type with elements.
    if hasattr(xsd_type, "elements"):
        schema = {"type": "object", "properties": {}, "required": []}

        for element_name, element_obj in xsd_type.elements:
            element_type_obj = element_obj.type
            max_occurs = getattr(element_obj, "max_occurs", 1)
            min_occurs = getattr(element_obj, "min_occurs", 1)
            is_nillable = getattr(element_obj, "nillable", False)

            if _is_unbounded(max_occurs):
                schema["properties"][element_name] = {
                    "type": "array",
                    "items": xsd_to_json_schema(element_type_obj, visited),
                }
            else:
                prop_schema = xsd_to_json_schema(element_type_obj, visited)
                # Nillable scalars allow null.
                if is_nillable and prop_schema.get("type") and prop_schema["type"] != "object":
                    prop_schema["type"] = [prop_schema["type"], "null"]
                schema["properties"][element_name] = prop_schema

            if getattr(element_obj, "documentation", None):
                schema["properties"][element_name]["description"] = element_obj.documentation

            is_optional = getattr(element_obj, "is_optional", False)
            if min_occurs is None:
                min_occurs = 0 if is_optional else 1
            if min_occurs > 0:
                schema["required"].append(element_name)

        # XSD attributes.
        if hasattr(xsd_type, "attributes"):
            attrs = xsd_type.attributes
            if hasattr(attrs, "items"):
                attr_iter = attrs.items()
            elif isinstance(attrs, (list, tuple)):
                attr_iter = attrs
            else:
                attr_iter = []

            for attr_name, attr_obj in attr_iter:
                attr_type = getattr(attr_obj, "type", None)
                attr_schema = (
                    xsd_to_json_schema(attr_type, visited) if attr_type else {"type": "string"}
                )
                attr_schema["x-xsd-attribute"] = True
                schema["properties"][attr_name] = attr_schema
                # zeep 4.3 exposes `.required`; older code checked `.use`.
                if (
                    getattr(attr_obj, "use", None) == "required"
                    or getattr(attr_obj, "required", False)
                ):
                    schema["required"].append(attr_name)

        if not schema["required"]:
            del schema["required"]

        return schema

    # Explicit list/array item type.
    if hasattr(xsd_type, "item_type"):
        return {
            "type": "array",
            "items": xsd_to_json_schema(xsd_type.item_type, visited),
        }

    # Unknown type → permissive object.
    return {"type": "object"}
