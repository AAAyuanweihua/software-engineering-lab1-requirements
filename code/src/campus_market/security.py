"""Password hashing and opaque, hashed-at-rest session tokens."""

import hashlib
import hmac
import secrets

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """Use per-password salt and PBKDF2; never persist the input password."""
    if not isinstance(password, str) or len(password) < 10 or len(password) > 200:
        from campus_market.models import MarketError

        raise MarketError("WEAK_PASSWORD", "密码长度须为10至200字符", 422)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Reject malformed stored hashes without exposing storage details."""
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
        )
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    """Sessions carry random entropy; a fast one-way digest is appropriate here."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    """Return a credential that is only displayed/held by the caller."""
    return secrets.token_urlsafe(32)
