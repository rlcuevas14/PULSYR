"""Single source of truth for closed-domain enums.

Each tuple exactly mirrors the CHECK constraints in the database.
Changing a value here requires a migration — not a cosmetic edit.
"""

# --- items ---
ITEM_TYPES: tuple[str, ...] = (
    "bug", "feature", "tech-debt", "infra", "docs", "ops", "security", "product", "idea",
)
ITEM_STATUSES: tuple[str, ...] = (
    "idea", "backlog", "spec", "in-progress", "blocked", "in-review", "done", "discarded",
)
TERMINAL: tuple[str, ...] = ("done", "discarded")
OPEN_STATUSES: tuple[str, ...] = tuple(s for s in ITEM_STATUSES if s not in TERMINAL)
PRIORITIES: tuple[str, ...] = ("p0", "p1", "p2", "p3")
EFFORTS: tuple[str, ...] = ("XS", "S", "M", "L", "XL")
ORIGENES: tuple[str, ...] = ("digest", "human", "ai-session", "sentry", "agent")

# --- graph ---
RELATIONS: tuple[str, ...] = ("blocks", "requires", "conflicts", "related", "part_of")

# --- item comments ---
COMMENT_KINDS: tuple[str, ...] = ("comment", "ai-analysis", "decision", "status-change")

# --- threads ---
THREAD_STAGES: tuple[str, ...] = (
    "idea", "research", "stories", "spec", "in-development", "review", "done", "discarded",
)
THREAD_ARTIFACT_KINDS: tuple[str, ...] = (
    "research", "stories", "spec", "notes", "decision",
)

# --- list ordering (UI / MCP); not a CHECK but a closed domain ---
LIST_ORDERS: tuple[str, ...] = ("impact", "priority", "topological", "recent")

# --- jobs ---
AGENT_RUN_KINDS: tuple[str, ...] = (
    "enrich", "dedup", "triage-sentry", "digest-email", "fix-externo",
)
AGENT_RUN_STATUSES: tuple[str, ...] = ("pending", "running", "ok", "error")

# --- sentry ---
SENTRY_LEVELS: tuple[str, ...] = ("error", "warning", "info")
SENTRY_TRIAGE: tuple[str, ...] = ("pending", "real-bug", "bad-input", "3rd-party", "noise")
SENTRY_STATUSES: tuple[str, ...] = ("new", "linked", "resolved", "ignored")

# --- auth ---
USER_ROLES: tuple[str, ...] = ("admin", "viewer")
TOKEN_SCOPES: tuple[str, ...] = ("read", "write")
ACCOUNT_ROLES: tuple[str, ...] = ("owner", "member")
PROJECT_MEMBER_ROLES: tuple[str, ...] = ("viewer", "editor")

# --- management (PMO tab: documentos / plan / pendientes) ---
DELIVERABLE_TYPES: tuple[str, ...] = ("docx", "pdf", "html", "md", "xlsx", "pptx")
DELIVERABLE_STATUSES: tuple[str, ...] = ("draft", "review", "final", "archived")
PENDING_STATUSES: tuple[str, ...] = ("open", "doing", "blocked", "done")
MANAGEMENT_ENTITY_TYPES: tuple[str, ...] = ("compartment", "deliverable", "pending", "plan_task")

# Canonical MIME per deliverable type — we never trust the client's Content-Type,
# we derive it from the (whitelisted) extension / explicit doc_type. Reliability over trust.
DELIVERABLE_MIME: dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}
DELIVERABLE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — bytea ceiling; move to volume if this hurts backups.


def sql_list(values: tuple[str, ...] | list[str]) -> str:
    return ",".join(repr(v) for v in values)


def check_in(col: str, values: tuple[str, ...] | list[str]) -> str:
    return f"{col} IN ({sql_list(values)})"
