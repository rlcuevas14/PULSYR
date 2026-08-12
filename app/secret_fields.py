"""Write-only form semantics for secrets stored by the application."""


def resolve_write_only_secret(
    current: str | None,
    submitted: str | None,
    *,
    clear: bool = False,
) -> str | None:
    """Delete explicitly, replace with a nonblank value, otherwise preserve."""
    if clear:
        return None
    replacement = (submitted or "").strip()
    return replacement or current
