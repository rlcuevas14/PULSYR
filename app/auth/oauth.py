"""Sign in with GitHub or Google.

Built on httpx (already a dependency) rather than an OAuth library, for the same
reason the MCP server is hand-rolled: it is three HTTP calls, and keeping the
seam small means tests can replace ``fetch_identity`` without a framework in the
way. The part we deliberately do NOT own is identity itself: the provider holds
the account and vouches for the address, which is what removes the need for a
mailer, email confirmation and password resets.

The address we trust is a *verified* one. Google reports ``email_verified``;
GitHub omits the address from /user when it is private, so we read /user/emails
and take the primary verified entry. An unverified address is refused: otherwise
anyone could sign up to a provider with someone else's address and walk into
their Pulsyr account.
"""
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

_TIMEOUT = 10.0
# The provider's redirect back to us is a cross-site navigation, and the session
# cookie is SameSite=Strict (SEC-04), so it is NOT sent on that request: state
# kept in the session would always read back empty. Sign the state instead, so it
# carries its own integrity and needs nothing on our side to verify.
_STATE_MAX_AGE = 600


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorize_url: str
    token_url: str
    scope: str


@dataclass(frozen=True)
class Identity:
    email: str
    name: str
    subject: str


PROVIDERS: dict[str, Provider] = {
    "github": Provider(
        name="github",
        label="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
    ),
    "google": Provider(
        name="google",
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
    ),
}


def credentials(name: str) -> tuple[str, str]:
    """(client_id, client_secret) for a provider, empty strings when unset."""
    if name == "github":
        return settings.oauth_github_client_id, settings.oauth_github_client_secret
    if name == "google":
        return settings.oauth_google_client_id, settings.oauth_google_client_secret
    return "", ""


def get(name: str) -> Provider | None:
    """A provider, only if it is both known and fully configured."""
    provider = PROVIDERS.get(name)
    if provider is None:
        return None
    client_id, client_secret = credentials(name)
    if not client_id or not client_secret:
        return None
    return provider


def configured() -> list[Provider]:
    """Providers usable right now. Empty on an instance with no OAuth keys, which
    is what keeps the buttons off the login page for self-hosters."""
    return [p for name in PROVIDERS if (p := get(name)) is not None]


def redirect_uri(provider: str) -> str:
    """Callback URL registered with the provider.

    Derived from BASE_URL rather than the inbound request: behind the reverse
    proxy the request host is not authoritative, and a redirect_uri that does not
    match the registration is rejected by the provider anyway.
    """
    return f"{settings.base_url.rstrip('/')}/callback/{provider}"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="pulsyr-oauth-state")


def make_state(
    provider: str,
    *,
    intent: str = "login",
    terms_version: str | None = None,
) -> str:
    """A signed, short-lived, single-provider state parameter."""
    payload = {"p": provider, "i": intent, "n": secrets.token_urlsafe(8)}
    if terms_version:
        payload["v"] = terms_version
    return _serializer().dumps(payload)


def read_state(state: str, provider: str) -> dict[str, str] | None:
    """Return a valid state payload, or None when forged, stale or misbound."""
    try:
        data: Any = _serializer().loads(state, max_age=_STATE_MAX_AGE)
    except BadSignature:
        return None
    if not isinstance(data, dict) or data.get("p") != provider:
        return None
    return {str(key): str(value) for key, value in data.items()}


def check_state(state: str, provider: str) -> bool:
    """Reject anything we did not mint, that expired, or that was issued for
    another provider (so a callback cannot be replayed against a different one)."""
    return read_state(state, provider) is not None


def authorize_url(provider: Provider, state: str) -> str:
    client_id, _ = credentials(provider.name)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider.name),
        "scope": provider.scope,
        "state": state,
        "response_type": "code",
    }
    return f"{provider.authorize_url}?{urlencode(params)}"


async def _access_token(provider: Provider, code: str) -> str | None:
    client_id, client_secret = credentials(provider.name)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        resp = await http.post(
            provider.token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri(provider.name),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        return None
    token = resp.json().get("access_token")
    return token if isinstance(token, str) and token else None


async def _github_identity(token: str) -> Identity | None:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        profile = await http.get("https://api.github.com/user", headers=headers)
        emails = await http.get("https://api.github.com/user/emails", headers=headers)
    if profile.status_code != 200 or emails.status_code != 200:
        return None
    # Primary AND verified. GitHub lets you add any address; only the verified
    # ones prove control, and only the primary is the one the user means.
    address = next(
        (e["email"] for e in emails.json() if e.get("primary") and e.get("verified")),
        None,
    )
    if not address:
        return None
    body = profile.json()
    subject = body.get("id")
    if subject is None:
        return None
    return Identity(
        email=address.casefold(),
        name=body.get("name") or body.get("login") or address,
        subject=str(subject),
    )


async def _google_identity(token: str) -> Identity | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        resp = await http.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        return None
    body = resp.json()
    # email_verified arrives as a bool from the userinfo endpoint, but as the
    # string "true" in some id_token payloads — accept both, reject anything else.
    verified = body.get("email_verified")
    if verified not in (True, "true"):
        return None
    address = body.get("email")
    subject = body.get("sub")
    if not address or subject is None:
        return None
    return Identity(email=address.casefold(), name=body.get("name") or address, subject=str(subject))


async def fetch_identity(provider: Provider, code: str) -> Identity | None:
    """Exchange the callback code for a verified identity, or None.

    Every failure path collapses to None on purpose: the caller shows one generic
    message. Telling a visitor whether the code, the token or the address was the
    problem only helps someone probing.
    """
    token = await _access_token(provider, code)
    if token is None:
        return None
    if provider.name == "github":
        return await _github_identity(token)
    if provider.name == "google":
        return await _google_identity(token)
    return None
