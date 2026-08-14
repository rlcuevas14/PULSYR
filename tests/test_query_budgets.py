from scripts.check_query_budgets import QUERIES, _walk


def test_query_budget_inventory_has_index_and_time_guards():
    assert {budget.name for budget in QUERIES} == {
        "items_recent", "items_impact", "threads_recent", "job_claim",
    }
    assert all(budget.expected_index and budget.max_ms <= 25 for budget in QUERIES)


def test_query_plan_walker_visits_nested_nodes():
    plan = {"Node Type": "Limit", "Plans": [{"Node Type": "Index Scan"}]}
    assert [node["Node Type"] for node in _walk(plan)] == ["Limit", "Index Scan"]
