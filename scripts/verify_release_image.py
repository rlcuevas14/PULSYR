"""Reject mutable or malformed container references before staging/promotion."""

import argparse
import re

_DIGEST_REF = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:([a-f0-9]{64})$")


def validate_image_reference(reference: str) -> str:
    match = _DIGEST_REF.fullmatch(reference)
    if not match or set(match.group(1)) == {"0"}:
        raise ValueError("image must be an immutable non-placeholder sha256 digest reference")
    return reference


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    args = parser.parse_args()
    print(validate_image_reference(args.image))


if __name__ == "__main__":
    main()
