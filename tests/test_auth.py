import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import (
    authenticate,
    create_api_token,
    create_user,
    revoke_api_token,
    verify_api_token,
)


@pytest.mark.asyncio
async def test_create_user_and_authenticate(db: AsyncSession):
    user = await create_user(db, "admin@test.cl", "Admin Test", "secreta123", "admin")
    assert user.id is not None
    assert user.account_role == "owner"
    assert user.is_superadmin is True

    found = await authenticate(db, "admin@test.cl", "secreta123")
    assert found is not None
    assert found.id == user.id


@pytest.mark.asyncio
async def test_authenticate_wrong_password(db: AsyncSession):
    await create_user(db, "user2@test.cl", "User 2", "correcta")
    found = await authenticate(db, "user2@test.cl", "incorrecta")
    assert found is None


@pytest.mark.asyncio
async def test_api_token_create_and_verify(db: AsyncSession):
    user = await create_user(db, "tokenuser@test.cl", "Token User", "pass123")
    token, raw = await create_api_token(db, "CI token", "write", user.id)
    assert token.id is not None
    assert raw != token.token_hash

    found = await verify_api_token(db, raw)
    assert found is not None
    assert found.id == token.id
    assert found.last_used_at is not None


@pytest.mark.asyncio
async def test_revoked_token_is_rejected(db: AsyncSession):
    user = await create_user(db, "revoke@test.cl", "Revoke User", "pass")
    token, raw = await create_api_token(db, "revocable", "read", user.id)
    await revoke_api_token(db, token.id)

    found = await verify_api_token(db, raw)
    assert found is None


@pytest.mark.asyncio
async def test_login_endpoint_sets_cookie(client):
    from app.database import get_db
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, "login@test.cl", "Login User", "login123", "admin")
        break

    resp = await client.post(
        "/auth/login",
        data={"email": "login@test.cl", "password": "login123"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "pulsyr_session" in resp.cookies


@pytest.mark.asyncio
async def test_protected_route_without_cookie_redirects(client):
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/auth/login" in resp.headers.get("location", "")
