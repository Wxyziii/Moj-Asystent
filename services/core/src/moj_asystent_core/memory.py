"""Structured, explicit and local-only persistence for Milestone 10.

The store deliberately opens a short-lived SQLite connection per operation. It
keeps database handles out of the asyncio loop and avoids sharing a connection
between arbitrary worker threads.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .tools.models import JsonValue

DB_SCHEMA_VERSION = 2
MAX_MEMORY_RESULTS = 12
MAX_MEMORY_CONTEXT_CHARS = 4_096
MAX_ROUTINE_STEPS = 16
MAX_ROUTINE_ARGUMENT_BYTES = 8_192
MAX_WATCHER_JSON_BYTES = 8_192
MAX_WATCHER_EVENT_PAYLOAD_BYTES = 2_048
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_KEY = re.compile(
    r"(?:password|hasło|passwd|api[\s_-]*key|token|credential|secret|sekret|"
    r"private[\s_-]*key|klucz[\s_-]*prywat|card|karta|cvv|pin)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:password|hasło|api[\s_-]*key|token|bearer|secret|cvv|pin)\s*[:=]",
    re.IGNORECASE,
)
_PAYMENT_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryValidationError(ValueError):
    """User-safe validation failure for explicit memory writes."""


class MemoryStoreError(RuntimeError):
    """Expected local persistence failure; callers must fail closed."""


class MemoryStoreCorruptError(MemoryStoreError):
    pass


class MemoryStoreBusyError(MemoryStoreError):
    pass


class MemorySchemaMismatchError(MemoryStoreError):
    pass


class MemoryRecord(MemoryModel):
    memory_id: UUID
    category: Literal["preference", "memory"]
    key: Annotated[str, Field(min_length=1, max_length=96)]
    value: Annotated[str, Field(min_length=1, max_length=2_048)]
    source: Annotated[str, Field(min_length=1, max_length=64)]
    approved: StrictBool = True
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class AliasRecord(MemoryModel):
    alias_id: UUID
    kind: Literal["app", "project"]
    alias: Annotated[str, Field(min_length=1, max_length=96)]
    target: Annotated[str, Field(min_length=1, max_length=1_024)]
    updated_at: datetime


class MemoryContextItem(MemoryModel):
    kind: Literal["preference", "memory", "app_alias", "project_alias"]
    key: Annotated[str, Field(min_length=1, max_length=96)]
    value: Annotated[str, Field(min_length=1, max_length=2_048)]
    source_id: UUID


class RoutineStep(MemoryModel):
    tool_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    arguments: dict[str, JsonValue] = Field(max_length=64)

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Routine arguments must be finite JSON values") from error
        if len(encoded.encode("utf-8")) > MAX_ROUTINE_ARGUMENT_BYTES:
            raise ValueError("Routine arguments exceed the safe limit")
        return value


class RoutineRecord(MemoryModel):
    routine_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=96)]
    description: Annotated[str, Field(max_length=512)] | None = None
    steps: tuple[RoutineStep, ...] = Field(min_length=1, max_length=MAX_ROUTINE_STEPS)
    created_at: datetime
    updated_at: datetime


class RoutineSummary(MemoryModel):
    routine_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=96)]
    step_count: Annotated[StrictInt, Field(ge=1, le=MAX_ROUTINE_STEPS)]
    updated_at: datetime


WatcherType = Literal["window", "process", "file", "resource", "build", "download"]
WatcherStatus = Literal["active", "paused", "completed", "failed", "cancelled", "expired"]
WatcherNotificationLevel = Literal["normal", "quiet"]


class WatcherRecord(MemoryModel):
    watcher_id: UUID
    watcher_type: WatcherType
    status: WatcherStatus
    name: Annotated[str, Field(min_length=1, max_length=96)]
    target: dict[str, JsonValue] = Field(max_length=32)
    condition: dict[str, JsonValue] = Field(max_length=32)
    interval_seconds: Annotated[StrictInt, Field(ge=1, le=3_600)]
    expires_at: datetime | None = None
    one_shot: StrictBool = True
    notification_level: WatcherNotificationLevel = "normal"
    last_observed: dict[str, JsonValue] | None = Field(default=None, max_length=32)
    last_event_type: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    created_at: datetime
    updated_at: datetime


class WatcherEventRecord(MemoryModel):
    event_id: UUID
    watcher_id: UUID
    occurred_at: datetime
    event_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")]
    payload: dict[str, JsonValue] = Field(max_length=32)
    notified: StrictBool
    interpreted: StrictBool


class RememberPreferenceArguments(MemoryModel):
    key: Annotated[str, Field(min_length=1, max_length=96)]
    value: Annotated[str, Field(min_length=1, max_length=2_048)]


class RememberMemoryArguments(MemoryModel):
    key: Annotated[str, Field(min_length=1, max_length=96)]
    value: Annotated[str, Field(min_length=1, max_length=2_048)]


class RememberAliasArguments(MemoryModel):
    kind: Literal["app", "project"]
    alias: Annotated[str, Field(min_length=1, max_length=96)]
    target: Annotated[str, Field(min_length=1, max_length=1_024)]
    overwrite: StrictBool = False


class ListMemoriesArguments(MemoryModel):
    limit: Annotated[StrictInt, Field(ge=1, le=MAX_MEMORY_RESULTS)] = MAX_MEMORY_RESULTS


class ForgetMemoryArguments(MemoryModel):
    memory_id: UUID


class ResolveAliasArguments(MemoryModel):
    alias: Annotated[str, Field(min_length=1, max_length=96)]
    kind: Literal["app", "project"] | None = None


class CreateRoutineArguments(MemoryModel):
    name: Annotated[str, Field(min_length=1, max_length=96)]
    description: Annotated[str, Field(max_length=512)] | None = None
    steps: tuple[RoutineStep, ...] = Field(min_length=1, max_length=MAX_ROUTINE_STEPS)


class MemoryWriteOutput(MemoryModel):
    memory: MemoryRecord


class MemoryListOutput(MemoryModel):
    memories: tuple[MemoryRecord, ...] = Field(max_length=MAX_MEMORY_RESULTS)


class MemoryDeleteOutput(MemoryModel):
    removed: bool


class AliasOutput(MemoryModel):
    alias: AliasRecord


class AliasLookupOutput(MemoryModel):
    found: bool
    alias: AliasRecord | None = None


class RoutineWriteOutput(MemoryModel):
    routine: RoutineRecord


def default_memory_database_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "MojAsystent" / "memory.sqlite3"


def normalize_key(value: str) -> str:
    cleaned = _clean_text(value, "key", 96).casefold()
    normalized = " ".join(re.sub(r"[_-]+", " ", cleaned).split())
    if not normalized:
        raise MemoryValidationError("key ma nieprawidłową długość.")
    return normalized


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", _clean_text(value, "alias", 96))
    normalized = " ".join(normalized.casefold().split())
    if not normalized or "/" in normalized or "\\" in normalized or ":" in normalized:
        raise MemoryValidationError("Alias musi być krótką nazwą, nie ścieżką.")
    return normalized


def _clean_text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise MemoryValidationError(f"{label} musi być tekstem.")
    cleaned = _CONTROL_CHARS.sub(" ", value).strip()
    if not cleaned or len(cleaned) > maximum:
        raise MemoryValidationError(f"{label} ma nieprawidłową długość.")
    return cleaned


def _reject_secret(key: str, value: str) -> None:
    if _SECRET_KEY.search(key) or _SECRET_VALUE.search(value) or _PAYMENT_CARD.search(value):
        raise MemoryValidationError("Dane uwierzytelniające nie mogą być zapisywane w pamięci.")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise MemoryValidationError("Data pamięci musi zawierać strefę czasową.")
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise MemoryStoreCorruptError("Baza zawiera datę bez strefy czasowej.")
    return parsed.astimezone(UTC)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise MemoryStoreCorruptError("Baza zawiera nieprawidłowy identyfikator.") from error


class SQLiteMemoryStore:
    """Versioned structured SQLite store with bounded, explicit operations."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_memory_database_path()).expanduser()
        self._init_lock = threading.Lock()
        self._closed = False
        self._initialize()

    def close(self) -> None:
        self._closed = True

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise MemoryStoreError("Magazyn pamięci jest zamknięty.")
        try:
            connection = sqlite3.connect(self.path, timeout=2.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 2000")
            return connection
        except sqlite3.DatabaseError as error:
            raise MemoryStoreCorruptError("Nie można otworzyć lokalnej bazy pamięci.") from error
        except (OSError, sqlite3.OperationalError) as error:
            raise MemoryStoreError("Nie można otworzyć lokalnej bazy pamięci.") from error

    def _initialize(self) -> None:
        with self._init_lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = self._connect_unchecked()
                try:
                    connection.execute("PRAGMA foreign_keys = ON")
                    connection.execute("PRAGMA busy_timeout = 2000")
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = NORMAL")
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if version > DB_SCHEMA_VERSION:
                        raise MemorySchemaMismatchError("Wersja bazy pamięci jest nowsza.")
                    if version == 0:
                        connection.executescript(_SCHEMA_V1)
                        version = 1
                    if version == 1:
                        connection.executescript(_SCHEMA_V2)
                except MemoryStoreError:
                    connection.rollback()
                    raise
                except sqlite3.DatabaseError as error:
                    connection.rollback()
                    raise MemoryStoreCorruptError(
                        "Migracja bazy pamięci nie powiodła się."
                    ) from error
                finally:
                    connection.close()
            except MemoryStoreError:
                raise
            except (OSError, sqlite3.DatabaseError) as error:
                raise MemoryStoreError("Nie można zainicjalizować bazy pamięci.") from error

    def _connect_unchecked(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _run(self, operation):
        connection = self._connect()
        try:
            return operation(connection)
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise MemoryValidationError("Dane kolidują z istniejącym wpisem.") from error
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).casefold() or "busy" in str(error).casefold():
                raise MemoryStoreBusyError("Lokalna baza pamięci jest chwilowo zajęta.") from error
            raise MemoryStoreError(
                "Operacja na lokalnej bazie pamięci nie powiodła się."
            ) from error
        except sqlite3.DatabaseError as error:
            connection.rollback()
            raise MemoryStoreCorruptError("Lokalna baza pamięci jest uszkodzona.") from error
        finally:
            connection.close()

    def history_retention_enabled(self) -> bool:
        def read(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'history_retention'"
            ).fetchone()
            return bool(row and row[0] == "1")

        return self._run(read)

    def set_history_retention(self, enabled: bool) -> None:
        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES('history_retention', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("1" if enabled else "0",),
                )

        self._run(write)

    def append_message(
        self, conversation_id: UUID, role: Literal["user", "assistant"], content: str
    ) -> None:
        checked = _clean_text(content, "message", 8_192)
        now = _iso(_now())

        def write(connection: sqlite3.Connection) -> None:
            setting = connection.execute(
                "SELECT value FROM settings WHERE key = 'history_retention'"
            ).fetchone()
            if not setting or setting[0] != "1":
                return
            with connection:
                connection.execute(
                    "INSERT INTO conversations(id, created_at, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                    (str(conversation_id), now, now),
                )
                connection.execute(
                    "INSERT INTO messages(id, conversation_id, role, content, created_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (str(uuid4()), str(conversation_id), role, checked, now),
                )

        self._run(write)

    def append_completed_turn(
        self, conversation_id: UUID, user_text: str, assistant_text: str
    ) -> None:
        """Persist one completed turn atomically when history retention is enabled."""
        checked_user = _clean_text(user_text, "message", 8_192)
        checked_assistant = _clean_text(assistant_text, "message", 8_192)
        started = _now()
        user_created = _iso(started)
        assistant_created = _iso(started + timedelta(microseconds=1))
        now = assistant_created

        def write(connection: sqlite3.Connection) -> None:
            setting = connection.execute(
                "SELECT value FROM settings WHERE key = 'history_retention'"
            ).fetchone()
            if not setting or setting[0] != "1":
                return
            with connection:
                connection.execute(
                    "INSERT INTO conversations(id, created_at, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                    (str(conversation_id), now, now),
                )
                connection.executemany(
                    "INSERT INTO messages(id, conversation_id, role, content, created_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (
                        (str(uuid4()), str(conversation_id), "user", checked_user, user_created),
                        (
                            str(uuid4()),
                            str(conversation_id),
                            "assistant",
                            checked_assistant,
                            assistant_created,
                        ),
                    ),
                )

        self._run(write)

    def conversation_messages(self, conversation_id: UUID) -> tuple[dict[str, str], ...]:
        def read(connection: sqlite3.Connection) -> tuple[dict[str, str], ...]:
            rows = connection.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY created_at, id",
                (str(conversation_id),),
            ).fetchall()
            return tuple(
                {
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            )

        return self._run(read)

    def clear_history(self) -> int:
        def clear(connection: sqlite3.Connection) -> int:
            with connection:
                result = connection.execute("DELETE FROM messages")
                connection.execute("DELETE FROM conversations")
                return result.rowcount

        return self._run(clear)

    def remember_preference(self, key: str, value: str, *, source: str = "user") -> MemoryRecord:
        checked_key = normalize_key(key)
        checked_value = _clean_text(value, "value", 2_048)
        _reject_secret(checked_key, checked_value)
        checked_source = _clean_text(source, "source", 64)
        now = _now()

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    "INSERT INTO preferences(key, value, source, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, "
                    "updated_at=excluded.updated_at",
                    (checked_key, checked_value, checked_source, _iso(now), _iso(now)),
                )

        self._run(write)
        existing = self.preference(checked_key)
        if existing is None:
            raise MemoryStoreError("Nie udało się odczytać zapisanej preferencji.")
        return existing

    def preference(self, key: str) -> MemoryRecord | None:
        checked_key = normalize_key(key)

        def read(connection: sqlite3.Connection) -> MemoryRecord | None:
            row = connection.execute(
                "SELECT key, value, source, created_at, updated_at FROM preferences WHERE key = ?",
                (checked_key,),
            ).fetchone()
            if row is None:
                return None
            return MemoryRecord(
                memory_id=UUID(int=0),
                category="preference",
                key=row["key"],
                value=row["value"],
                source=row["source"],
                created_at=_parse_datetime(row["created_at"]),
                updated_at=_parse_datetime(row["updated_at"]),
            )

        return self._run(read)

    def create_memory(
        self,
        key: str,
        value: str,
        *,
        category: Literal["memory"] = "memory",
        source: str = "user",
        expires_at: datetime | None = None,
    ) -> MemoryRecord:
        checked_key = normalize_key(key)
        checked_value = _clean_text(value, "value", 2_048)
        _reject_secret(checked_key, checked_value)
        checked_source = _clean_text(source, "source", 64)
        now = _now()
        memory_id = uuid4()

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    "INSERT INTO memories("
                    "id, category, key, value, source, approved, created_at, "
                    "updated_at, expires_at) "
                    "VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        str(memory_id),
                        category,
                        checked_key,
                        checked_value,
                        checked_source,
                        _iso(now),
                        _iso(now),
                        _iso(expires_at) if expires_at else None,
                    ),
                )

        self._run(write)
        result = self.get_memory(memory_id)
        if result is None:
            raise MemoryStoreError("Nie udało się odczytać zapisanej pamięci.")
        return result

    def get_memory(self, memory_id: UUID) -> MemoryRecord | None:
        def read(connection: sqlite3.Connection) -> MemoryRecord | None:
            row = connection.execute(
                "SELECT id, category, key, value, source, approved, created_at, "
                "updated_at, expires_at "
                "FROM memories WHERE id = ?",
                (str(memory_id),),
            ).fetchone()
            return _memory_row(row) if row else None

        return self._run(read)

    def list_memories(self, *, limit: int = MAX_MEMORY_RESULTS) -> tuple[MemoryRecord, ...]:
        if not 1 <= limit <= MAX_MEMORY_RESULTS:
            raise MemoryValidationError("Limit pamięci jest poza zakresem.")
        now = _iso(_now())

        def read(connection: sqlite3.Connection) -> tuple[MemoryRecord, ...]:
            preference_rows = connection.execute(
                "SELECT key, value, source, created_at, updated_at FROM preferences "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            memory_rows = connection.execute(
                "SELECT id, category, key, value, source, approved, created_at, "
                "updated_at, expires_at "
                "FROM memories WHERE approved = 1 AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (now, limit),
            ).fetchall()
            try:
                records = [
                    MemoryRecord(
                        memory_id=UUID(int=0),
                        category="preference",
                        key=row["key"],
                        value=row["value"],
                        source=row["source"],
                        created_at=_parse_datetime(row["created_at"]),
                        updated_at=_parse_datetime(row["updated_at"]),
                    )
                    for row in preference_rows
                ] + [_memory_row(row) for row in memory_rows]
            except (ValueError, TypeError) as error:
                raise MemoryStoreCorruptError("Pamięć w bazie ma nieprawidłowe dane.") from error
            return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True)[:limit])

        return self._run(read)

    def retrieve_for_prompt(
        self,
        text: str,
        *,
        max_items: int = 8,
        max_characters: int = MAX_MEMORY_CONTEXT_CHARS,
    ) -> tuple[MemoryContextItem, ...]:
        if (
            not 1 <= max_items <= MAX_MEMORY_RESULTS
            or not 256 <= max_characters <= MAX_MEMORY_CONTEXT_CHARS
        ):
            raise MemoryValidationError("Limity pamięci są poza zakresem.")
        query = " ".join(_CONTROL_CHARS.sub(" ", text).casefold().split())[:8_192]
        tokens = {token for token in re.findall(r"[\wąćęłńóśźż]{3,}", query)}
        preference_markers = {"prefer", "wolę", "odpowiedzi", "ustawieni", "krótk"}

        def read(connection: sqlite3.Connection) -> tuple[MemoryContextItem, ...]:
            candidates: list[MemoryContextItem] = []
            if tokens & preference_markers:
                for row in connection.execute(
                    "SELECT key, value, updated_at FROM preferences "
                    "ORDER BY updated_at DESC LIMIT 4"
                ).fetchall():
                    candidates.append(
                        MemoryContextItem(
                            kind="preference",
                            key=row["key"],
                            value=row["value"],
                            source_id=UUID(int=0),
                        )
                    )
            for row in connection.execute(
                "SELECT id, category, key, value, updated_at FROM memories "
                "WHERE approved = 1 AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY updated_at DESC LIMIT 24",
                (_iso(_now()),),
            ).fetchall():
                if row["key"] in tokens or any(
                    token in row["value"].casefold() for token in tokens
                ):
                    candidates.append(
                        MemoryContextItem(
                            kind="memory",
                            key=row["key"],
                            value=row["value"],
                            source_id=_uuid(row["id"]),
                        )
                    )
            for table, kind in (("app_aliases", "app_alias"), ("project_aliases", "project_alias")):
                for row in connection.execute(
                    f"SELECT id, alias_display, target, updated_at FROM {table} "
                    "ORDER BY updated_at DESC LIMIT 24"
                ).fetchall():
                    alias_display = str(row["alias_display"])
                    alias_normalized = alias_display.casefold()
                    if alias_normalized in tokens or (
                        len(alias_normalized) >= 3 and alias_normalized in query
                    ):
                        candidates.append(
                            MemoryContextItem(
                                kind=kind,
                                key=alias_display,
                                value=row["target"],
                                source_id=_uuid(row["id"]),
                            )
                        )
            selected: list[MemoryContextItem] = []
            used = 0
            seen: set[tuple[str, str]] = set()
            for item in candidates:
                marker = (item.kind, item.key)
                size = len(item.key) + len(item.value) + 12
                if marker in seen or len(selected) >= max_items or used + size > max_characters:
                    continue
                seen.add(marker)
                selected.append(item)
                used += size
            return tuple(selected)

        return self._run(read)

    def delete_memory(self, memory_id: UUID) -> bool:
        def delete(connection: sqlite3.Connection) -> bool:
            with connection:
                result = connection.execute("DELETE FROM memories WHERE id = ?", (str(memory_id),))
                return result.rowcount == 1

        return self._run(delete)

    def clear_memories(self) -> int:
        def clear(connection: sqlite3.Connection) -> int:
            with connection:
                total = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                total += connection.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
                total += connection.execute("SELECT COUNT(*) FROM app_aliases").fetchone()[0]
                total += connection.execute("SELECT COUNT(*) FROM project_aliases").fetchone()[0]
                connection.execute("DELETE FROM memories")
                connection.execute("DELETE FROM preferences")
                connection.execute("DELETE FROM app_aliases")
                connection.execute("DELETE FROM project_aliases")
                return int(total)

        return self._run(clear)

    def save_alias(
        self, kind: Literal["app", "project"], alias: str, target: str, *, overwrite: bool = False
    ) -> AliasRecord:
        normalized = normalize_alias(alias)
        display = _clean_text(alias, "alias", 96)
        checked_target = _clean_text(target, "target", 1_024)
        _reject_secret(display, checked_target)
        if "\x00" in checked_target:
            raise MemoryValidationError("Cel aliasu jest nieprawidłowy.")
        now = _iso(_now())
        alias_id = uuid4()
        table = "app_aliases" if kind == "app" else "project_aliases"

        def write(connection: sqlite3.Connection) -> None:
            nonlocal alias_id
            with connection:
                existing = connection.execute(
                    f"SELECT id FROM {table} WHERE alias_norm = ?", (normalized,)
                ).fetchone()
                if existing is not None and not overwrite:
                    raise MemoryValidationError("Alias już istnieje; potwierdź jego aktualizację.")
                if existing is None:
                    connection.execute(
                        f"INSERT INTO {table}(id, alias_norm, alias_display, target, updated_at) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (str(alias_id), normalized, display, checked_target, now),
                    )
                else:
                    alias_id_local = existing[0]
                    connection.execute(
                        f"UPDATE {table} SET alias_display=?, target=?, updated_at=? WHERE id=?",
                        (display, checked_target, now, alias_id_local),
                    )
                    alias_id_local = str(alias_id_local)
                    alias_id = _uuid(alias_id_local)

        self._run(write)
        return AliasRecord(
            alias_id=alias_id,
            kind=kind,
            alias=display,
            target=checked_target,
            updated_at=_parse_datetime(now),
        )

    def resolve_alias(
        self, alias: str, *, kind: Literal["app", "project"] | None = None
    ) -> AliasRecord | None:
        normalized = normalize_alias(alias)
        tables = (("app_aliases", "app"), ("project_aliases", "project"))
        if kind is not None:
            tables = tuple(item for item in tables if item[1] == kind)

        def read(connection: sqlite3.Connection) -> AliasRecord | None:
            for table, table_kind in tables:
                row = connection.execute(
                    f"SELECT id, alias_display, target, updated_at FROM {table} "
                    "WHERE alias_norm = ?",
                    (normalized,),
                ).fetchone()
                if row:
                    return AliasRecord(
                        alias_id=_uuid(row["id"]),
                        kind=table_kind,
                        alias=row["alias_display"],
                        target=row["target"],
                        updated_at=_parse_datetime(row["updated_at"]),
                    )
            return None

        return self._run(read)

    def list_aliases(
        self, *, kind: Literal["app", "project"] | None = None
    ) -> tuple[AliasRecord, ...]:
        tables = (("app_aliases", "app"), ("project_aliases", "project"))
        if kind is not None:
            tables = tuple(item for item in tables if item[1] == kind)

        def read(connection: sqlite3.Connection) -> tuple[AliasRecord, ...]:
            rows: list[AliasRecord] = []
            for table, table_kind in tables:
                rows.extend(
                    AliasRecord(
                        alias_id=_uuid(row["id"]),
                        kind=table_kind,
                        alias=row["alias_display"],
                        target=row["target"],
                        updated_at=_parse_datetime(row["updated_at"]),
                    )
                    for row in connection.execute(
                        f"SELECT id, alias_display, target, updated_at FROM {table} "
                        "ORDER BY alias_norm LIMIT 96"
                    ).fetchall()
                )
            return tuple(sorted(rows, key=lambda item: (item.kind, item.alias.casefold())))

        return self._run(read)

    def delete_alias(self, alias_id: UUID) -> bool:
        def delete(connection: sqlite3.Connection) -> bool:
            with connection:
                for table in ("app_aliases", "project_aliases"):
                    result = connection.execute(
                        f"DELETE FROM {table} WHERE id = ?", (str(alias_id),)
                    )
                    if result.rowcount == 1:
                        return True
                return False

        return self._run(delete)

    def create_watcher(self, record: WatcherRecord) -> WatcherRecord:
        encoded_target = _bounded_json(record.target, MAX_WATCHER_JSON_BYTES, "cel obserwacji")
        encoded_condition = _bounded_json(
            record.condition, MAX_WATCHER_JSON_BYTES, "warunek obserwacji"
        )
        encoded_observed = (
            _bounded_json(record.last_observed, MAX_WATCHER_JSON_BYTES, "stan obserwacji")
            if record.last_observed is not None
            else None
        )

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    "INSERT INTO watchers("
                    "id, watcher_type, status, name, target_json, condition_json, "
                    "interval_seconds, expires_at, one_shot, notification_level, "
                    "last_observed_json, last_event_type, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record.watcher_id),
                        record.watcher_type,
                        record.status,
                        record.name,
                        encoded_target,
                        encoded_condition,
                        record.interval_seconds,
                        _iso(record.expires_at) if record.expires_at else None,
                        int(record.one_shot),
                        record.notification_level,
                        encoded_observed,
                        record.last_event_type,
                        _iso(record.created_at),
                        _iso(record.updated_at),
                    ),
                )

        self._run(write)
        return record

    def get_watcher(self, watcher_id: UUID) -> WatcherRecord | None:
        def read(connection: sqlite3.Connection) -> WatcherRecord | None:
            row = connection.execute(
                "SELECT * FROM watchers WHERE id = ?", (str(watcher_id),)
            ).fetchone()
            return _watcher_row(row) if row is not None else None

        return self._run(read)

    def list_watchers(self) -> tuple[WatcherRecord, ...]:
        def read(connection: sqlite3.Connection) -> tuple[WatcherRecord, ...]:
            rows = connection.execute(
                "SELECT * FROM watchers ORDER BY created_at DESC LIMIT 128"
            ).fetchall()
            return tuple(_watcher_row(row) for row in rows)

        return self._run(read)

    def update_watcher(self, record: WatcherRecord) -> WatcherRecord:
        encoded_target = _bounded_json(record.target, MAX_WATCHER_JSON_BYTES, "cel obserwacji")
        encoded_condition = _bounded_json(
            record.condition, MAX_WATCHER_JSON_BYTES, "warunek obserwacji"
        )
        encoded_observed = (
            _bounded_json(record.last_observed, MAX_WATCHER_JSON_BYTES, "stan obserwacji")
            if record.last_observed is not None
            else None
        )

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                result = connection.execute(
                    "UPDATE watchers SET status=?, name=?, target_json=?, condition_json=?, "
                    "interval_seconds=?, expires_at=?, one_shot=?, notification_level=?, "
                    "last_observed_json=?, last_event_type=?, updated_at=? WHERE id=?",
                    (
                        record.status,
                        record.name,
                        encoded_target,
                        encoded_condition,
                        record.interval_seconds,
                        _iso(record.expires_at) if record.expires_at else None,
                        int(record.one_shot),
                        record.notification_level,
                        encoded_observed,
                        record.last_event_type,
                        _iso(record.updated_at),
                        str(record.watcher_id),
                    ),
                )
                if result.rowcount != 1:
                    raise MemoryValidationError("Obserwacja nie istnieje.")

        self._run(write)
        return record

    def delete_watcher(self, watcher_id: UUID) -> bool:
        def delete(connection: sqlite3.Connection) -> bool:
            with connection:
                result = connection.execute("DELETE FROM watchers WHERE id = ?", (str(watcher_id),))
                return result.rowcount == 1

        return self._run(delete)

    def append_watcher_event(self, record: WatcherEventRecord, *, limit: int = 256) -> None:
        if not 1 <= limit <= 1_024:
            raise MemoryValidationError("Limit historii obserwacji jest poza zakresem.")
        encoded = _bounded_json(
            record.payload, MAX_WATCHER_EVENT_PAYLOAD_BYTES, "zdarzenie obserwacji"
        )

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    "INSERT INTO watcher_events("
                    "id, watcher_id, occurred_at, event_type, payload_json, notified, interpreted) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record.event_id),
                        str(record.watcher_id),
                        _iso(record.occurred_at),
                        record.event_type,
                        encoded,
                        int(record.notified),
                        int(record.interpreted),
                    ),
                )
                connection.execute(
                    "DELETE FROM watcher_events WHERE id IN ("
                    "SELECT id FROM watcher_events ORDER BY occurred_at DESC "
                    "LIMIT -1 OFFSET ?)",
                    (limit,),
                )

        self._run(write)

    def list_watcher_events(
        self, watcher_id: UUID | None = None, *, limit: int = 128
    ) -> tuple[WatcherEventRecord, ...]:
        if not 1 <= limit <= 1_024:
            raise MemoryValidationError("Limit historii obserwacji jest poza zakresem.")

        def read(connection: sqlite3.Connection) -> tuple[WatcherEventRecord, ...]:
            if watcher_id is None:
                rows = connection.execute(
                    "SELECT * FROM watcher_events ORDER BY occurred_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM watcher_events WHERE watcher_id = ? "
                    "ORDER BY occurred_at DESC LIMIT ?",
                    (str(watcher_id), limit),
                ).fetchall()
            return tuple(_watcher_event_row(row) for row in rows)

        return self._run(read)

    def clear_watcher_events(self, watcher_id: UUID | None = None) -> int:
        def clear(connection: sqlite3.Connection) -> int:
            with connection:
                if watcher_id is None:
                    result = connection.execute("DELETE FROM watcher_events")
                else:
                    result = connection.execute(
                        "DELETE FROM watcher_events WHERE watcher_id = ?", (str(watcher_id),)
                    )
                return result.rowcount

        return self._run(clear)

    def create_routine(
        self, name: str, steps: tuple[RoutineStep, ...], *, description: str | None = None
    ) -> RoutineRecord:
        checked_name = _clean_text(name, "routine name", 96)
        if not steps or len(steps) > MAX_ROUTINE_STEPS:
            raise MemoryValidationError("Rutyna musi mieć od 1 do 16 kroków.")
        checked_description = _clean_text(description, "description", 512) if description else None
        normalized = normalize_key(checked_name)
        now = _now()
        routine_id = uuid4()

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    "INSERT INTO routines("
                    "id, name, name_norm, description, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        str(routine_id),
                        checked_name,
                        normalized,
                        checked_description,
                        _iso(now),
                        _iso(now),
                    ),
                )
                connection.executemany(
                    "INSERT INTO routine_steps(routine_id, step_index, tool_name, arguments_json) "
                    "VALUES(?, ?, ?, ?)",
                    [
                        (
                            str(routine_id),
                            index,
                            step.tool_name,
                            json.dumps(
                                step.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                allow_nan=False,
                            ),
                        )
                        for index, step in enumerate(steps)
                    ],
                )

        self._run(write)
        result = self.get_routine(routine_id)
        if result is None:
            raise MemoryStoreError("Nie udało się odczytać zapisanej rutyny.")
        return result

    def get_routine(self, routine_id: UUID) -> RoutineRecord | None:
        def read(connection: sqlite3.Connection) -> RoutineRecord | None:
            row = connection.execute(
                "SELECT id, name, description, created_at, updated_at FROM routines WHERE id = ?",
                (str(routine_id),),
            ).fetchone()
            if row is None:
                return None
            try:
                steps = tuple(
                    RoutineStep(
                        tool_name=step["tool_name"], arguments=json.loads(step["arguments_json"])
                    )
                    for step in connection.execute(
                        "SELECT tool_name, arguments_json FROM routine_steps "
                        "WHERE routine_id = ? ORDER BY step_index",
                        (str(routine_id),),
                    ).fetchall()
                )
                return RoutineRecord(
                    routine_id=_uuid(row["id"]),
                    name=row["name"],
                    description=row["description"],
                    steps=steps,
                    created_at=_parse_datetime(row["created_at"]),
                    updated_at=_parse_datetime(row["updated_at"]),
                )
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise MemoryStoreCorruptError("Rutyna w bazie ma nieprawidłowe dane.") from error

        return self._run(read)

    def list_routines(self) -> tuple[RoutineSummary, ...]:
        def read(connection: sqlite3.Connection) -> tuple[RoutineSummary, ...]:
            try:
                return tuple(
                    RoutineSummary(
                        routine_id=_uuid(row["id"]),
                        name=row["name"],
                        step_count=int(row["step_count"]),
                        updated_at=_parse_datetime(row["updated_at"]),
                    )
                    for row in connection.execute(
                        "SELECT r.id, r.name, r.updated_at, COUNT(s.step_index) AS step_count "
                        "FROM routines r JOIN routine_steps s ON s.routine_id = r.id "
                        "GROUP BY r.id ORDER BY r.name_norm LIMIT 96"
                    ).fetchall()
                )
            except (ValueError, TypeError) as error:
                raise MemoryStoreCorruptError("Rutyna w bazie ma nieprawidłowe dane.") from error

        return self._run(read)

    def rename_routine(self, routine_id: UUID, name: str) -> RoutineRecord:
        checked_name = _clean_text(name, "routine name", 96)
        normalized = normalize_key(checked_name)
        now = _iso(_now())

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                result = connection.execute(
                    "UPDATE routines SET name=?, name_norm=?, updated_at=? WHERE id=?",
                    (checked_name, normalized, now, str(routine_id)),
                )
                if result.rowcount != 1:
                    raise MemoryValidationError("Rutyna nie istnieje.")

        self._run(write)
        result = self.get_routine(routine_id)
        if result is None:
            raise MemoryStoreError("Nie udało się odczytać przemianowanej rutyny.")
        return result

    def delete_routine(self, routine_id: UUID) -> bool:
        def delete(connection: sqlite3.Connection) -> bool:
            with connection:
                result = connection.execute("DELETE FROM routines WHERE id = ?", (str(routine_id),))
                return result.rowcount == 1

        return self._run(delete)


