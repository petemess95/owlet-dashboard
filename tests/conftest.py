import re

import pytest
from fastapi.testclient import TestClient

from app.auth_store import AuthStore
from app.config import Settings
from app.main import create_app
from app.security import hash_password, hash_token, new_token
from app.store import ReadingStore


def test_settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


@pytest.fixture
def app_bundle(tmp_path):
    """(app, store, auth_store) wired against a tmp SQLite file."""
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    auth_store = AuthStore(tmp_path / "owlet.sqlite3")
    app = create_app(store=store, settings=test_settings(), start_poller=False, auth_store=auth_store)
    return app, store, auth_store


async def make_user(auth_store: AuthStore, email: str) -> tuple[dict, str]:
    """Create a user + session directly; returns (user, raw_session_token)."""
    user = await auth_store.create_user(email, hash_password("hunter22"))
    raw = new_token()
    await auth_store.create_session(user["id"], hash_token(raw))
    return user, raw


def client_for(app, session_token: str | None = None) -> TestClient:
    client = TestClient(app)
    if session_token:
        client.cookies.set("owlet_session", session_token)
    return client


_PAGE_ASSET_REF = re.compile(r'(?:href|src)="(/static/pages/[a-z0-9_-]+\.(?:css|js))"')


def with_page_assets(client: TestClient, html: str) -> str:
    """The page as the browser assembles it: its HTML followed by every
    /static/pages/ file it links (page CSS/JS that used to be inline).
    Each linked file must be served, so a dangling link fails the test."""
    parts = [html]
    for path in _PAGE_ASSET_REF.findall(html):
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.status_code}"
        parts.append(response.text)
    return "\n".join(parts)
