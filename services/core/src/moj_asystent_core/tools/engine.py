"""Central typed registry, authorization gate and deterministic executor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from ..context import ActiveWindowSnapshot, DesktopContextSnapshot
from ..memory import (
    AliasLookupOutput,
    AliasOutput,
    CreateRoutineArguments,
    ForgetMemoryArguments,
    ListMemoriesArguments,
    MemoryDeleteOutput,
    MemoryListOutput,
    MemoryWriteOutput,
    RememberAliasArguments,
    RememberMemoryArguments,
    RememberPreferenceArguments,
    ResolveAliasArguments,
    RoutineWriteOutput,
    SQLiteMemoryStore,
)
from ..telemetry import TelemetrySnapshot
from ..vision import VisionCaptureOutcome, VisionImage, VisionInspectionResult
from .confirmations import ConfirmationManager, ConfirmationRejected, ConfirmationRequest
from .models import (
    ActionOutput,
    ConfirmationDecision,
    ContextProviderArguments,
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
    ToolExecutionResult,
    ToolStatus,
)
from .platform import PathPolicy, ToolPlatformError, WindowsToolPlatform
from .policy import PermissionDecision, PermissionPolicyStore, ToolPreference

InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class ToolEventSink(Protocol):
    def can_request_confirmation(self) -> bool: ...

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
    prepare: Callable[[BaseModel], BaseModel] = lambda value: value
    cancel: Callable[[], None] = lambda: None
    contextual_implementation: (
        Callable[[BaseModel, UUID], BaseModel | VisionCaptureOutcome] | None
    ) = None

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
        validated = definition.input_model.model_validate(arguments)
        return definition, definition.prepare(validated)


class ToolEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicyStore,
        confirmations: ConfirmationManager,
        events: ToolEventSink,
        cleanup: Callable[[], None] = lambda: None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.confirmations = confirmations
        self.events = events
        self._cleanup = cleanup
        self._images: dict[tuple[UUID, UUID], tuple[VisionImage, ...]] = {}

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
        except ToolPlatformError as error:
            result = ToolExecutionResult(
                tool_name=tool_name,
                status=ToolStatus.FAILURE,
                message=_safe_error_message(error),
            )
            self.events.publish_tool_result(operation_id, call_id, result)
            return result
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
            if not self.events.can_request_confirmation():
                return self._publish_result(
                    operation_id,
                    call_id,
                    ToolExecutionResult(
                        tool_name=definition.name,
                        status=ToolStatus.CANCELLED,
                        message="Brak aktywnego interfejsu do potwierdzenia działania.",
                    ),
                )
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
                try:
                    self.policy.set_preference(definition.name, ToolPreference.ALLOW)
                except OSError:
                    return self._publish_result(
                        operation_id,
                        call_id,
                        ToolExecutionResult(
                            tool_name=definition.name,
                            status=ToolStatus.FAILURE,
                            message="Nie udało się bezpiecznie zapisać zgody użytkownika.",
                        ),
                    )

        self.events.publish_tool_status(operation_id, call_id, definition.name, "executing")
        captured_image: VisionImage | None = None
        try:
            if definition.contextual_implementation is None:
                executed = await asyncio.wait_for(
                    asyncio.to_thread(definition.implementation, validated),
                    timeout=definition.timeout_seconds,
                )
            else:
                executed = await asyncio.wait_for(
                    asyncio.to_thread(
                        definition.contextual_implementation, validated, operation_id
                    ),
                    timeout=definition.timeout_seconds,
                )
            if isinstance(executed, VisionCaptureOutcome):
                output = executed.result
                if executed.preview is not None:
                    executed.preview[:] = b"\0" * len(executed.preview)
                    executed.preview.clear()
                    executed.preview = None
                if executed.image is not None:
                    captured_image = executed.image
            else:
                output = executed
            checked = definition.output_model.model_validate(output)
            if captured_image is not None:
                self._images[(operation_id, call_id)] = (captured_image,)
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.SUCCESS,
                message="Narzędzie zakończyło działanie pomyślnie.",
                output=cast(dict[str, JsonValue], checked.model_dump(mode="json")),
            )
        except TimeoutError:
            definition.cancel()
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.TIMEOUT,
                message="Narzędzie przekroczyło bezpieczny limit czasu.",
            )
        except asyncio.CancelledError:
            definition.cancel()
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.CANCELLED,
                message="Działanie anulowano.",
            )
            self.events.publish_tool_result(operation_id, call_id, result)
            raise
        except (ToolPlatformError, ValidationError) as error:
            if captured_image is not None:
                captured_image.clear()
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.FAILURE,
                message=_safe_error_message(error),
            )
        except OSError:
            if captured_image is not None:
                captured_image.clear()
            result = ToolExecutionResult(
                tool_name=definition.name,
                status=ToolStatus.FAILURE,
                message="System operacyjny odrzucił wykonanie narzędzia.",
            )
        except Exception:
            if captured_image is not None:
                captured_image.clear()
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
        self.release_operation(operation_id)

    def take_images(self, operation_id: UUID, call_id: UUID) -> tuple[VisionImage, ...]:
        return self._images.pop((operation_id, call_id), ())

    def release_operation(self, operation_id: UUID) -> None:
        keys = [key for key in self._images if key[0] == operation_id]
        for key in keys:
            for image in self._images.pop(key):
                image.clear()

    def cancel_all_confirmations(self) -> None:
        self.confirmations.cancel_all()

    def shutdown(self) -> None:
        self.confirmations.shutdown()
        for images in self._images.values():
            for image in images:
                image.clear()
        self._images.clear()
        self._cleanup()

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
    memory_store: SQLiteMemoryStore | None = None,
) -> ToolEngine:
    resolved_platform = platform or WindowsToolPlatform(path_policy=PathPolicy(path_roots))
    registry = ToolRegistry(_definitions(resolved_platform, memory_store))
    return ToolEngine(
        registry,
        PermissionPolicyStore(policy_path),
        ConfirmationManager(),
        events,
        resolved_platform.close,
    )


def _definitions(
    platform: WindowsToolPlatform, memory_store: SQLiteMemoryStore | None = None
) -> tuple[ToolDefinition, ...]:
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
        prepare: Callable[[BaseModel], BaseModel] = lambda value: value,
        cancel: Callable[[], None] = lambda: None,
        contextual: Callable[[BaseModel, UUID], BaseModel | VisionCaptureOutcome] | None = None,
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
            prepare=prepare,
            cancel=cancel,
            contextual_implementation=contextual,
        )

    def prepare_move(value: BaseModel) -> MoveFileArguments:
        arguments = cast(MoveFileArguments, value)
        return MoveFileArguments(
            source=str(platform.paths.existing_file(arguments.source)),
            destination=str(platform.paths.destination_file(arguments.destination)),
            overwrite=False,
        )

    def unavailable_vision_implementation(_: BaseModel) -> BaseModel:
        raise RuntimeError("Vision requires operation context")

    definitions = (
        definition(
            "get_system_stats",
            "Pobierz świeży, ograniczony raport CPU, RAM, dysków, sieci, GPU i procesów.",
            NoArguments,
            TelemetrySnapshot,
            PermissionLevel.READ,
            lambda _: platform.get_system_stats(UUID(int=0)),
            category="system.read",
            action="Odczytać statystyki systemu?",
            timeout=8,
            contextual=lambda _, operation_id: platform.get_system_stats(operation_id),
            cancel=platform.cancel_context,
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
            ActiveWindowSnapshot,
            PermissionLevel.READ,
            lambda _: platform.get_active_window(),
            category="window.read",
            action="Sprawdzić aktywne okno?",
        ),
        definition(
            "read_ui_tree",
            "Odczytaj ograniczony kontekst i drzewo dostępności aktywnego okna. "
            "Używaj tylko, gdy pytanie dotyczy bieżącego interfejsu, zaznaczenia "
            "albo widocznego tekstu.",
            ContextProviderArguments,
            DesktopContextSnapshot,
            PermissionLevel.READ,
            lambda value: platform.read_ui_tree(cast(ContextProviderArguments, value)),
            timeout=3,
            category="context.read",
            action="Odczytać drzewo UI?",
            cancel=platform.cancel_context,
        ),
        definition(
            "inspect_screen",
            "Przechwyć ograniczony obraz aktywnego okna do lokalnej analizy wizualnej. "
            "Używaj tylko na wyraźne pytanie o wygląd, układ, kolor, ikonę lub wykres, "
            "albo gdy wcześniej odczytane dane UI są niewystarczające.",
            ContextProviderArguments,
            VisionInspectionResult,
            PermissionLevel.READ,
            unavailable_vision_implementation,
            timeout=5,
            category="context.read",
            action="Sprawdzić ekran?",
            cancel=platform.cancel_context,
            contextual=lambda value, operation_id: platform.inspect_screen(
                cast(ContextProviderArguments, value), operation_id
            ),
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
            prepare=lambda value: PathArguments(
                path=str(platform.paths.existing_directory(cast(PathArguments, value).path))
            ),
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
            prepare=prepare_move,
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
            prepare=lambda value: DeleteFileArguments(
                path=str(platform.paths.existing_file(cast(DeleteFileArguments, value).path))
            ),
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
    if memory_store is None:
        return definitions

    def resolve_alias_output(value: BaseModel) -> AliasLookupOutput:
        arguments = cast(ResolveAliasArguments, value)
        alias = memory_store.resolve_alias(arguments.alias, kind=arguments.kind)
        return AliasLookupOutput(found=alias is not None, alias=alias)

    def create_routine_output(value: BaseModel) -> RoutineWriteOutput:
        arguments = cast(CreateRoutineArguments, value)
        known = {item.name: item for item in definitions}
        for step in arguments.steps:
            definition_item = known.get(step.tool_name)
            if definition_item is None:
                raise ValueError("Routine contains an unknown tool")
            definition_item.input_model.model_validate(step.arguments)
        return RoutineWriteOutput(
            routine=memory_store.create_routine(
                arguments.name, arguments.steps, description=arguments.description
            )
        )

    return definitions + (
        definition(
            "remember_preference",
            "Zapisz wyraźnie zaakceptowaną preferencję użytkownika. Wymaga jawnej prośby.",
            RememberPreferenceArguments,
            MemoryWriteOutput,
            PermissionLevel.SENSITIVE,
            lambda value: MemoryWriteOutput(
                memory=memory_store.remember_preference(
                    cast(RememberPreferenceArguments, value).key,
                    cast(RememberPreferenceArguments, value).value,
                )
            ),
            category="memory.sensitive",
            action="Zapisać tę preferencję?",
            target=lambda value: cast(RememberPreferenceArguments, value).key,
            risk="Preferencja zostanie zapisana lokalnie i będzie używana w przyszłych rozmowach.",
        ),
        definition(
            "create_routine",
            "Zaproponuj i zapisz rutynę jako krótką, uporządkowaną listę istniejących narzędzi. "
            "Wymaga jawnej prośby i potwierdzenia użytkownika.",
            CreateRoutineArguments,
            RoutineWriteOutput,
            PermissionLevel.SENSITIVE,
            create_routine_output,
            category="routine.sensitive",
            action="Zapisać tę rutynę?",
            target=lambda value: cast(CreateRoutineArguments, value).name,
            risk="Rutyna zostanie zapisana lokalnie i zachowa osobne uprawnienia każdego kroku.",
        ),
        definition(
            "remember_memory",
            "Zapisz wyraźnie zaakceptowaną informację użytkownika. Wymaga jawnej prośby.",
            RememberMemoryArguments,
            MemoryWriteOutput,
            PermissionLevel.SENSITIVE,
            lambda value: MemoryWriteOutput(
                memory=memory_store.create_memory(
                    cast(RememberMemoryArguments, value).key,
                    cast(RememberMemoryArguments, value).value,
                )
            ),
            category="memory.sensitive",
            action="Zapisać tę informację?",
            target=lambda value: cast(RememberMemoryArguments, value).key,
            risk="Informacja zostanie zapisana lokalnie i będzie używana w przyszłych rozmowach.",
        ),
        definition(
            "remember_alias",
            "Zapisz lokalny alias aplikacji albo projektu po jawnej prośbie użytkownika.",
            RememberAliasArguments,
            AliasOutput,
            PermissionLevel.SENSITIVE,
            lambda value: AliasOutput(
                alias=memory_store.save_alias(
                    cast(RememberAliasArguments, value).kind,
                    cast(RememberAliasArguments, value).alias,
                    cast(RememberAliasArguments, value).target,
                    overwrite=cast(RememberAliasArguments, value).overwrite,
                )
            ),
            category="memory.sensitive",
            action="Zapisać ten alias?",
            target=lambda value: cast(RememberAliasArguments, value).alias,
            risk=(
                "Alias zostanie zapisany lokalnie; istniejący alias nie zostanie "
                "zastąpiony bez wyraźnego overwrite."
            ),
        ),
        definition(
            "list_memories",
            "Pokaż ograniczoną listę zapisanych, zatwierdzonych informacji użytkownika.",
            ListMemoriesArguments,
            MemoryListOutput,
            PermissionLevel.READ,
            lambda value: MemoryListOutput(
                memories=memory_store.list_memories(limit=cast(ListMemoriesArguments, value).limit)
            ),
            category="memory.read",
            action="Wyświetlić zapamiętane informacje?",
        ),
        definition(
            "forget_memory",
            "Usuń jedną zapisaną informację po wskazaniu jej identyfikatora.",
            ForgetMemoryArguments,
            MemoryDeleteOutput,
            PermissionLevel.SENSITIVE,
            lambda value: MemoryDeleteOutput(
                removed=memory_store.delete_memory(cast(ForgetMemoryArguments, value).memory_id)
            ),
            category="memory.sensitive",
            action="Usunąć tę zapamiętaną informację?",
            target=lambda value: str(cast(ForgetMemoryArguments, value).memory_id),
            risk="Ta zapamiętana informacja zostanie trwale usunięta.",
        ),
        definition(
            "resolve_alias",
            "Rozwiąż zapisany alias aplikacji lub projektu bez wykonywania żadnej akcji.",
            ResolveAliasArguments,
            AliasLookupOutput,
            PermissionLevel.READ,
            resolve_alias_output,
            category="memory.read",
            action="Odczytać alias?",
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
        and value.isascii()
        and value[0].isalpha()
        and all(
            character.islower() or character.isdigit() or character == "_" for character in value
        )
    )
