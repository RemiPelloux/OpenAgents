"""Tests for openos_mesh contract_wrap."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from plugins.openos_mesh.contract_wrap import wrap_signed_hop


def test_wrap_signed_hop_returns_payload_when_no_contract_url():
    with patch.dict(os.environ, {}, clear=True):
        payload = {"company_name": "Acme"}
        result = wrap_signed_hop(
            contract_id="CC-W1-003",
            producer="OpenAgents",
            consumer="OpenCRM",
            payload=payload,
        )
    assert result == payload


def test_wrap_signed_hop_strict_raises_without_keys():
    with patch.dict(
        os.environ,
        {
            "OPENCONTRACT_URL": "http://localhost:3070",
            "OPENCONTRACT_REQUIRE_SIGNATURE": "1",
        },
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="signing required"):
            wrap_signed_hop(
                contract_id="CC-W1-003",
                producer="OpenAgents",
                consumer="OpenCRM",
                payload={"org_id": "o1"},
            )


def test_wrap_signed_hop_uses_opencontract_api():
    envelope = {
        "contract_id": "CC-W1-004",
        "producer": "OpenTeam",
        "consumer": "OpenCRM",
        "payload": {"company_name": "Decathlon"},
        "signature": {"signer_id": "OpenTeam", "algorithm": "ed25519", "value": "abc"},
    }
    response = MagicMock()
    response.read.return_value = json.dumps({"envelope": envelope}).encode()
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)

    with (
        patch.dict(
            os.environ,
            {
                "OPENCONTRACT_URL": "http://localhost:3070",
                "OPENCONTRACT_DEV_KEYS": "1",
            },
            clear=True,
        ),
        patch("urllib.request.urlopen", return_value=response),
    ):
        result = wrap_signed_hop(
            contract_id="CC-W1-004",
            producer="OpenTeam",
            consumer="OpenCRM",
            payload={"company_name": "Decathlon"},
            signer_id="OpenTeam",
        )

    assert result["contract_id"] == "CC-W1-004"
    assert result["producer"] == "OpenTeam"


def test_opencrm_client_propose_crm_update_wraps_when_configured():
    from plugins.opencrm_sales import opencrm_client

    with (
        patch(
            "plugins.opencrm_sales.opencrm_client.wrap_signed_hop",
            return_value={"contract_id": "CC-W1-003", "payload": {"org_id": "o1"}},
        ) as wrap_mock,
        patch(
            "plugins.opencrm_sales.opencrm_client._post",
            return_value={
                "contract_id": "CC-W1-003",
                "payload": {"staged_update_id": "s1"},
            },
        ) as post_mock,
    ):
        opencrm_client.propose_crm_update(
            "opportunity",
            "opp-1",
            {"next_step": "call back"},
            org_id="org-1",
            correlation_id="corr-1",
        )

    wrap_mock.assert_called_once()
    assert wrap_mock.call_args.kwargs["contract_id"] == "CC-W1-003"
    post_mock.assert_called_once()
