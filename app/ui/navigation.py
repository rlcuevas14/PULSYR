"""Single source of truth for Pulsyr product navigation.

Navigation is a presentation projection of domain capabilities.  This module is
deliberately pure: templates can render the same descriptor on desktop and mobile,
and the project-module service can filter it later without duplicating route rules.
"""

from dataclasses import dataclass
from typing import Collection

CORE_MODULE = "core"
OPTIONAL_MODULES = frozenset({"threads", "incidents", "management"})


@dataclass(frozen=True)
class NavigationEntry:
    key: str
    label_key: str
    href: str | None
    icon: str
    active_prefixes: tuple[str, ...]
    module: str = CORE_MODULE
    children: tuple["NavigationEntry", ...] = ()
    badge_key: str | None = None


@dataclass(frozen=True)
class NavigationContext:
    primary: tuple[NavigationEntry, ...]
    backlog_tabs: tuple[NavigationEntry, ...]
    active_key: str | None
    active_child_key: str | None


BACKLOG_TABS = (
    NavigationEntry("backlog_work", "nav.backlog_work", "/backlog", "list", ("/backlog",)),
    NavigationEntry("priority", "nav.priority", "/priority", "priority", ("/priority",)),
    NavigationEntry("archive", "nav.archive", "/archive", "archive", ("/archive",)),
)

MANAGEMENT_CHILDREN = (
    NavigationEntry(
        "management_pending",
        "management.subtab.pendientes",
        "/management/pendientes",
        "pending",
        ("/management/pendientes",),
        module="management",
    ),
    NavigationEntry(
        "management_plan",
        "management.subtab.plan",
        "/management/plan",
        "plan",
        ("/management/plan",),
        module="management",
    ),
    NavigationEntry(
        "management_documents",
        "management.subtab.documentos",
        "/management/documentos",
        "documents",
        ("/management/documentos",),
        module="management",
    ),
)

PRIMARY_NAVIGATION = (
    NavigationEntry(
        "backlog",
        "nav.backlog",
        "/backlog",
        "backlog",
        ("/backlog", "/priority", "/archive", "/items"),
        children=BACKLOG_TABS,
    ),
    NavigationEntry(
        "threads", "nav.threads", "/threads", "threads", ("/threads",), module="threads"
    ),
    NavigationEntry(
        "management",
        "nav.management",
        "/management",
        "management",
        ("/management",),
        module="management",
        children=MANAGEMENT_CHILDREN,
    ),
    NavigationEntry(
        "incidents",
        "nav.incidents",
        "/incidents",
        "incidents",
        ("/incidents",),
        module="incidents",
        badge_key="incidents_new",
    ),
)


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _active(entries: tuple[NavigationEntry, ...], path: str) -> str | None:
    for entry in entries:
        if any(_matches(path, prefix) for prefix in entry.active_prefixes):
            return entry.key
    return None


def navigation_context(
    path: str, enabled_modules: Collection[str] | None = None
) -> NavigationContext:
    """Build the effective navigation for ``path``.

    Until project capabilities land, omitting ``enabled_modules`` preserves the
    legacy surface by enabling every optional module.  Passing a collection is
    authoritative; ``core`` is always added and cannot be disabled.
    """
    modules = {CORE_MODULE, *OPTIONAL_MODULES} if enabled_modules is None else {
        CORE_MODULE,
        *enabled_modules,
    }
    primary = tuple(entry for entry in PRIMARY_NAVIGATION if entry.module in modules)
    return NavigationContext(
        primary=primary,
        backlog_tabs=BACKLOG_TABS,
        active_key=_active(primary, path),
        active_child_key=_active(BACKLOG_TABS + MANAGEMENT_CHILDREN, path),
    )
