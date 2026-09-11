"""Central typed registry, authorization gate and deterministic executor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .confirmations import ConfirmationManager, ConfirmationRejected, ConfirmationRequest
from .models import (
    ActionOutput,
    ActiveWindowOutput,
    ConfirmationDecision,
    ContextProviderArguments,
    ContextUnavailableOutput,
    DeleteFileArguments,
    FindProcessByPortArguments,
    FindProcessByPortOutput,
    FocusWindowArguments,
    JsonValue,
    ListDirectoryArguments,
    ListDirectoryOutput,
    ModelToolDefinition,
    MoveFileArguments,
    NoArguments,
    OpenApplicationArguments,
    PathArguments,
    PermissionLevel,
    PowerActionArguments,
    PowerActionOutput,
    ReadFileArguments,
    ReadFileOutput,
    RestartProcessArguments,
    RunningProcessesArguments,
    RunningProcessesOutput,
    SetApplicationVolumeArguments,
    SetVolumeArguments,
    SystemStatsOutput,
    ToolExecutionResult,
    ToolStatus,
)
from .platform import PathPolicy, ToolPlatformError, WindowsToolPlatform
from .policy import PermissionDecision, PermissionPolicyStore, ToolPreference

InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class ToolEventSink(Protocol):
    def publish_tool_status(
        self, operation_id: UUID, call_id: UUID, tool_name: str, status: str
    ) -> None: ...

    def publish_confirmation_requested(self, request: ConfirmationRequest) -> None: ...

    def publish_confirmation_resolved(
        self, request: ConfirmationRequest, decision: str
    ) -> None: ...

    def publish_tool_result(
        self, operation_id: UUID, call_id: UUID, result: ToolExecutionResult
    ) -> None: ...


@dataclass(frozen=True)
class ConfirmationPresentation:
    action: str
    target: str
    details: tuple[tuple[str, str], ...]
    risk: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission: PermissionLevel
    implementation: Callable[[BaseModel], BaseModel]
    timeout_seconds: float
    cancellation: str
    persistent_approval: bool
    audit_category: str
    confirmation: Callable[[BaseModel], ConfirmationPresentation]

    def model_definition(self) -> ModelToolDefinition:
        parameters = cast(dict[str, JsonValue], self.input_model.model_json_schema())
        return ModelToolDefinition(
            function={
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            }
        )


class ToolRegistry:
    def __init__(self, definitions: tuple[ToolDefinition, ...]) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name in self._definitions:
                raise ValueError(f"Duplicate tool name: {definition.name}")
            if (
                definition.permission is PermissionLevel.SENSITIVE
                and definition.persistent_approval
            ):
                raise ValueError("Sensitive tools cannot permit persistent approval")
            self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def model_definitions(self) -> tuple[ModelToolDefinition, ...]:
        return tuple(definition.model_definition() for definition in self._definitions.values())

    def validate(
        self, name: str, arguments: dict[str, JsonValue]
    ) -> tuple[ToolDefinition, BaseModel]:
        definition = self.get(name)
        if definition is None:
            raise ValueError("Unknown tool")
        return definition, definition.input_model.model_validate(arguments)


class ToolEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicyStore,
        confirmations: ConfirmationManager,
        events: ToolEventSink,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.confirmations = confirmations
        self.events = events

    async def execute(
        self,
        *,
        operation_id: UUID,
        call_id: UUID,
        tool_name: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        if _valid_tool_name(tool_name):
            self.events.publish_tool_status(operation_id, call_id, tool_name, "requested")
        try:
            definition, validated = self.registry.validate(tool_name, arguments)
        except (ValueError, ValidationError):
            result = ToolExecutionResult(
                tool_name=tool_name if _valid_tool_name(tool_name) else "invalid_tool",
                status=ToolStatus.FAILURE,
                message="Narzędzie lub jego argumenty są nieprawidłowe.",
            )
            self.events.publish_tool_result(operation_id, call_id, result)
            return result

        permission = self.policy.evaluate(definition.name, definition.permission)
        if permission is PermissionDecision.DENY:
            return self._publish_result(
                operation_id,
                call_id,
                ToolExecutionResult(
                    tool_name=definition.name,
                    status=ToolStatus.DENIED,
                    message="Polityka użytkownika nie zezwala na to narzędzie.",
                ),
            )

        if permission is PermissionDecision.CONFIRM:
            presentation = definition.confirmation(validated)
            normalized = cast(dict[str, JsonValue], validated.model_dump(mode="json"))
            request = self.confirmations.create(
                operation_id=operation_id,
                call_id=call_id,
                tool_name=definition.name,
                arguments=normalized,
                action=presentation.action,
                target=presentation.target,
                details=presentation.details,
                risk=presentation.risk,
                persistent_allowed=definition.persistent_approval,
            )
            self.events.publish_tool_status(
                operation_id, call_id, definition.name, ToolStatus.CONFIRMATION_REQUIRED.value
            )
            self.events.publish_confirmation_requested(request)
            try:
                decision = await self.confirmations.wait(request)
            except (ConfirmationRejected, asyncio.CancelledError):
                result = ToolExecutionResult(
                    tool_name=definition.name,
                    status=ToolStatus.CANCELLED,
                    message="Działanie anulowano albo potwierdzenie wygasło.",
                )
                self.events.publish_tool_result(operation_id, call_id, result)
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                return result
            if decision == "cancel":
                return self._publish_result(
                    operation_id,
                    call_id,
                    ToolExecutionResult(
                        tool_name=definition.name,
                        status=ToolStatus.CANCELLED,
                        message="Użytkownik anulował działanie.",
                    ),
                )
            if decision == "always_allow":
                self.policy.set_preference(definition.name, ToolPreference.ALLOW)

        self.events.publish_tool_status(operation_id, call_id, definition.name, "executing")
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(definition.implementation, validated),
                timeout=definition.timeout_seconds,
            )
            checked = definition.output_model.model_validate(output)
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.SUCCESS,
                message="Narzędzie zakończyło działanie pomyślnie.",
                output=cast(dict[str, JsonValue], checked.model_dump(mode="json")),
            )
        except TimeoutError:
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.TIMEOUT,
                message="Narzędzie przekroczyło bezpieczny limit czasu.",
            )
        except asyncio.CancelledError:
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.CANCELLED,
                message="Działanie anulowano.",
            )
            self.events.publish_tool_result(operation_id, call_id, result)
            raise
        except (ToolPlatformError, ValidationError) as error:
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.FAILURE,
                message=_safe_error_message(error),
            )
        except OSError:
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.FAILURE,
                message="System operacyjny odrzucił wykonanie narzędzia.",
            )
        except Exception:
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.FAILURE,
                message="Narzędzie nie ukończyło działania.",
            )
        return self._publish_result(operation_id, call_id, result)

    def resolve_confirmation(self, decision: ConfirmationDecision) -> ConfirmationRequest:
        request = self.confirmations.resolve(decision)
        self.events.publish_confirmation_resolved(request, decision.decision)
        return request

    def cancel_operation(self, operation_id: UUID) -> None:
        self.confirmations.cancel_operation(operation_id)

    def shutdown(self) -> None:
        self.confirmations.shutdown()

    def _publish_result(
        self, operation_id: UUID, call_id: UUID, result: ToolExecutionResult
    ) -> ToolExecutionResult:
        self.events.publish_tool_result(operation_id, call_id, result)
        return result


def build_tool_engine(
    events: ToolEventSink,
    *,
    policy_path: Path | None = None,
    path_roots: tuple[Path, ...] | None = None,
    platform: WindowsToolPlatform | None = None,
) -> ToolEngine:
    resolved_platform = platform or WindowsToolPlatform(path_policy=PathPolicy(path_roots))
    registry = ToolRegistry(_definitions(resolved_platform))
    return ToolEngine(
        registry,
        PermissionPolicyStore(policy_path),
        ConfirmationManager(),
        events,
    )


def _definitions(platform: WindowsToolPlatform) -> tuple[ToolDefinition, ...]:
    def definition(
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        permission: PermissionLevel,
        implementation: Callable[[BaseModel], BaseModel],
        *,
        timeout: float = 5,
        persistent: bool = False,
        category: str,
        action: str,
        target: Callable[[BaseModel], str] = lambda _: "system",
        details: Callable[[BaseModel], tuple[tuple[str, str], ...]] = lambda _: (),
        risk: str = "Działanie zmieni stan komputera.",
    ) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
            input_model=input_model,
            output_model=output_model,
            permission=permission,
            implementation=implementation,
            timeout_seconds=timeout,
            cancellation="cooperative_before_effect",
            persistent_approval=persistent,
            audit_category=category,
            confirmation=lambda args: ConfirmationPresentation(
                action=action,
                target=target(args),
                details=details(args),
                risk=risk,
            ),
        )

    return (
        definition(
            "get_system_stats",
            "Pobierz bieżące użycie CPU, pamięci i dysku.",
            NoArguments,
            SystemStatsOutput,
            PermissionLevel.READ,
            lambda _: platform.get_system_stats(),
            category="system.read",
            action="Odczytać statystyki systemu?",
        ),
        definition(
            "get_running_processes",
            "Pobierz ograniczoną listę uruchomionych procesów.",
            RunningProcessesArguments,
            RunningProcessesOutput,
            PermissionLevel.READ,
            lambda value: platform.get_running_processes(cast(RunningProcessesArguments, value)),
            category="process.read",
            action="Odczytać listę procesów?",
        ),
        definition(
            "get_active_window",
            "Pobierz podstawową tożsamość aktywnego okna bez drzewa UI.",
            NoArguments,
            ActiveWindowOutput,
            PermissionLevel.READ,
            lambda _: platform.get_active_window(),
            category="window.read",
            action="Sprawdzić aktywne okno?",
        ),
        definition(
            "read_ui_tree",
            "Odczytaj drzewo dostępności aktywnego okna, gdy dostawca Milestone 7 jest dostępny.",
            ContextProviderArguments,
            ContextUnavailableOutput,
            PermissionLevel.READ,
            lambda _: platform.unavailable_context("Drzewo UI"),
            category="context.read",
            action="Odczytać drzewo UI?",
        ),
        definition(
            "inspect_screen",
            "Sprawdź ekran, gdy jawny dostawca obrazu Milestone 7 jest dostępny.",
            ContextProviderArguments,
            ContextUnavailableOutput,
            PermissionLevel.READ,
            lambda _: platform.unavailable_context("Inspekcja ekranu"),
            category="context.read",
            action="Sprawdzić ekran?",
        ),
        definition(
            "list_directory",
            "Wyświetl ograniczoną listę elementów dozwolonego folderu.",
            ListDirectoryArguments,
            ListDirectoryOutput,
            PermissionLevel.READ,
            lambda value: platform.list_directory(cast(ListDirectoryArguments, value)),
            category="filesystem.read",
            action="Wyświetlić folder?",
            target=lambda value: cast(ListDirectoryArguments, value).path,
        ),
        definition(
            "read_file",
            "Odczytaj mały plik tekstowy UTF-8 z dozwolonego obszaru.",
            ReadFileArguments,
            ReadFileOutput,
            PermissionLevel.READ,
            lambda value: platform.read_file(cast(ReadFileArguments, value)),
            category="filesystem.read",
            action="Odczytać plik?",
            target=lambda value: cast(ReadFileArguments, value).path,
        ),
        definition(
            "find_process_using_port",
            "Znajdź proces nasłuchujący na konkretnym porcie TCP lub UDP.",
            FindProcessByPortArguments,
            FindProcessByPortOutput,
            PermissionLevel.READ,
            lambda value: platform.find_process_using_port(cast(FindProcessByPortArguments, value)),
            category="process.read",
            action="Sprawdzić port?",
        ),
        definition(
            "open_application",
            "Otwórz aplikację z zamkniętego rejestru aplikacji.",
            OpenApplicationArguments,
            ActionOutput,
            PermissionLevel.WRITE_SAFE,
            lambda value: platform.open_application(cast(OpenApplicationArguments, value)),
            persistent=True,
            category="application.write",
            action="Otworzyć aplikację?",
            target=lambda value: cast(OpenApplicationArguments, value).application_id,
            risk="Aplikacja zostanie uruchomiona na tym komputerze.",
        ),
        definition(
            "focus_window",
            "Przenieś fokus na dokładnie wskazane istniejące okno.",
            FocusWindowArguments,
            ActionOutput,
            PermissionLevel.WRITE_SAFE,
            lambda value: platform.focus_window(cast(FocusWindowArguments, value)),
            persistent=True,
            category="window.write",
            action="Przełączyć aktywne okno?",
            target=lambda value: f"Okno {cast(FocusWindowArguments, value).handle}",
        ),
        definition(
            "open_folder",
            "Otwórz istniejący dozwolony folder w Eksploratorze.",
            PathArguments,
            ActionOutput,
            PermissionLevel.WRITE_SAFE,
            lambda value: platform.open_folder(cast(PathArguments, value)),
            persistent=True,
            category="filesystem.open",
            action="Otworzyć folder?",
            target=lambda value: cast(PathArguments, value).path,
        ),
        definition(
            "set_volume",
            "Ustaw głośność systemową w zakresie od 0 do 100.",
            SetVolumeArguments,
            ActionOutput,
            PermissionLevel.WRITE_SAFE,
            lambda value: platform.set_volume(cast(SetVolumeArguments, value)),
            persistent=True,
            category="audio.write",
            action="Zmienić głośność systemową?",
            target=lambda value: f"{cast(SetVolumeArguments, value).volume}%",
        ),
        definition(
            "set_application_volume",
            "Ustaw głośność dokładnie zidentyfikowanej sesji aplikacji.",
            SetApplicationVolumeArguments,
            ActionOutput,
            PermissionLevel.WRITE_SAFE,
            lambda value: platform.set_application_volume(
                cast(SetApplicationVolumeArguments, value)
            ),
            persistent=True,
            category="audio.write",
            action="Zmienić głośność aplikacji?",
            target=lambda value: f"PID {cast(SetApplicationVolumeArguments, value).pid}",
        ),
        definition(
            "restart_approved_process",
            "Uruchom ponownie proces z jawnej listy zatwierdzonych plików wykonywalnych.",
            RestartProcessArguments,
            ActionOutput,
            PermissionLevel.SENSITIVE,
            lambda value: platform.restart_approved_process(cast(RestartProcessArguments, value)),
            timeout=12,
            category="process.sensitive",
            action="Uruchomić proces ponownie?",
            target=_restart_process_target,
            risk=(
                "Proces zostanie zakończony i uruchomiony ponownie; "
                "niezapisana praca może zostać utracona."
            ),
        ),
        definition(
            "move_file",
            "Przenieś jeden zwykły plik bez nadpisywania celu.",
            MoveFileArguments,
            ActionOutput,
            PermissionLevel.SENSITIVE,
            lambda value: platform.move_file(cast(MoveFileArguments, value)),
            category="filesystem.sensitive",
            action="Przenieść plik?",
            target=lambda value: cast(MoveFileArguments, value).source,
            details=lambda value: (("Do", cast(MoveFileArguments, value).destination),),
            risk="Plik zmieni położenie. Istniejący cel nie zostanie nadpisany.",
        ),
        definition(
            "delete_file",
            "Usuń jeden zwykły plik; usuwanie folderów i rekursja są niedozwolone.",
            DeleteFileArguments,
            ActionOutput,
            PermissionLevel.SENSITIVE,
            lambda value: platform.delete_file(cast(DeleteFileArguments, value)),
            category="filesystem.sensitive",
            action="Usunąć plik?",
            target=lambda value: cast(DeleteFileArguments, value).path,
            risk="Plik zostanie trwale usunięty i może nie być możliwy do odzyskania.",
        ),
        definition(
            "restart_pc",
            "Uruchom ponownie komputer po każdorazowym potwierdzeniu.",
            PowerActionArguments,
            PowerActionOutput,
            PermissionLevel.SENSITIVE,
            lambda value: platform.power_action(cast(PowerActionArguments, value), restart=True),
            category="power.sensitive",
            action="Uruchomić ponownie komputer?",
            target=lambda _: "Windows",
            risk="Wszystkie aplikacje zostaną zamknięte; niezapisana praca może zostać utracona.",
        ),
        definition(
            "shutdown_pc",
            "Wyłącz komputer po każdorazowym potwierdzeniu.",
            PowerActionArguments,
            PowerActionOutput,
            PermissionLevel.SENSITIVE,
            lambda value: platform.power_action(cast(PowerActionArguments, value), restart=False),
            category="power.sensitive",
            action="Wyłączyć komputer?",
            target=lambda _: "Windows",
            risk="Wszystkie aplikacje zostaną zamknięte; niezapisana praca może zostać utracona.",
        ),
    )


def _safe_error_message(error: Exception) -> str:
    message = str(error).strip()
    return message[:512] if message else "Narzędzie nie ukończyło działania."


def _restart_process_target(value: BaseModel) -> str:
    arguments = cast(RestartProcessArguments, value)
    return f"{arguments.executable_name} (PID {arguments.pid})"


def _valid_tool_name(value: str) -> bool:
    return (
        2 <= len(value) <= 64
        and value[0].isalpha()
        and all(
            character.islower() or character.isdigit() or character == "_" for character in value
        )
    )
