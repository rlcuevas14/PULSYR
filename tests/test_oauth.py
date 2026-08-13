"""Sign in with GitHub/Google, and the short /login routes that replaced /auth/*.

The provider is faked at the ``fetch_identity`` seam: everything above it (state
signing, the account-or-refuse decision, session start) is the part worth testing,
and everything below it is someone else's HTTP API.
"""
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import oauth
from app.auth.models import User
from app.auth.rate_limit import oauth_rate_limiter
from app.auth.service import authenticate, create_user
from app.config import settings
from app.database import get_db


@pytest.fixture
def github_configured(monkeypatch):
    monkeypatch.setattr(settings, "oauth_github_client_id", "cid")
    monkeypatch.setattr(settings, "oauth_github_client_secret", "csecret")
    monkeypatch.setattr(settings, "base_url", "https://app.pulsyr.dev")


@pytest.fixture(autouse=True)
def reset_oauth_rate_limit():
    oauth_rate_limiter.reset()
    yield
    oauth_rate_limiter.reset()


def fake_identity(monkeypatch, email: str, name: str = "Someone"):
    async def _fake(provider, code):
        return oauth.Identity(email=email, name=name, subject=f"subject:{email}")

    monkeypatch.setattr(oauth, "fetch_identity", _fake)


def fake_identity_rejected(monkeypatch):
    """What an unverified address looks like from the caller's side: None."""

    async def _fake(provider, code):
        return None

    monkeypatch.setattr(oauth, "fetch_identity", _fake)


async def _seed_user(client, email: str) -> None:
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, email, "Seeded", "password123")
        break


# ---------- state ----------

def test_state_roundtrips(github_configured):
    state = oauth.make_state("github")
    assert oauth.check_state(state, "github") is True


def test_signup_state_carries_signed_intent_and_terms(github_configured):
    state = oauth.make_state("github", intent="signup", terms_version="v1")
    assert oauth.read_state(state, "github")["i"] == "signup"
    assert oauth.read_state(state, "github")["v"] == "v1"


def test_state_is_bound_to_its_provider(github_configured):
    # A callback cannot be replayed against a different provider.
    assert oauth.check_state(oauth.make_state("github"), "google") is False


def test_state_rejects_forgery(github_configured):
    assert oauth.check_state("not-a-signed-value", "github") is False


# ---------- configuration gating ----------

def test_provider_absent_without_keys():
    assert oauth.get("github") is None
    assert oauth.configured() == []


def test_provider_present_with_keys(github_configured):
    assert oauth.get("github") is not None
    assert [p.name for p in oauth.configured()] == ["github"]


def test_authorize_url_carries_redirect_and_state(github_configured):
    url = oauth.authorize_url(oauth.PROVIDERS["github"], "st4te")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "state=st4te" in url
    assert "redirect_uri=https%3A%2F%2Fapp.pulsyr.dev%2Fcallback%2Fgithub" in url


@pytest.mark.asyncio
async def test_login_page_hides_buttons_when_unconfigured(client):
    await _seed_user(client, "solo@test.cl")
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "/login/github" not in resp.text


@pytest.mark.asyncio
async def test_login_page_shows_buttons_when_configured(client, github_configured):
    await _seed_user(client, "withoauth@test.cl")
    resp = await client.get("/login")
    assert "/login/github" in resp.text


