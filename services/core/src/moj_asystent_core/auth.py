"""Per-launch credential for the loopback desktop/core boundary."""

import re
import secrets
from dataclasses import dataclass

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}")


@dataclass(frozen=True, repr=False)
class SessionCredential:
    _value: str

    @classmethod
    def generate(cls) -> "SessionCredential":
        return cls(secrets.token_urlsafe(32))

    @classmethod
    def from_value(cls, value: str) -> "SessionCredential":
        if not _TOKEN_PATTERN.fullmatch(value):
            raise ValueError("Session credential must be a high-entropy URL-safe token")
        return cls(value)

    def reveal(self) -> str:
        return self._value

    def matches(self, candidate: str) -> bool:
        return secrets.compare_digest(self._value, candidate)

    def __repr__(self) -> str:
        return "SessionCredential(<redacted>)"
