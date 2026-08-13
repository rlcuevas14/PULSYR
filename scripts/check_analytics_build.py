"""Validate that analytics is absent by default and singular in production builds."""

import argparse
from pathlib import Path

SNIPPET = "https://plausible.io/js/script.js"
BOOTSTRAP = 'src="/analytics.js"'
REQUIRED_EVENTS = {"cta_signup", "cta_github", "cta_docs", "quick_start_complete", "contact"}


def validate(root: Path, enabled: bool) -> None:
    pages = list(root.rglob("*.html"))
    if not pages:
        raise SystemExit("no generated HTML found")
    joined = "\n".join(page.read_text(encoding="utf-8") for page in pages)
    for page in pages:
        source = page.read_text(encoding="utf-8")
        count = source.count(SNIPPET)
        bootstrap_count = source.count(BOOTSTRAP)
        expected = 1 if enabled else 0
        if count != expected:
            raise SystemExit(f"{page}: expected {expected} analytics snippet, found {count}")
        if bootstrap_count != expected:
            raise SystemExit(
                f"{page}: expected {expected} self-hosted analytics bootstrap, found {bootstrap_count}"
            )
    if enabled:
        missing = {event for event in REQUIRED_EVENTS if f'data-analytics-event="{event}"' not in joined}
        if missing:
            raise SystemExit(f"missing analytics events: {sorted(missing)}")
    print(f"Analytics build contract passed ({'enabled' if enabled else 'disabled'}).")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--enabled", action="store_true")
    args = parser.parse_args()
    validate(args.root, args.enabled)


if __name__ == "__main__":
    main()
