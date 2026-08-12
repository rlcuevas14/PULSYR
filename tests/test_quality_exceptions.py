import datetime as dt
import json

from scripts.check_quality_exceptions import validate


def write_registry(tmp_path, exceptions):
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"exceptions": exceptions}), encoding="utf-8")
    return path


def test_empty_quality_exception_registry_is_valid(tmp_path):
    assert validate(write_registry(tmp_path, []), dt.date(2026, 8, 12)) == []


def test_quality_exceptions_require_known_gate_and_short_expiry(tmp_path):
    errors = validate(
        write_registry(
            tmp_path,
            [
                {
                    "id": "QG-1",
                    "gate": "unknown",
                    "owner": "",
                    "approved_by": "web-lead",
                    "reason": "",
                    "tracking": "https://github.com/example/project/issues/1",
                    "expires": "2026-10-01",
                }
            ],
        ),
        dt.date(2026, 8, 12),
    )
    assert any("unknown gate" in error for error in errors)
    assert any("owner, approver, reason and tracking" in error for error in errors)
    assert any("cannot exceed 30 days" in error for error in errors)


def test_expired_quality_exception_fails(tmp_path):
    errors = validate(
        write_registry(
            tmp_path,
            [
                {
                    "id": "QG-2",
                    "gate": "lighthouse",
                    "owner": "web-owner",
                    "approved_by": "web-lead",
                    "reason": "Temporary regression with linked remediation",
                    "tracking": "https://github.com/example/project/issues/2",
                    "expires": "2026-08-11",
                }
            ],
        ),
        dt.date(2026, 8, 12),
    )
    assert errors == ["exceptions[0]: expired on 2026-08-11"]
