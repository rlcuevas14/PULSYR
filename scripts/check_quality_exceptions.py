#!/usr/bin/env python3
"""Validate that quality-gate exceptions are explicit, owned and temporary."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

KNOWN_GATES = {
    "seo-crawl",
    "html-validation",
    "browser-axe",
    "lighthouse",
    "delivery",
    "links",
}


def validate(path: Path, today: dt.date) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read exception registry: {exc}"]
    exceptions = payload.get("exceptions") if isinstance(payload, dict) else None
    if not isinstance(exceptions, list):
        return ["top-level exceptions must be a list"]
    seen: set[str] = set()
    for index, item in enumerate(exceptions):
        prefix = f"exceptions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        required = {"id", "gate", "owner", "approved_by", "reason", "tracking", "expires"}
        missing = sorted(required - item.keys())
        if missing:
            errors.append(f"{prefix}: missing {missing}")
            continue
        exception_id = str(item["id"]).strip()
        if not exception_id or exception_id in seen:
            errors.append(f"{prefix}: id must be unique and non-empty")
        seen.add(exception_id)
        if item["gate"] not in KNOWN_GATES:
            errors.append(f"{prefix}: unknown gate {item['gate']!r}")
        text_fields = ("owner", "approved_by", "reason", "tracking")
        if any(not str(item[field]).strip() for field in text_fields):
            errors.append(f"{prefix}: owner, approver, reason and tracking must be non-empty")
        try:
            expires = dt.date.fromisoformat(str(item["expires"]))
        except ValueError:
            errors.append(f"{prefix}: expires must be YYYY-MM-DD")
            continue
        if expires < today:
            errors.append(f"{prefix}: expired on {expires.isoformat()}")
        if expires > today + dt.timedelta(days=30):
            errors.append(f"{prefix}: exception lifetime cannot exceed 30 days")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()
    errors = validate(args.registry, args.today)
    if errors:
        print("Quality-gate exception registry failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Quality-gate exception registry is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
