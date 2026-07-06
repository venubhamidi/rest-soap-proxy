"""
Serialize Zeep result objects to JSON-serializable Python values.

Ported from ``soap_translator._serialize_zeep_result`` with explicit handling
added for ``decimal.Decimal`` (→ float), ``datetime``/``date``/``time`` (→ ISO
string), and ``bytes`` (→ base64 string) ahead of the ``str()`` fallback.
"""
from __future__ import annotations

import base64
import datetime
import decimal
from typing import Any


def serialize(result: Any) -> Any:
    if result is None:
        return None

    # bool before int (bool is a subclass of int).
    if isinstance(result, bool):
        return result
    if isinstance(result, (str, int, float)):
        return result

    if isinstance(result, decimal.Decimal):
        return float(result)

    # datetime before date (datetime is a subclass of date).
    if isinstance(result, (datetime.datetime, datetime.date, datetime.time)):
        return result.isoformat()

    if isinstance(result, bytes):
        return base64.b64encode(result).decode("ascii")

    if isinstance(result, (list, tuple)):
        return [serialize(item) for item in result]

    # Zeep CompoundValue (complex SOAP types).
    if hasattr(result, "__values__"):
        return {key: serialize(value) for key, value in result.__values__.items()}

    if isinstance(result, dict):
        return {key: serialize(value) for key, value in result.items()}

    return str(result)