def _memory_row(row: sqlite3.Row) -> MemoryRecord:
    try:
        return MemoryRecord(
            memory_id=_uuid(row["id"]),
            category="memory",
            key=row["key"],
            value=row["value"],
            source=row["source"],
            approved=bool(row["approved"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
            expires_at=_parse_datetime(row["expires_at"]) if row["expires_at"] else None,
        )
    except (ValueError, TypeError) as error:
        raise MemoryStoreCorruptError("Pamięć w bazie ma nieprawidłowe dane.") from error


def _bounded_json(value: object, maximum: int, label: str) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise MemoryValidationError(f"{label} ma nieprawidłowy format.") from error
    if len(encoded.encode("utf-8")) > maximum:
        raise MemoryValidationError(f"{label} jest zbyt duże.")
    return encoded


def _watcher_row(row: sqlite3.Row) -> WatcherRecord:
    try:
        target = json.loads(row["target_json"])
        condition = json.loads(row["condition_json"])
        observed = (
            json.loads(row["last_observed_json"]) if row["last_observed_json"] is not None else None
        )
        if not isinstance(target, dict) or not isinstance(condition, dict):
            raise ValueError("Watcher JSON must be objects")
        if observed is not None and not isinstance(observed, dict):
            raise ValueError("Watcher observed state must be an object")
        return WatcherRecord(
            watcher_id=_uuid(row["id"]),
            watcher_type=row["watcher_type"],
            status=row["status"],
            name=row["name"],
            target=target,
            condition=condition,
            interval_seconds=row["interval_seconds"],
            expires_at=_parse_datetime(row["expires_at"]) if row["expires_at"] else None,
            one_shot=bool(row["one_shot"]),
            notification_level=row["notification_level"],
            last_observed=observed,
            last_event_type=row["last_event_type"],
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise MemoryStoreCorruptError("Obserwacja w bazie ma nieprawidłowe dane.") from error


def _watcher_event_row(row: sqlite3.Row) -> WatcherEventRecord:
    try:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("Watcher event payload must be an object")
        return WatcherEventRecord(
            event_id=_uuid(row["id"]),
            watcher_id=_uuid(row["watcher_id"]),
            occurred_at=_parse_datetime(row["occurred_at"]),
            event_type=row["event_type"],
            payload=payload,
            notified=bool(row["notified"]),
            interpreted=bool(row["interpreted"]),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise MemoryStoreCorruptError("Historia obserwacji ma nieprawidłowe dane.") from error


_SCHEMA_V1 = """
BEGIN;
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY CHECK(length(key) BETWEEN 1 AND 64),
    value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 32)
);
INSERT OR IGNORE INTO settings(key, value) VALUES('history_retention', '0');
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 8192),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY CHECK(length(key) BETWEEN 1 AND 96),
    value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 2048),
    source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK(category = 'memory'),
    key TEXT NOT NULL CHECK(length(key) BETWEEN 1 AND 96),
    value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 2048),
    source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 64),
    approved INTEGER NOT NULL CHECK(approved IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS memories_key_idx ON memories(key, updated_at);
CREATE TABLE IF NOT EXISTS app_aliases (
    id TEXT PRIMARY KEY,
    alias_norm TEXT NOT NULL UNIQUE CHECK(length(alias_norm) BETWEEN 1 AND 96),
    alias_display TEXT NOT NULL CHECK(length(alias_display) BETWEEN 1 AND 96),
    target TEXT NOT NULL CHECK(length(target) BETWEEN 1 AND 1024),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_aliases (
    id TEXT PRIMARY KEY,
    alias_norm TEXT NOT NULL UNIQUE CHECK(length(alias_norm) BETWEEN 1 AND 96),
    alias_display TEXT NOT NULL CHECK(length(alias_display) BETWEEN 1 AND 96),
    target TEXT NOT NULL CHECK(length(target) BETWEEN 1 AND 1024),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 96),
    name_norm TEXT NOT NULL UNIQUE CHECK(length(name_norm) BETWEEN 1 AND 96),
    description TEXT CHECK(description IS NULL OR length(description) <= 512),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routine_steps (
    routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL CHECK(step_index >= 0 AND step_index < 16),
    tool_name TEXT NOT NULL CHECK(length(tool_name) BETWEEN 2 AND 64),
    arguments_json TEXT NOT NULL CHECK(length(arguments_json) <= 8192),
    PRIMARY KEY(routine_id, step_index)
);
PRAGMA user_version = 1;
COMMIT;
"""


_SCHEMA_V2 = """
BEGIN;
CREATE TABLE IF NOT EXISTS watchers (
    id TEXT PRIMARY KEY,
    watcher_type TEXT NOT NULL CHECK(watcher_type IN (
        'window', 'process', 'file', 'resource', 'build', 'download')),
    status TEXT NOT NULL CHECK(status IN (
        'active', 'paused', 'completed', 'failed', 'cancelled', 'expired')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 96),
    target_json TEXT NOT NULL CHECK(length(target_json) BETWEEN 2 AND 8192),
    condition_json TEXT NOT NULL CHECK(length(condition_json) BETWEEN 2 AND 8192),
    interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 1 AND 3600),
    expires_at TEXT,
    one_shot INTEGER NOT NULL CHECK(one_shot IN (0, 1)),
    notification_level TEXT NOT NULL CHECK(notification_level IN ('normal', 'quiet')),
    last_observed_json TEXT CHECK(last_observed_json IS NULL OR
        length(last_observed_json) BETWEEN 2 AND 8192),
    last_event_type TEXT CHECK(last_event_type IS NULL OR length(last_event_type) BETWEEN 1 AND 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS watchers_status_idx ON watchers(status, updated_at);
CREATE TABLE IF NOT EXISTS watcher_events (
    id TEXT PRIMARY KEY,
    watcher_id TEXT NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(length(event_type) BETWEEN 2 AND 64),
    payload_json TEXT NOT NULL CHECK(length(payload_json) BETWEEN 2 AND 2048),
    notified INTEGER NOT NULL CHECK(notified IN (0, 1)),
    interpreted INTEGER NOT NULL CHECK(interpreted IN (0, 1))
);
CREATE INDEX IF NOT EXISTS watcher_events_time_idx ON watcher_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS watcher_events_watcher_idx
    ON watcher_events(watcher_id, occurred_at DESC);
PRAGMA user_version = 2;
COMMIT;
"""
