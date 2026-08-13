from http.client import RemoteDisconnected

from scripts import check_site_links


class SuccessfulResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_external_probe_retries_remote_disconnect(monkeypatch):
    outcomes = iter([RemoteDisconnected("closed"), SuccessfulResponse()])

    def fake_urlopen(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(check_site_links, "urlopen", fake_urlopen)
    monkeypatch.setattr(check_site_links.time, "sleep", lambda _seconds: None)

    assert check_site_links.probe("https://github.com/example/repo") == (200, None)


def test_external_probe_returns_error_after_retries(monkeypatch):
    monkeypatch.setattr(
        check_site_links,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RemoteDisconnected("closed")),
    )
    monkeypatch.setattr(check_site_links.time, "sleep", lambda _seconds: None)

    status, error = check_site_links.probe("https://github.com/example/repo")

    assert status is None
    assert error == "closed"


def test_internal_language_endpoint_is_validated_as_worker_route(tmp_path):
    (tmp_path / "index.html").write_text(
        '<a href="/__language/es?next=%2F">Español</a>',
        encoding="utf-8",
    )

    errors, _external, checked = check_site_links.check_internal(tmp_path)

    assert errors == []
    assert checked == 1


def test_internal_language_endpoint_rejects_missing_next_page(tmp_path):
    (tmp_path / "index.html").write_text(
        '<a href="/__language/es?next=%2Fmissing%2F">Español</a>',
        encoding="utf-8",
    )

    errors, _external, _checked = check_site_links.check_internal(tmp_path)

    assert errors == ["/: language link points from missing page: /missing/"]
