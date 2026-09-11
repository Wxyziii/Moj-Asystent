"""Small user-local policy store for write-safe tools."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import PermissionLevel


class ToolPreference(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class _PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    tools: dict[str, ToolPreference]


class PermissionDecision(StrEnum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


def default_policy_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "MojAsystent" / "permissions.json"


class PermissionPolicyStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_policy_path()
        self._preferences: dict[str, ToolPreference] = {}
        self._load()

    def evaluate(self, tool_name: str, permission: PermissionLevel) -> PermissionDecision:
        if permission is PermissionLevel.READ:
            return PermissionDecision.AUTO
        if permission is PermissionLevel.SENSITIVE:
            return PermissionDecision.CONFIRM
        preference = self._preferences.get(tool_name, ToolPreference.ASK)
        return {
            ToolPreference.ALLOW: PermissionDecision.AUTO,
            ToolPreference.ASK: PermissionDecision.CONFIRM,
            ToolPreference.DENY: PermissionDecision.DENY,
        }[preference]

    def set_preference(self, tool_name: str, value: ToolPreference) -> None:
        self._preferences[tool_name] = value
        document = _PolicyDocument(tools=self._preferences)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(document.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def preference(self, tool_name: str) -> ToolPreference:
        return self._preferences.get(tool_name, ToolPreference.ASK)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            if len(raw.encode("utf-8")) > 65_536:
                return
            document = _PolicyDocument.model_validate_json(raw)
        except (OSError, UnicodeError, ValidationError, json.JSONDecodeError):
            return
        self._preferences = dict(document.tools)
