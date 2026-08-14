"""Write a deterministic JSON inventory for security review and CI diffs."""

import argparse
import json
from pathlib import Path

from app.main import create_app
from app.route_inventory import route_inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    rows = route_inventory(create_app())
    rendered = json.dumps(rows, indent=2) + "\n"
    if args.check:
        current = args.check.read_text(encoding="utf-8")
        if current != rendered:
            print(f"route inventory is stale: regenerate {args.check}")
            return 1
        print(f"verified {len(rows)} operations in {args.check}")
        return 0
    if args.output is None:
        parser.error("output is required unless --check is used")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {len(rows)} operations to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
