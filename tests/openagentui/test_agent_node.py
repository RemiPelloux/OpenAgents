"""Tests for the ``agent`` node executor (delegates to run_agent.AIAgent)."""

import sys
import types

from openagentui.nodes import agent_node
from openagentui.nodes.base import NodeContext
from openagentui.schema import WorkflowExecution, WorkflowNode


def _ctx(data: dict, variables: dict | None = None) -> NodeContext:
    node = WorkflowNode(id="n1", type="agent", data=data)
    execution = WorkflowExecution(id="exec1", workflow_id="wf1", variables=dict(variables or {}))
    return NodeContext(node=node, execution=execution)


def _install_fake_run_agent(monkeypatch, messages):
    fake_module = types.ModuleType("run_agent")

    class FakeAIAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_conversation(self, instructions, task_id=None):
            return {"messages": messages}

    fake_module.AIAgent = FakeAIAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_module)


def test_agent_missing_instructions_fails():
    ctx = _ctx({})
    result = agent_node.execute(ctx)
    assert result.status == "failed"
    assert "instructions" in result.error


def test_agent_returns_final_assistant_text(monkeypatch):
    _install_fake_run_agent(
        monkeypatch,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"},
        ],
    )
    ctx = _ctx({"instructions": "Say hi", "outputField": "reply"})
    result = agent_node.execute(ctx)
    assert result.status == "completed"
    assert result.output == "hello there"
    assert ctx.execution.variables["reply"] == "hello there"


def test_agent_coerces_json_output_format(monkeypatch):
    _install_fake_run_agent(
        monkeypatch,
        [{"role": "assistant", "content": '{"score": 9}'}],
    )
    ctx = _ctx({"instructions": "Score this", "outputFormat": "json"})
    result = agent_node.execute(ctx)
    assert result.status == "completed"
    assert result.output == {"score": 9}


def test_agent_extracts_text_from_content_blocks(monkeypatch):
    _install_fake_run_agent(
        monkeypatch,
        [{"role": "assistant", "content": [{"type": "text", "text": "block text"}]}],
    )
    ctx = _ctx({"instructions": "Go"})
    result = agent_node.execute(ctx)
    assert result.output == "block text"


def test_agent_run_conversation_exception_fails(monkeypatch):
    fake_module = types.ModuleType("run_agent")

    class FakeAIAgent:
        def __init__(self, **kwargs):
            pass

        def run_conversation(self, instructions, task_id=None):
            raise RuntimeError("model unavailable")

    fake_module.AIAgent = FakeAIAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_module)

    ctx = _ctx({"instructions": "Go"})
    result = agent_node.execute(ctx)
    assert result.status == "failed"
    assert "model unavailable" in result.error
