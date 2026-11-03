"""Explicit public-service argument contracts, independent of the CLI adapter."""

from functools import wraps
from inspect import signature

from campus_market.models import MarketError, required

MAX_PAGE = (2**63 - 1) // 20 + 1
TEXT_LIMITS = {
    "token": 200,
    "account": 60,
    "password": 200,
    "title": 120,
    "category": 40,
    "description": 2000,
    "image_ref": 120,
    "reason": 500,
    "keyword": 2000,
    "scope": 20,
    "status": 30,
    "order": 30,
    "minimum": 40,
    "maximum": 40,
    "price": 40,
    "start": 100,
    "end": 100,
    "actor_id": 128,
    "action": 80,
    "decision": 30,
}
IDENTIFIERS = {"product_id", "reservation_id", "cancellation_id", "user_id"}


def validate_value(name, value, optional):
    """Reject types before truth-value branching, string methods or SQL binding."""
    if value is None and optional:
        return
    if name in ("approve", "accept", "bootstrap"):
        valid = type(value) is bool
    elif name in ("version", "epoch", "page"):
        maximum = MAX_PAGE if name == "page" else 2**63 - 1
        valid = type(value) is int and 1 <= value <= maximum
    elif name == "key":
        required(value, "请求键", 100)
        return
    elif name in IDENTIFIERS:
        required(value, name, 128)
        return
    elif name in TEXT_LIMITS:
        valid = isinstance(value, str) and len(value) <= TEXT_LIMITS[name]
    else:
        raise RuntimeError(f"Missing internal parameter contract: {name}")
    if not valid:
        raise MarketError("INVALID_INPUT", f"参数{name}的类型或范围无效", 422)


def validated(function):
    """Bind and validate every declared argument of a public business command."""
    contract = signature(function)

    @wraps(function)
    def checked(*args, **kwargs):
        try:
            bound = contract.bind(*args, **kwargs)
        except TypeError as exc:
            raise MarketError("INVALID_INPUT", "参数缺失、重复或名称无效", 422) from exc
        bound.apply_defaults()
        for name, value in bound.arguments.items():
            if name != "self":
                validate_value(name, value, contract.parameters[name].default is None)
        return function(*bound.args, **bound.kwargs)

    return checked
