from app.secret_fields import resolve_write_only_secret


def test_write_only_secret_preserves_replaces_and_clears():
    assert resolve_write_only_secret("stored", "") == "stored"
    assert resolve_write_only_secret("stored", "  new  ") == "new"
    assert resolve_write_only_secret("stored", "new", clear=True) is None
    assert resolve_write_only_secret(None, "") is None
