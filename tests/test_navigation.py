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
