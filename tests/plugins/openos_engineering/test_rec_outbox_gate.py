"""Tests for mesh outbox file fallback gate."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from plugins.openos_engineering import rec_outbox_common as common
from plugins.openos_engineering import rec_outbox as facade


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
