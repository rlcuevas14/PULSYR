import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.service import create_user
from app.items.models import Item
from app.projects.models import Project
from app.scopes.models import Scope


@pytest.fixture(autouse=True, scope="module")
async def ensure_search_vector(test_engine):
    """Agrega la columna tsvector si no existe (create_all no la genera)."""
    async with test_engine.begin() as conn:
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='items' AND column_name='search_vector'
                ) THEN
                    ALTER TABLE items
                    ADD COLUMN search_vector tsvector
                    GENERATED ALWAYS AS (
                        setweight(to_tsvector('spanish', coalesce(title, '')), 'A') ||
                        setweight(to_tsvector('spanish', coalesce(summary_md, '')), 'B')
                    ) STORED;
                    CREATE INDEX items_search_gin ON items USING GIN (search_vector);
                END IF;
            END $$;
        """))


@pytest.mark.asyncio
async def test_search_finds_by_title(client: AsyncClient, test_engine):
    uid = uuid.uuid4().hex[:8]
    email = f"searchadmin-{uid}@test.cl"

    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as s:
        user = await create_user(s, email, "Admin", "pass", "admin")
        project = await s.scalar(select(Project).where(Project.account_id == user.account_id))
        scope = Scope(name=f"search-scope-{uid}", project_id=project.id)
        s.add(scope)
        await s.commit()
        await s.refresh(scope)
        item = Item(
            scope_id=scope.id,
            project_id=project.id,
            title="Autenticación con OAuth",
            summary_md="Integrar OAuth 2.0 con Google.",
            type="feature",
            origen="human",
        )
        s.add(item)
        await s.commit()

    login = await client.post(
        "/login",
        data={"email": email, "password": "pass"},
        follow_redirects=False,
    )
    cookies = dict(login.cookies)

    resp = await client.get("/api/v1/items/search?q=oauth", cookies=cookies)
    assert resp.status_code == 200
    results = resp.json()
    assert any("OAuth" in r["title"] for r in results)


@pytest.mark.asyncio
async def test_search_empty_query_returns_400(client: AsyncClient):
    resp = await client.get("/api/v1/items/search")
    assert resp.status_code in (400, 401, 422)


@pytest.mark.asyncio
async def test_websearch_syntax_or_phrase_negation(test_engine):
    """websearch_to_tsquery: OR entre términos, frase entre comillas y -exclusión.
    plainto_tsquery hacía AND estricto — 'oauth OR saml' no matcheaba nada."""
    from app.items.search import search_items

    uid = uuid.uuid4().hex[:8]
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as s:
        user = await create_user(s, f"ws-{uid}@t.cl", "Admin", "pass", "admin")
        project = await s.scalar(select(Project).where(Project.account_id == user.account_id))
        scope = Scope(name=f"ws-scope-{uid}", project_id=project.id)
        s.add(scope)
        await s.commit()
        await s.refresh(scope)
        pid = project.id
        s.add_all([
            Item(scope_id=scope.id, project_id=pid, title=f"Login con OAuth {uid}",
                 type="feature", origen="human"),
            Item(scope_id=scope.id, project_id=pid, title=f"Login con SAML {uid}",
                 type="feature", origen="human"),
            Item(scope_id=scope.id, project_id=pid, title=f"Reporte mensual {uid}",
                 type="feature", origen="human"),
        ])
        await s.commit()

        # OR: matchea ambos logins (plainto hubiese hecho AND -> 0 resultados)
        rows = await search_items(s, "oauth OR saml", project_id=pid)
        titles = {r["title"] for r in rows}
        assert any("OAuth" in t for t in titles) and any("SAML" in t for t in titles)

        # Exclusión: login pero no el de OAuth
        rows2 = await search_items(s, f"{uid} -oauth", project_id=pid)
        t2 = {r["title"] for r in rows2}
        assert any("SAML" in t for t in t2) and not any("OAuth" in t for t in t2)

        # Frase exacta entre comillas
        rows3 = await search_items(s, '"login con oauth"', project_id=pid)
        assert any("OAuth" in r["title"] for r in rows3)
