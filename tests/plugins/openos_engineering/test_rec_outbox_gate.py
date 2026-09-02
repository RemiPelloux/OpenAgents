"""Tests for mesh outbox file fallback gate."""

from __future__ import annotations

import os
import io
import json
import sys
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from plugins.openos_engineering import rec_outbox_common as common
from plugins.openos_engineering import rec_outbox as facade
from plugins.openos_engineering import rec_client
from plugins.openos_engineering import rec_pg_outbox
from openagentui import rec_outbox as ui_outbox


def test_file_outbox_requires_dev_flag() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert common.file_outbox_allowed() is False
        with pytest.raises(RuntimeError, match="MESH_OUTBOX_DATABASE_URL"):
            common.require_mesh_outbox_configured()


def test_file_outbox_allowed_with_dev_flag() -> None:
    with patch.dict(os.environ, {"OPENOS_DEV_OUTBOX_FILE": "1"}, clear=True):
        assert common.file_outbox_allowed() is True
        common.require_mesh_outbox_configured()


def test_enqueue_uses_dev_file_when_flag_set(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENOS_DEV_OUTBOX_FILE", "1")
    monkeypatch.delenv("MESH_OUTBOX_DATABASE_URL", raising=False)
    called: list[dict] = []

    def fake_enqueue(body: dict) -> None:
        called.append(body)

    monkeypatch.setattr(facade, "enqueue_file_outbox", fake_enqueue)
    facade.enqueue_rec_event({"event_type": "test.event", "correlation_id": "c1"})
    assert len(called) == 1


def test_rec_event_is_enqueued_while_openrec_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("OPENREC_URL", raising=False)
    enqueued: list[dict] = []
    monkeypatch.setattr(rec_client, "enqueue_rec_event", enqueued.append)
    monkeypatch.setattr(rec_client, "drain_rec_outbox", lambda max_items: 0)

    rec_client.emit_rec_event(
        "run.completed",
        {"status": "completed"},
        correlation_id="corr-1",
        agent_run_id="run-1",
    )

    assert len(enqueued) == 1
    assert enqueued[0]["type"] == "run.completed"
    assert enqueued[0]["agent_run_id"] == "run-1"


def test_pg_outbox_recovers_stale_claims_before_claiming(monkeypatch) -> None:
    statements: list[str] = []

    class Cursor:
        def execute(self, statement, _params=()) -> None:
            statements.append(" ".join(statement.split()))

        def fetchall(self) -> list:
            return []

    class Connection:
        autocommit = True

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def cursor(self):
            return nullcontext(Cursor())

        def commit(self) -> None:
            return None

    fake_psycopg = type("FakePsycopg", (), {"connect": staticmethod(lambda _url: Connection())})
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setenv("OPENAGENTS_OUTBOX_DATABASE_URL", "postgres://outbox")
    monkeypatch.setenv("OPENREC_URL", "http://openrec:3030")

    assert rec_pg_outbox.drain_pg_outbox() == 0
    assert "status = CASE WHEN attempts >= max_attempts THEN 'dead_letter'" in statements[0]
    assert "AND status = 'pending'" in statements[1]
    assert "OR (status = 'processing'" not in statements[1]


def test_openrec_oauth_token_is_bound_to_audience_scope_and_org() -> None:
    common._OAUTH_TOKEN = None
    response = io.BytesIO(
        json.dumps({"access_token": "mesh-token", "expires_in": 300}).encode()
    )
    with (
        patch.dict(
            os.environ,
            {
                "PLATFORM_AUTH_URL": "http://openbrain-api:3001",
                "PLATFORM_AUTH_CLIENT_ID": "openrec-mesh",
                "PLATFORM_AUTH_CLIENT_SECRET": "secret",
                "OPENREC_ORG_ID": "org-1",
            },
            clear=True,
        ),
        patch("urllib.request.urlopen", return_value=response) as urlopen,
    ):
        assert common.oauth_authorization() == "Bearer mesh-token"
    request = urlopen.call_args.args[0]
    body = request.data.decode()
    assert request.full_url == "http://openbrain-api:3001/oauth/token"
    assert "audience=openrec" in body
    assert "organization_id=org-1" in body
    assert "scope=rec%3Aread+rec%3Awrite" in body


def test_post_rec_event_sends_the_openbrain_bearer_token() -> None:
    response = type(
        "Response",
        (),
        {
            "status": 202,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *_args: None,
        },
    )()
    with (
        patch.object(common, "wrap_rec_event", side_effect=lambda event: event),
        patch.object(common, "oauth_authorization", return_value="Bearer mesh-token"),
        patch("urllib.request.urlopen", return_value=response) as urlopen,
    ):
        assert common.post_rec_event("http://openrec:3030", {"id": "event-1"}) is True

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://openrec:3030/v1/events"
    assert request.get_header("Authorization") == "Bearer mesh-token"


def test_rec_outbox_never_uses_dev_key_without_explicit_opt_in() -> None:
    event = {"correlation_id": "corr-1"}
    with (
        patch.dict(
            os.environ,
            {"OPENCONTRACT_URL": "http://opencontract", "OPENCONTRACT_DEV_KEYS": "0"},
            clear=True,
        ),
        patch("urllib.request.urlopen") as urlopen,
    ):
        assert common.wrap_rec_event(event) is event
        urlopen.assert_not_called()


def test_rec_outbox_fails_closed_when_signature_is_required() -> None:
    with patch.dict(
        os.environ,
        {
            "OPENCONTRACT_URL": "http://opencontract",
            "OPENCONTRACT_DEV_KEYS": "0",
            "OPENCONTRACT_REQUIRE_SIGNATURE": "1",
        },
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="signing key required"):
            common.wrap_rec_event({"correlation_id": "corr-1"})


def test_ui_outbox_sends_the_openbrain_bearer_token() -> None:
    response = type(
        "Response",
        (),
        {
            "status": 202,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *_args: None,
        },
    )()
    with (
        patch.object(ui_outbox, "_wrap_event", side_effect=lambda event: event),
        patch.object(
            ui_outbox, "oauth_authorization", return_value="Bearer mesh-token"
        ),
        patch("urllib.request.urlopen", return_value=response) as urlopen,
    ):
        assert ui_outbox._post_event("http://openrec:3030", {"id": "event-1"}) is True

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://openrec:3030/v1/events"
    assert request.get_header("Authorization") == "Bearer mesh-token"
