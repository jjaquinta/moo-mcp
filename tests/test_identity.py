"""Unit tests for MOOConfig.for_identity and moo_mcp.identity.resolve_identity_conn."""

import asyncio

import pytest

from moo_mcp.connection import MOOConfig, MOOError
from moo_mcp.identity import resolve_identity_conn

PRIMARY = MOOConfig(
    host="example.test",
    port=7777,
    user="wizard",
    password="hunter2",
    timeout=5.0,
)


def test_for_identity_resolves_env_vars(monkeypatch):
    monkeypatch.setenv("MOO_USER_AMADAN", "amadan")
    monkeypatch.setenv("MOO_PASS_AMADAN", "idiot")
    cfg = MOOConfig.for_identity(PRIMARY, "amadan")
    assert cfg.user == "amadan"
    assert cfg.password == "idiot"
    assert cfg.verify_login is False
    # Inherited from primary.
    assert cfg.host == PRIMARY.host
    assert cfg.port == PRIMARY.port
    assert cfg.timeout == PRIMARY.timeout


def test_for_identity_uppercases_and_sanitizes_key(monkeypatch):
    monkeypatch.setenv("MOO_USER_TEST_BOT", "testbot")
    monkeypatch.setenv("MOO_PASS_TEST_BOT", "secret")
    cfg = MOOConfig.for_identity(PRIMARY, "test-bot")
    assert cfg.user == "testbot"


def test_for_identity_missing_env_vars_raises_naming_both(monkeypatch):
    monkeypatch.delenv("MOO_USER_GHOST", raising=False)
    monkeypatch.delenv("MOO_PASS_GHOST", raising=False)
    with pytest.raises(MOOError) as exc_info:
        MOOConfig.for_identity(PRIMARY, "ghost")
    assert "MOO_USER_GHOST" in str(exc_info.value)
    assert "MOO_PASS_GHOST" in str(exc_info.value)


@pytest.mark.parametrize("bad_char", ["\r", "\n"])
def test_for_identity_rejects_credential_injection(monkeypatch, bad_char):
    # NUL bytes can't reach this path via env vars (the OS itself rejects
    # NUL in environment variables - see test_validate_credential_rejects_nul
    # for direct coverage of that branch).
    monkeypatch.setenv("MOO_USER_BAD", f"bad{bad_char}user")
    monkeypatch.setenv("MOO_PASS_BAD", "whatever")
    with pytest.raises(MOOError):
        MOOConfig.for_identity(PRIMARY, "bad")


def test_validate_credential_rejects_nul():
    with pytest.raises(MOOError):
        MOOConfig._validate_credential("MOO_PASS_BAD", "bad\x00user")


class _FakeConn:
    def __init__(self, config: MOOConfig) -> None:
        self.config = config
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1


@pytest.mark.asyncio
async def test_resolve_identity_conn_none_returns_primary():
    identities: dict = {}
    lock = asyncio.Lock()
    primary_conn = object()
    result = await resolve_identity_conn(
        identities=identities,
        lock=lock,
        primary_config=PRIMARY,
        primary_conn=primary_conn,
        as_player=None,
    )
    assert result is primary_conn
    assert identities == {}


@pytest.mark.asyncio
async def test_resolve_identity_conn_same_as_primary_reuses_it(monkeypatch):
    # as_player resolves to the same username as the primary connection.
    monkeypatch.setenv("MOO_USER_WIZARD", "wizard")
    monkeypatch.setenv("MOO_PASS_WIZARD", "hunter2")
    primary_conn = object()
    identities = {PRIMARY.user.lower(): primary_conn}
    lock = asyncio.Lock()

    result = await resolve_identity_conn(
        identities=identities,
        lock=lock,
        primary_config=PRIMARY,
        primary_conn=primary_conn,
        as_player="Wizard",  # case-varied
        connection_factory=_FakeConn,
    )
    assert result is primary_conn
    assert len(identities) == 1


@pytest.mark.asyncio
async def test_resolve_identity_conn_new_identity_created_once_and_cached(monkeypatch):
    monkeypatch.setenv("MOO_USER_AMADAN", "amadan")
    monkeypatch.setenv("MOO_PASS_AMADAN", "idiot")
    identities: dict = {PRIMARY.user.lower(): object()}
    lock = asyncio.Lock()

    first = await resolve_identity_conn(
        identities=identities,
        lock=lock,
        primary_config=PRIMARY,
        primary_conn=identities[PRIMARY.user.lower()],
        as_player="amadan",
        connection_factory=_FakeConn,
    )
    assert isinstance(first, _FakeConn)
    assert first.connect_calls == 1
    assert identities["amadan"] is first

    second = await resolve_identity_conn(
        identities=identities,
        lock=lock,
        primary_config=PRIMARY,
        primary_conn=identities[PRIMARY.user.lower()],
        as_player="amadan",
        connection_factory=_FakeConn,
    )
    assert second is first
    assert first.connect_calls == 1  # not reconnected on cache hit
