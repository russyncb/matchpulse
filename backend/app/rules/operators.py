from typing import Any

OPERATOR_FUNCS = {
    "eq": lambda a, b: a == b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}


def compare(fact: Any, operator: str, value: Any) -> bool:
    """An unknown fact (None) never satisfies a condition: no data means no alert."""
    if fact is None:
        return False
    return OPERATOR_FUNCS[operator](fact, value)