@pytest.mark.asyncio
async def test_oauth_start_redirects_to_provider(client, github_configured):
    resp = await client.get("/login/github", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("https://github.com/login/oauth/authorize?")


@pytest.mark.asyncio
async def test_signup_requires_consent_before_oauth(client, github_configured, monkeypatch):
    await _seed_user(client, "signup-gate@test.cl")
    monkeypatch.setattr(settings, "public_signup", True)

    page = await client.get("/signup")
    assert page.status_code == 200
    assert 'name="accept_terms"' in page.text

    refused = await client.post("/signup", data={"provider": "github"})
    assert refused.status_code == 422

    accepted = await client.post(
        "/signup",
        data={"provider": "github", "accept_terms": "true"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"].startswith("https://github.com/login/oauth/authorize?")


@pytest.mark.asyncio
async def test_oauth_start_on_unknown_provider_goes_back_to_login(client):
    resp = await client.get("/login/myspace", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# ---------- callback ----------

@pytest.mark.asyncio
async def test_callback_rejects_bad_state(client, github_configured, monkeypatch):
    await _seed_user(client, "state@test.cl")
    fake_identity(monkeypatch, "state@test.cl")
    resp = await client.get("/callback/github?code=abc&state=forged")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_rejects_missing_code(client, github_configured):
    await _seed_user(client, "nocode@test.cl")
    state = oauth.make_state("github")
    resp = await client.get(f"/callback/github?state={state}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_rejects_unverified_identity(client, github_configured, monkeypatch):
    await _seed_user(client, "someone@test.cl")
    fake_identity_rejected(monkeypatch)
    state = oauth.make_state("github")
    resp = await client.get(f"/callback/github?code=abc&state={state}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_logs_in_existing_user(client, github_configured, monkeypatch):
    await _seed_user(client, "known@test.cl")
    fake_identity(monkeypatch, "known@test.cl")
    monkeypatch.setattr(settings, "public_signup", False)
    state = oauth.make_state("github")

    resp = await client.get(f"/callback/github?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


@pytest.mark.asyncio
async def test_callback_refuses_unknown_user_when_signup_closed(client, github_configured, monkeypatch):
    await _seed_user(client, "resident@test.cl")
    fake_identity(monkeypatch, "stranger@test.cl")
    monkeypatch.setattr(settings, "public_signup", False)
    state = oauth.make_state("github")

    resp = await client.get(f"/callback/github?code=abc&state={state}")
    assert resp.status_code == 403
    async for db in client.app.dependency_overrides[get_db]():
        assert await db.scalar(select(User.id).where(User.email == "stranger@test.cl")) is None
        break


@pytest.mark.asyncio
async def test_callback_creates_account_when_signup_open(client, github_configured, monkeypatch):
    await _seed_user(client, "resident2@test.cl")
    fake_identity(monkeypatch, "newcomer@test.cl", name="New Comer")
    monkeypatch.setattr(settings, "public_signup", True)
    state = oauth.make_state(
        "github", intent="signup", terms_version=settings.terms_version
    )

    resp = await client.get(f"/callback/github?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/welcome"
    assert "pulsyr_session" in resp.cookies

    async for db in client.app.dependency_overrides[get_db]():
        user = (await db.execute(select(User).where(User.email == "newcomer@test.cl"))).scalar_one()
        # No credential is stored for them, which is the whole point.
        assert user.password_hash is None
        assert user.account_role == "owner"
        assert user.terms_version == settings.terms_version
        from app.accounts.models import AccountSubscription
        from app.auth.models import OAuthIdentity
        from app.projects.models import Project

        subscription = await db.scalar(
            select(AccountSubscription).where(AccountSubscription.account_id == user.account_id)
        )
        assert subscription is not None and subscription.plan_code == "free"
        assert await db.scalar(select(Project.id).where(Project.account_id == user.account_id))
        assert await db.scalar(select(OAuthIdentity.id).where(OAuthIdentity.user_id == user.id))
        break

    welcome = await client.get("/welcome")
    assert welcome.status_code == 200
    completed = await client.post(
        "/welcome",
        data={"project_name": "My Product", "token_name": "codex"},
    )
    assert completed.status_code == 200
    assert "My Product" in completed.text

    async for db in client.app.dependency_overrides[get_db]():
        from app.accounts.models import AccountSubscription
        from app.auth.models import ApiToken
        from app.projects.models import Project

        user = (await db.execute(select(User).where(User.email == "newcomer@test.cl"))).scalar_one()
        subscription = await db.scalar(
            select(AccountSubscription).where(AccountSubscription.account_id == user.account_id)
        )
        project = await db.scalar(select(Project).where(Project.account_id == user.account_id))
        assert subscription is not None and subscription.onboarding_completed_at is not None
        assert project is not None and project.name == "My Product" and project.slug == "my-product"
        assert await db.scalar(select(ApiToken.id).where(ApiToken.project_id == project.id))
        break


@pytest.mark.asyncio
async def test_login_intent_does_not_silently_register(client, github_configured, monkeypatch):
    await _seed_user(client, "resident3@test.cl")
    fake_identity(monkeypatch, "new-login@test.cl")
    monkeypatch.setattr(settings, "public_signup", True)
    state = oauth.make_state("github", intent="login")

    resp = await client.get(f"/callback/github?code=abc&state={state}")

    assert resp.status_code == 403
    async for db in client.app.dependency_overrides[get_db]():
        assert await db.scalar(select(User.id).where(User.email == "new-login@test.cl")) is None
        break


# ---------- a passwordless user cannot be logged in with a password ----------

@pytest.mark.asyncio
async def test_null_password_hash_never_authenticates(db: AsyncSession):
    user = await create_user(db, "oauthonly@test.cl", "OAuth Only", "temporary")
    user.password_hash = None
    await db.commit()

    assert await authenticate(db, "oauthonly@test.cl", "temporary") is None
    assert await authenticate(db, "oauthonly@test.cl", "") is None


# ---------- what the providers actually say ----------
# The rule these cover is the one the feature rests on: only an address the
# provider marks verified is allowed to identify a person. Mocking above
# fetch_identity (as the tests further up do) would leave it unexercised.

def _mock_http(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(oauth.httpx, "AsyncClient", factory)


def _github_handler(emails, *, token_ok=True):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login/oauth/access_token" in url:
            return httpx.Response(200 if token_ok else 401, json={"access_token": "tok"})
        if url.endswith("/user/emails"):
            return httpx.Response(200, json=emails)
        if url.endswith("/user"):
            return httpx.Response(200, json={"id": 123, "name": "Octo Cat", "login": "octocat"})
        return httpx.Response(404)
    return handle


@pytest.mark.asyncio
async def test_github_takes_the_primary_verified_address(github_configured, monkeypatch):
    _mock_http(monkeypatch, _github_handler([
        {"email": "old@test.cl", "primary": False, "verified": True},
        {"email": "real@test.cl", "primary": True, "verified": True},
    ]))
    identity = await oauth.fetch_identity(oauth.PROVIDERS["github"], "code")
    assert identity is not None
    assert identity.email == "real@test.cl"
    assert identity.name == "Octo Cat"


@pytest.mark.asyncio
async def test_github_refuses_unverified_primary(github_configured, monkeypatch):
    # Anyone can add any address to a GitHub account; only verification proves it.
    _mock_http(monkeypatch, _github_handler([
        {"email": "victim@test.cl", "primary": True, "verified": False},
    ]))
    assert await oauth.fetch_identity(oauth.PROVIDERS["github"], "code") is None


@pytest.mark.asyncio
async def test_github_refuses_verified_but_not_primary(github_configured, monkeypatch):
    _mock_http(monkeypatch, _github_handler([
        {"email": "secondary@test.cl", "primary": False, "verified": True},
    ]))
    assert await oauth.fetch_identity(oauth.PROVIDERS["github"], "code") is None


@pytest.mark.asyncio
async def test_failed_token_exchange_yields_no_identity(github_configured, monkeypatch):
    _mock_http(monkeypatch, _github_handler([], token_ok=False))
    assert await oauth.fetch_identity(oauth.PROVIDERS["github"], "code") is None


def _google_handler(userinfo):
    def handle(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={"sub": "google-123", **userinfo})
    return handle


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "oauth_google_client_id", "gid")
    monkeypatch.setattr(settings, "oauth_google_client_secret", "gsecret")


@pytest.mark.asyncio
async def test_google_accepts_verified(google_configured, monkeypatch):
    _mock_http(monkeypatch, _google_handler(
        {"email": "g@test.cl", "email_verified": True, "name": "G User"}))
    identity = await oauth.fetch_identity(oauth.PROVIDERS["google"], "code")
    assert identity is not None and identity.email == "g@test.cl"


@pytest.mark.asyncio
async def test_google_accepts_the_string_form_of_verified(google_configured, monkeypatch):
    # id_token payloads spell it "true"; userinfo sends a bool. Both are fine.
    _mock_http(monkeypatch, _google_handler(
        {"email": "g2@test.cl", "email_verified": "true", "name": "G2"}))
    assert await oauth.fetch_identity(oauth.PROVIDERS["google"], "code") is not None


@pytest.mark.asyncio
async def test_google_refuses_unverified(google_configured, monkeypatch):
    _mock_http(monkeypatch, _google_handler(
        {"email": "g3@test.cl", "email_verified": False, "name": "G3"}))
    assert await oauth.fetch_identity(oauth.PROVIDERS["google"], "code") is None


@pytest.mark.asyncio
async def test_google_refuses_when_the_flag_is_missing(google_configured, monkeypatch):
    _mock_http(monkeypatch, _google_handler({"email": "g4@test.cl", "name": "G4"}))
    assert await oauth.fetch_identity(oauth.PROVIDERS["google"], "code") is None


# ---------- the old /auth/* paths ----------

@pytest.mark.asyncio
async def test_legacy_login_path_redirects(client):
    resp = await client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_legacy_login_post_preserves_method(client):
    # 308, not 301: a 301 would let the client downgrade the POST to a GET and
    # silently drop the credentials.
    resp = await client.post("/auth/login", data={"email": "a@b.cl", "password": "x"},
                             follow_redirects=False)
    assert resp.status_code == 308
