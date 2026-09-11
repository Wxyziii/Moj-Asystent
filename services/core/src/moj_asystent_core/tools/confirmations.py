"""Single-use, operation-bound confirmation tickets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from .models import ConfirmationDecision, JsonValue


def arguments_digest(arguments: dict[str, JsonValue]) -> str:
    canonical = json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ConfirmationRequest:
    confirmation_id: str
    operation_id: UUID
    call_id: UUID
    tool_name: str
    arguments_digest: str
    action: str
    target: str
    details: tuple[tuple[str, str], ...]
    risk: str
    expires_at: datetime
    persistent_allowed: bool


@dataclass
class _Pending:
    request: ConfirmationRequest
    future: asyncio.Future[Literal["allow", "cancel", "always_allow"]]


class ConfirmationRejected(ValueError):
    pass


class ConfirmationManager:
    def __init__(
        self,
        *,
        ttl_seconds: int = 90,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 5 <= ttl_seconds <= 300:
            raise ValueError("Confirmation TTL must be between 5 and 300 seconds")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pending: dict[str, _Pending] = {}

    def create(
        self,
        *,
        operation_id: UUID,
        call_id: UUID,
        tool_name: str,
        arguments: dict[str, JsonValue],
        action: str,
        target: str,
        details: tuple[tuple[str, str], ...],
        risk: str,
        persistent_allowed: bool,
    ) -> ConfirmationRequest:
        request = ConfirmationRequest(
            confirmation_id=secrets.token_urlsafe(32),
            operation_id=operation_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest(arguments),
            action=action,
            target=target,
            details=details,
            risk=risk,
            expires_at=self._clock() + self._ttl,
            persistent_allowed=persistent_allowed,
        )
        future: asyncio.Future[Literal["allow", "cancel", "always_allow"]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request.confirmation_id] = _Pending(request=request, future=future)
        return request

    async def wait(
        self, request: ConfirmationRequest
    ) -> Literal["allow", "cancel", "always_allow"]:
        pending = self._pending.get(request.confirmation_id)
        if pending is None:
            raise ConfirmationRejected("Confirmation is no longer pending")
        timeout = max(0.0, (request.expires_at - self._clock()).total_seconds())
        try:
            return await asyncio.wait_for(pending.future, timeout)
        except TimeoutError as error:
            raise ConfirmationRejected("Confirmation expired") from error
        finally:
            self._pending.pop(request.confirmation_id, None)

    def resolve(self, decision: ConfirmationDecision) -> ConfirmationRequest:
        pending = self._pending.get(decision.confirmation_id)
        if pending is None or pending.future.done():
            raise ConfirmationRejected("Confirmation is invalid, expired or already used")
        request = pending.request
        if (
            str(request.operation_id) != decision.operation_id
            or str(request.call_id) != decision.call_id
            or request.tool_name != decision.tool_name
            or request.arguments_digest != decision.arguments_digest
        ):
            raise ConfirmationRejected("Confirmation does not match the exact tool request")
        if self._clock() >= request.expires_at:
            self._pending.pop(request.confirmation_id, None)
            pending.future.cancel()
            raise ConfirmationRejected("Confirmation expired")
        if decision.decision == "always_allow" and not request.persistent_allowed:
            raise ConfirmationRejected("Persistent approval is not allowed for this tool")
        pending.future.set_result(decision.decision)
        return request

    def cancel_operation(self, operation_id: UUID) -> None:
        for key, pending in list(self._pending.items()):
            if pending.request.operation_id == operation_id:
                self._pending.pop(key, None)
                if not pending.future.done():
                    pending.future.cancel()

    def pending(self) -> tuple[ConfirmationRequest, ...]:
        now = self._clock()
        return tuple(
            item.request
            for item in self._pending.values()
            if item.request.expires_at > now and not item.future.done()
        )

    def shutdown(self) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()
