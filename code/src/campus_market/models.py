"""Domain values shared by persistence, services, and command-line presentation."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import uuid4


class ProductStatus(StrEnum):
    """The seven product states in the approved UML design."""

    DRAFT = "DRAFT"
    REVIEWING = "REVIEWING"
    PUBLISHED = "PUBLISHED"
    RESERVED = "RESERVED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    OFFLINE = "OFFLINE"


class ReservationStatus(StrEnum):
    """Terminal reservations remain stored but no longer occupy a product."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    COMPLETED = "COMPLETED"


ACTIVE = ("PENDING", "CONFIRMED", "CANCEL_PENDING")


class MarketError(Exception):
    """A safe business error; details must never contain credentials or SQL."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status

    def as_dict(self) -> dict:
        """Expose a stable error envelope to the CLI and future GUI adapters."""
        return {"error": self.code, "message": str(self), "status": self.status}


@dataclass(frozen=True)
class User:
    """Authenticated identity, never accepted from untrusted command parameters."""

    id: str
    account: str
    enabled: bool
    admin: bool


def new_id() -> str:
    """Create a portable business identifier."""
    return uuid4().hex


def now() -> str:
    """Store comparable UTC instants; display filtering can use Beijing dates."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def required(value: str, label: str, limit: int = 2000) -> str:
    """Normalize required text without silently truncating user input."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise MarketError("INVALID_INPUT", f"{label}不能为空且不能超过{limit}字符", 422)
    return value.strip()


def price_cents(value: str) -> int:
    """Validate exact decimal money; floats and silent rounding are forbidden."""
    if not isinstance(value, str):
        raise MarketError("INVALID_PRICE", "金额必须以十进制字符串输入", 422)
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise MarketError("INVALID_PRICE", "金额不是有效数字", 422) from exc
    if not amount.is_finite() or amount <= 0 or amount > Decimal("99999.99"):
        raise MarketError("INVALID_PRICE", "金额须大于0且不超过99999.99元", 422)
    if amount.as_tuple().exponent < -2:
        raise MarketError("INVALID_PRICE", "金额最多两位小数，不自动舍入", 422)
    return int(amount * 100)
