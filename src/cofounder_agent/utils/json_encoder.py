"""
JSON Encoding Utilities

Handles serialization of PostgreSQL-specific types like Decimal, UUID, datetime.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


class DecimalEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles PostgreSQL types:
    - Decimal → float
    - UUID → string
    - datetime/date → ISO format string
    """

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


def safe_json_dumps(data: Any, **kwargs) -> str:
    """
    Safely serialize data to JSON, handling Decimal and other PostgreSQL types.

    Args:
        data: Data to serialize
        **kwargs: Additional arguments to pass to json.dumps()

    Returns:
        JSON string

    Example:
        >>> safe_json_dumps({"price": Decimal("19.99")})
        '{"price": 19.99}'
    """
    return json.dumps(data, cls=DecimalEncoder, **kwargs)


def safe_json_load(value: Any, fallback: Any = None) -> Any:
    """
    Safely deserialize a JSON string to a Python object.

    Handles the common pattern ``if isinstance(x, str): x = json.loads(x)``
    that appears throughout service files where PostgreSQL may return a JSON
    column as a pre-parsed object or as a raw string depending on the driver
    version / column type.

    Args:
        value: The value to deserialize.  If it is already a non-string type
               (dict, list, int, etc.) it is returned unchanged.
        fallback: Value to return when ``value`` is a string but cannot be
                  parsed as JSON.  Defaults to ``None``.

    Returns:
        Parsed Python object, or *fallback* on parse error.

    Examples:
        >>> safe_json_load('{"k": 1}')
        {'k': 1}
        >>> safe_json_load({'k': 1})   # already parsed
        {'k': 1}
        >>> safe_json_load('bad json', fallback=[])
        []
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return fallback
    return value


def convert_decimals(obj: Any) -> Any:
    """
    Recursively convert all Decimal objects to float in a data structure.

    Args:
        obj: Data structure (dict, list, or primitive)

    Returns:
        Same structure with Decimals converted to float

    Example:
        >>> convert_decimals({"price": Decimal("19.99"), "items": [Decimal("1.5")]})
        {"price": 19.99, "items": [1.5]}
    """
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {key: convert_decimals(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_decimals(item) for item in obj)
    elif isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj
