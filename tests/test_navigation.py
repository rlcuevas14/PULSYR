from types import SimpleNamespace

from starlette.requests import Request

from app.templates_config import templates
from app.ui.navigation import navigation_context


def test_primary_navigation_expresses_domain_hierarchy():
    nav = navigation_context("/backlog")

    assert [entry.key for entry in nav.primary] == [
        "backlog",
        "threads",
        "management",
        "incidents",
    ]
    assert [entry.key for entry in nav.backlog_tabs] == ["backlog_work", "priority", "archive"]
    management = next(entry for entry in nav.primary if entry.key == "management")
    assert [entry.key for entry in management.children] == [
        "management_pending",
        "management_plan",
        "management_documents",
    ]


def test_backlog_parent_is_active_for_all_core_views_and_item_detail():
    cases = {
        "/backlog": "backlog_work",
        "/priority": "priority",
        "/archive": "archive",
        "/items/00000000-0000-0000-0000-000000000001": None,
    }

    for path, child in cases.items():
        nav = navigation_context(path)
        assert nav.active_key == "backlog"
        assert nav.active_child_key == child


def test_management_parent_and_child_are_active():
    nav = navigation_context("/management/plan")

    assert nav.active_key == "management"
    assert nav.active_child_key == "management_plan"


def test_navigation_can_be_filtered_without_disabling_core():
    nav = navigation_context("/management/plan", enabled_modules={"management"})

    assert [entry.key for entry in nav.primary] == ["backlog", "management"]
    assert nav.active_key == "management"

    core_only = navigation_context("/threads", enabled_modules=set())
    assert [entry.key for entry in core_only.primary] == ["backlog"]
    assert core_only.active_key is None


def test_mobile_navigation_has_five_slots_and_nonduplicated_overflow():
    nav = navigation_context("/backlog")

    assert [entry.key for entry in nav.mobile_primary] == [
        "backlog",
        "priority",
        "create",
        "threads",
        "more",
    ]
    assert [entry.key for entry in nav.mobile_more] == ["archive", "incidents", "management"]


def test_mobile_navigation_promotes_archive_when_threads_are_disabled():
    nav = navigation_context("/archive", enabled_modules={"incidents", "management"})

    assert [entry.key for entry in nav.mobile_primary] == [
        "backlog",
        "priority",
        "create",
        "archive",
        "more",
    ]
    assert [entry.key for entry in nav.mobile_more] == ["incidents", "management"]
    assert nav.active_key == "backlog"
    assert nav.active_child_key == "archive"


def test_base_template_renders_responsive_hierarchy_and_badge():
    path = "/priority"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "session": {"current_project_slug": "demo"},
        }
    )
    user = SimpleNamespace(
        name="Test",
        email="test@example.com",
        account_role="owner",
        is_superadmin=True,
        plan_code="free",
    )

    html = templates.env.get_template("base.html").render(
        request=request,
        user=user,
        nav_badges={"incidents_new": 123},
    )

    assert "p-mobile-nav" in html
    assert 'data-modal-open="nav-more"' in html
    assert 'href="/backlog?new=1"' in html
    assert 'href="/backlog" class="p-mobile-nav-item "' in html
    assert 'href="/priority" class="p-mobile-nav-item p-mobile-nav-active"' in html
    assert "99+" in html
    assert "/management/pendientes" in html
    assert html.index("/management/pendientes") < html.index("/management/plan")
    assert html.index("/management/plan") < html.index("/management/documentos")
    assert "nav-drawer" not in html
