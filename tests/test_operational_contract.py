from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_production_container_has_readiness_and_graceful_shutdown_contract():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "stop_grace_period: 45s" in compose
    assert "http://127.0.0.1:8000/health/ready" in compose
    assert "start_period: 10s" in compose
    assert '"--timeout-graceful-shutdown", "30"' in dockerfile


def test_deploy_requires_candidate_health_and_restores_previous_image():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    required = (
        'previous_image="$(docker inspect',
        'export PULSYR_IMAGE="$candidate_image"',
        "for attempt in $(seq 1 30)",
        'if [ "$healthy" != true ]',
        'export PULSYR_IMAGE="$previous_image"',
        "CRITICAL: rollback image also failed readiness",
        ".last-successful-image",
    )
    for contract in required:
        assert contract in workflow
    assert "alembic downgrade" not in workflow


def test_alert_catalog_covers_red_saturation_jobs_and_collection_failure():
    rules = (ROOT / "infra/monitoring/pulsyr-alerts.yml").read_text(encoding="utf-8")
    alerts = (
        "PulsyrReadinessUnavailable",
        "PulsyrHighServerErrorRatio",
        "PulsyrHighP95Latency",
        "PulsyrOldestPendingJob",
        "PulsyrJobFailures",
        "PulsyrDatabasePoolSaturation",
        "PulsyrMetricsCollectionFailed",
    )

    for alert in alerts:
        assert rules.count(f"alert: {alert}") == 1
    assert rules.count("owner: service-owner") == len(alerts)
    assert "project_id" not in rules
