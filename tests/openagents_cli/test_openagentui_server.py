"""FastAPI TestClient coverage for the OpenAgentUI REST routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openagentui import store
from openagents_cli.openagentui_server import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _linear_workflow_body(id_="wf_api"):
    return {
        "id": id_,
        "name": "API Flow",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }


def test_list_workflows_empty():
    client = _client()
    resp = client.get("/api/openagentui/workflows")
    assert resp.status_code == 200
    assert resp.json() == {"workflows": []}


def test_list_workflows_returns_summaries_not_full_graph():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_summary"))
    resp = client.get("/api/openagentui/workflows")
    assert resp.status_code == 200
    row = next(w for w in resp.json()["workflows"] if w["id"] == "wf_summary")
    assert row["nodeCount"] == 2
    assert "nodes" not in row


def test_home_bootstrap_single_payload():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_home"))
    resp = client.get("/api/openagentui/home")
    assert resp.status_code == 200
    body = resp.json()
    assert any(w["id"] == "wf_home" for w in body["workflows"])
    assert isinstance(body["templates"], list)
    if body["templates"]:
        card = body["templates"][0]
        assert "nodeCount" in card
        assert "nodes" not in card


def test_editor_bootstrap_includes_workflow_and_catalog():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_editor"))
    resp = client.get("/api/openagentui/workflows/wf_editor/editor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"]["id"] == "wf_editor"
    assert set(body["catalog"].keys()) == {"toolsets", "tools", "mcpServers"}
    assert isinstance(body["workflows"], list)


def test_validate_workflow_route():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_val"))
    resp = client.post("/api/openagentui/workflows/wf_val/validate")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_duplicate_workflow_route():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_dup"))
    resp = client.post("/api/openagentui/workflows/wf_dup/duplicate")
    assert resp.status_code == 200
    assert resp.json()["id"] != "wf_dup"
    assert "copy" in resp.json()["name"].lower()


def test_create_and_get_workflow():
    client = _client()
    created = client.post("/api/openagentui/workflows", json=_linear_workflow_body())
    assert created.status_code == 200
    workflow_id = created.json()["id"]

    fetched = client.get(f"/api/openagentui/workflows/{workflow_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "API Flow"


def test_get_unknown_workflow_404():
    client = _client()
    resp = client.get("/api/openagentui/workflows/does-not-exist")
    assert resp.status_code == 404


def test_update_workflow_preserves_created_at():
    client = _client()
    body = _linear_workflow_body("wf_update")
    client.post("/api/openagentui/workflows", json=body)
    original = store.get_workflow("wf_update")

    updated_body = dict(body)
    updated_body["name"] = "Renamed Flow"
    resp = client.put("/api/openagentui/workflows/wf_update", json=updated_body)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Flow"
    assert resp.json()["createdAt"] == original.created_at


def test_delete_workflow():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_delete"))
    resp = client.delete("/api/openagentui/workflows/wf_delete")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert client.get("/api/openagentui/workflows/wf_delete").status_code == 404


def test_delete_unknown_workflow_404():
    client = _client()
    resp = client.delete("/api/openagentui/workflows/does-not-exist")
    assert resp.status_code == 404


def test_list_templates_includes_flagship_scenario():
    client = _client()
    resp = client.get("/api/openagentui/templates")
    assert resp.status_code == 200
    templates = resp.json()["templates"]
    assert any(
        "tiktok" in (t.get("id") or "").lower()
        or "prospection" in (t.get("name") or "").lower()
        for t in templates
    )


def test_install_template_creates_new_workflow():
    client = _client()
    templates = client.get("/api/openagentui/templates").json()["templates"]
    assert templates, "expected at least one bundled template"
    template_id = templates[0]["id"]

    resp = client.post(f"/api/openagentui/templates/{template_id}/install")
    assert resp.status_code == 200
    installed = resp.json()
    assert installed["id"] != template_id
    assert installed["isTemplate"] is False


def test_install_unknown_template_404():
    client = _client()
    resp = client.post("/api/openagentui/templates/tpl_does_not_exist/install")
    assert resp.status_code == 404


def test_catalog_route_has_expected_sections():
    client = _client()
    resp = client.get("/api/openagentui/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"toolsets", "tools", "mcpServers"}


def test_run_workflow_route_completes():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_run"))
    resp = client.post("/api/openagentui/workflows/wf_run/run", json={"inputs": {}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_run_workflow_route_unknown_workflow_404():
    client = _client()
    resp = client.post("/api/openagentui/workflows/does-not-exist/run", json={})
    assert resp.status_code == 404


def test_list_executions_route():
    client = _client()
    client.post(
        "/api/openagentui/workflows", json=_linear_workflow_body("wf_exec_list")
    )
    client.post("/api/openagentui/workflows/wf_exec_list/run", json={})
    resp = client.get("/api/openagentui/workflows/wf_exec_list/executions")
    assert resp.status_code == 200
    assert len(resp.json()["executions"]) == 1


def test_get_execution_route():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_exec_get"))
    run_resp = client.post("/api/openagentui/workflows/wf_exec_get/run", json={})
    execution_id = run_resp.json()["id"]

    resp = client.get(f"/api/openagentui/executions/{execution_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == execution_id


def test_get_unknown_execution_404():
    client = _client()
    resp = client.get("/api/openagentui/executions/exec-does-not-exist")
    assert resp.status_code == 404


def _approval_workflow_body(id_="wf_api_approval"):
    return {
        "id": id_,
        "name": "API Approval Flow",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "gate",
                "type": "user-approval",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "gate"},
            {"id": "e2", "source": "gate", "target": "end", "sourceHandle": "approved"},
        ],
    }


def test_approve_execution_route_resumes_workflow():
    client = _client()
    client.post("/api/openagentui/workflows", json=_approval_workflow_body())
    run_resp = client.post("/api/openagentui/workflows/wf_api_approval/run", json={})
    execution_id = run_resp.json()["id"]
    assert run_resp.json()["status"] == "waiting-approval"

    resp = client.post(f"/api/openagentui/executions/{execution_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_reject_execution_route_fails_workflow():
    client = _client()
    client.post(
        "/api/openagentui/workflows", json=_approval_workflow_body("wf_api_approval2")
    )
    run_resp = client.post("/api/openagentui/workflows/wf_api_approval2/run", json={})
    execution_id = run_resp.json()["id"]

    resp = client.post(f"/api/openagentui/executions/{execution_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


def test_approve_unknown_execution_400():
    client = _client()
    resp = client.post("/api/openagentui/executions/exec-missing/approve")
    assert resp.status_code == 400


def test_execute_stream_route_emits_node_and_done_events():
    client = _client()
    client.post("/api/openagentui/workflows", json=_linear_workflow_body("wf_stream"))
    with client.stream(
        "POST", "/api/openagentui/workflows/wf_stream/execute-stream", json={}
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: node" in body
    assert "event: done" in body


def test_create_workflow_from_yaml_route():
    client = _client()
    yaml_text = """
id: wf_yaml_api
name: YAML API Flow
nodes:
  - id: start
    type: start
    data: {}
  - id: end
    type: end
    data: {}
edges:
  - id: e1
    source: start
    target: end
"""
    resp = client.post("/api/openagentui/workflows/from-yaml", json={"yaml": yaml_text})
    assert resp.status_code == 200
    assert resp.json()["id"] == "wf_yaml_api"

    export = client.get("/api/openagentui/workflows/wf_yaml_api/yaml")
    assert export.status_code == 200
    assert "YAML API Flow" in export.json()["yaml"]
