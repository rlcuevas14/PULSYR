"""Authoritative, project-scoped capability configuration.

The configured state is deliberately separate from plan entitlements. All current
plans are entitled to every optional module, but every consumer goes through this
service so a future commercial rule cannot reinterpret stored configuration.
"""

import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import ProjectModule, ProjectModuleEvent

CORE_MODULE = "core"
OPTIONAL_MODULES = ("threads", "incidents", "management")
MODULES = (CORE_MODULE, *OPTIONAL_MODULES)
MODULE_SOURCES = ("onboarding", "preset", "manual", "migration")

PRESET_MODULES: dict[str, frozenset[str]] = {
    "solo": frozenset(),
    "product": frozenset({"threads", "incidents"}),
    "client": frozenset({"management"}),
    "hybrid": frozenset(OPTIONAL_MODULES),
}
PRESETS = tuple(PRESET_MODULES)


class ModuleConfigurationError(RuntimeError):
    """The persisted three-row invariant is missing or inconsistent."""


class ModuleDisabled(PermissionError):
    def __init__(self, project_id: uuid.UUID, module: str, *, ui: bool = False) -> None:
        self.project_id = project_id
        self.module = module
        self.ui = ui
        super().__init__(f"Module '{module}' is disabled for project {project_id}")


def _project_id(project_id: uuid.UUID) -> uuid.UUID:
    if not isinstance(project_id, uuid.UUID):
        raise ValueError("project_id must be a UUID")
    return project_id


def _optional_module(module: str) -> str:
    if module not in OPTIONAL_MODULES:
        raise ValueError(f"Unknown optional module: {module}")
    return module


def _preset(preset: str) -> str:
    if preset not in PRESET_MODULES:
        raise ValueError(f"Unknown project preset: {preset}")
    return preset


def _source(source: str) -> str:
    if source not in MODULE_SOURCES:
        raise ValueError(f"Unknown module change source: {source}")
    return source


def _actor(actor: str) -> str:
    normalized = actor.strip()[:255]
    if not normalized:
        raise ValueError("Module change actor cannot be empty")
    return normalized


def states_for_preset(preset: str) -> dict[str, bool]:
    enabled = PRESET_MODULES[_preset(preset)]
    return {module: module in enabled for module in OPTIONAL_MODULES}


def _states_from_rows(project_id: uuid.UUID, rows: list[ProjectModule]) -> dict[str, bool]:
    states = {row.module: row.enabled for row in rows}
    if len(rows) != len(OPTIONAL_MODULES) or set(states) != set(OPTIONAL_MODULES):
        raise ModuleConfigurationError(
            f"Project {project_id} must have exactly one row for each optional module"
        )
    return {module: states[module] for module in OPTIONAL_MODULES}


async def module_states(db: AsyncSession, project_id: uuid.UUID) -> dict[str, bool]:
    pid = _project_id(project_id)
    rows = list((await db.scalars(
        select(ProjectModule)
        .where(ProjectModule.project_id == pid)
        .order_by(ProjectModule.module)
    )).all())
    return _states_from_rows(pid, rows)


def entitled_modules(_plan_code: str | None = None) -> frozenset[str]:
    """All current plans are entitled; keep this boundary explicit for future policy."""
    return frozenset(OPTIONAL_MODULES)


async def enabled_modules(
    db: AsyncSession, project_id: uuid.UUID, *, plan_code: str | None = None
) -> frozenset[str]:
    states = await module_states(db, project_id)
    return effective_modules(states, plan_code=plan_code)


def effective_modules(
    states: Mapping[str, bool], *, plan_code: str | None = None
) -> frozenset[str]:
    entitled = entitled_modules(plan_code)
    return frozenset(
        {CORE_MODULE}
        | {
            module
            for module in OPTIONAL_MODULES
            if bool(states.get(module)) and module in entitled
        }
    )


async def is_module_enabled(db: AsyncSession, project_id: uuid.UUID, module: str) -> bool:
    if module == CORE_MODULE:
        _project_id(project_id)
        return True
    selected = _optional_module(module)
    return selected in await enabled_modules(db, project_id)


async def require_module(
    db: AsyncSession, project_id: uuid.UUID, module: str, *, ui: bool = False
) -> None:
    if not await is_module_enabled(db, project_id, module):
        raise ModuleDisabled(project_id, module, ui=ui)


async def initialize_modules(
    db: AsyncSession,
    project_id: uuid.UUID,
    preset: str,
    actor: str,
) -> dict[str, bool]:
    pid = _project_id(project_id)
    desired = states_for_preset(preset)
    _actor(actor)
    existing = list((await db.scalars(
        select(ProjectModule).where(ProjectModule.project_id == pid)
    )).all())
    if existing:
        return _states_from_rows(pid, existing)
    db.add_all([
        ProjectModule(project_id=pid, module=module, enabled=desired[module])
        for module in OPTIONAL_MODULES
    ])
    await db.flush()
    return desired


async def set_module_enabled(
    db: AsyncSession,
    project_id: uuid.UUID,
    module: str,
    enabled: bool,
    actor: str,
    source: str = "manual",
) -> bool:
    pid = _project_id(project_id)
    selected = _optional_module(module)
    normalized_actor = _actor(actor)
    normalized_source = _source(source)
    await module_states(db, pid)
    row = await db.scalar(
        select(ProjectModule)
        .where(ProjectModule.project_id == pid, ProjectModule.module == selected)
        .with_for_update()
    )
    if row is None:  # Defensive against an unsupported concurrent manual delete.
        raise ModuleConfigurationError(f"Missing module '{selected}' for project {pid}")
    desired = bool(enabled)
    if row.enabled == desired:
        return False
    previous = row.enabled
    row.enabled = desired
    db.add(ProjectModuleEvent(
        project_id=pid,
        module=selected,
        actor=normalized_actor,
        previous_enabled=previous,
        enabled=desired,
        source=normalized_source,
    ))
    await db.flush()
    return True


async def apply_preset(
    db: AsyncSession,
    project_id: uuid.UUID,
    preset: str,
    actor: str,
) -> dict[str, bool]:
    pid = _project_id(project_id)
    desired = states_for_preset(preset)
    normalized_actor = _actor(actor)
    rows = list((await db.scalars(
        select(ProjectModule)
        .where(ProjectModule.project_id == pid)
        .order_by(ProjectModule.module)
        .with_for_update()
    )).all())
    _states_from_rows(pid, rows)
    for row in rows:
        target = desired[row.module]
        if row.enabled == target:
            continue
        previous = row.enabled
        row.enabled = target
        db.add(ProjectModuleEvent(
            project_id=pid,
            module=row.module,
            actor=normalized_actor,
            previous_enabled=previous,
            enabled=target,
            source="preset",
        ))
    await db.flush()
    return desired


def infer_preset(states: Mapping[str, bool]) -> str:
    configured = {module: bool(states.get(module, False)) for module in OPTIONAL_MODULES}
    for preset, enabled in PRESET_MODULES.items():
        if {module for module, value in configured.items() if value} == set(enabled):
            return preset
    return "custom"
