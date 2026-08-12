from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_staging_compose_uses_isolated_immutable_candidate_contract():
    compose = yaml.safe_load((ROOT / "compose.staging.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app-staging"]
    database = compose["services"]["db-staging"]

    assert "build" not in app
    assert "PULSYR_IMAGE" in app["image"]
    assert app["ports"][0].startswith("127.0.0.1:")
    assert app["environment"]["DEPLOYMENT_ENVIRONMENT"] == "staging"
    assert app["environment"]["DEBUG"] == "false"
    assert database["volumes"][0].startswith("pulsyr_staging_db:")
    assert "pulsyr_staging_db" in compose["volumes"]


def test_staging_proxy_requires_authentication_and_noindex():
    caddy = (ROOT / "infra" / "Caddyfile.staging.example").read_text(encoding="utf-8")
    assert caddy.count("basic_auth") == 2
    assert caddy.count('X-Robots-Tag "noindex, nofollow"') == 2
    assert "app-staging.pulsyr.dev" in caddy
    assert "staging.pulsyr.dev" in caddy
