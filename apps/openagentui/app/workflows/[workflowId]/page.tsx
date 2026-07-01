"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import type { Connection, Edge, Node } from "@xyflow/react";
import { api, type ToolCatalog } from "@/lib/api";
import type {
  NodeData,
  NodeExecutionResult,
  NodeType,
  Workflow,
  WorkflowExecution,
  WorkflowNode as WFNode,
} from "@/lib/workflow/types";
import { Canvas } from "@/components/workflow-builder/Canvas";
import { NodeConfigPanel } from "@/components/workflow-builder/NodeConfigPanel";
import { NodePalette } from "@/components/workflow-builder/NodePalette";
import { RunPanel } from "@/components/workflow-builder/RunPanel";
import { WorkflowToolbar } from "@/components/workflow-builder/WorkflowToolbar";
import type { WorkflowNodeData } from "@/components/workflow-builder/nodes/WorkflowNodeView";

function summarize(type: NodeType, data: NodeData): string {
  switch (type) {
    case "agent":
      return data.instructions || "(no instructions)";
    case "mcp":
      return data.mcpTool || "(no tool selected)";
    case "transform":
      return data.transformScript ? "Python script" : "(empty script)";
    case "http":
      return `${data.method || "GET"} ${data.url || ""}`;
    case "if-else":
    case "while":
      return data.condition || "(no condition)";
    case "set-state":
      return `${data.stateKey || "?"} = ${data.stateValue || ""}`;
    case "user-approval":
      return data.approvalMessage || "(no message)";
    default:
      return "";
  }
}

function toRFNode(node: WFNode): Node<WorkflowNodeData> {
  return {
    id: node.id,
    type: "workflowNode",
    position: node.position,
    data: { label: node.data.label || node.id, nodeType: node.type, summary: summarize(node.type, node.data) },
  };
}

function toRFEdge(edge: Workflow["edges"][number]): Edge {
  return { id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle ?? undefined, label: edge.label };
}

export default function WorkflowEditorPage() {
  const params = useParams<{ workflowId: string }>();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [rfNodes, setRfNodes] = useState<Node<WorkflowNodeData>[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ToolCatalog | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [execution, setExecution] = useState<WorkflowExecution | null>(null);
  const [nodeResults, setNodeResults] = useState<Record<string, NodeExecutionResult>>({});
  const stopStreamRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    api.getWorkflow(params.workflowId).then((wf) => {
      setWorkflow(wf);
      setRfNodes(wf.nodes.map(toRFNode));
      setRfEdges(wf.edges.map(toRFEdge));
    });
    api.catalog().then(setCatalog).catch(() => setCatalog(null));
    return () => stopStreamRef.current?.();
  }, [params.workflowId]);

  // Reflect live execution status onto the canvas nodes.
  useEffect(() => {
    setRfNodes((nodes) =>
      nodes.map((n) => ({ ...n, data: { ...n.data, status: nodeResults[n.id]?.status } }))
    );
  }, [nodeResults]);

  const selectedNode = useMemo(() => workflow?.nodes.find((n) => n.id === selectedId) || null, [workflow, selectedId]);

  function updateWorkflowNodes(updater: (nodes: WFNode[]) => WFNode[]) {
    setWorkflow((wf) => (wf ? { ...wf, nodes: updater(wf.nodes) } : wf));
    setDirty(true);
  }

  function handleAddNode(type: NodeType) {
    if (!workflow) return;
    const id = `${type}_${Math.random().toString(36).slice(2, 8)}`;
    const position = { x: 80 + workflow.nodes.length * 40, y: 120 + workflow.nodes.length * 90 };
    const newNode: WFNode = { id, type, position, data: { label: id } };
    updateWorkflowNodes((nodes) => [...nodes, newNode]);
    setRfNodes((nodes) => [...nodes, toRFNode(newNode)]);
  }

  function handleNodeDataChange(data: NodeData) {
    if (!selectedId) return;
    updateWorkflowNodes((nodes) => nodes.map((n) => (n.id === selectedId ? { ...n, data } : n)));
    setRfNodes((nodes) =>
      nodes.map((n) =>
        n.id === selectedId ? { ...n, data: { ...n.data, label: data.label || n.id, summary: summarize(n.data.nodeType, data) } } : n
      )
    );
  }

  function handleDeleteNode() {
    if (!selectedId) return;
    updateWorkflowNodes((nodes) => nodes.filter((n) => n.id !== selectedId));
    setRfNodes((nodes) => nodes.filter((n) => n.id !== selectedId));
    setRfEdges((edges) => edges.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }

  function handleConnect(connection: Connection) {
    const id = `e_${connection.source}_${connection.target}_${connection.sourceHandle || "default"}`;
    const edge: Edge = { id, source: connection.source, target: connection.target, sourceHandle: connection.sourceHandle };
    setRfEdges((edges) => [...edges, edge]);
    setWorkflow((wf) =>
      wf
        ? { ...wf, edges: [...wf.edges, { id, source: connection.source!, target: connection.target!, sourceHandle: connection.sourceHandle }] }
        : wf
    );
    setDirty(true);
  }

  async function handleSave() {
    if (!workflow) return;
    setSaving(true);
    try {
      const saved = await api.saveWorkflow(workflow);
      setWorkflow(saved);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleRun() {
    if (!workflow) return;
    if (dirty) await handleSave();

    const startNode = workflow.nodes.find((n) => n.type === "start");
    const inputs: Record<string, unknown> = {};
    for (const spec of startNode?.data.inputVariables || []) {
      const value = window.prompt(`Value for '${spec.name}'${spec.defaultValue ? ` [${spec.defaultValue}]` : ""}:`, spec.defaultValue || "");
      if (value) inputs[spec.name] = value;
    }

    setRunning(true);
    setNodeResults({});
    setExecution({
      id: "(streaming)",
      workflowId: workflow.id,
      status: "running",
      nodeResults: {},
      variables: {},
      startedAt: new Date().toISOString(),
    });

    stopStreamRef.current = api.streamExecution(workflow.id, inputs, (event, data) => {
      if (event === "node") {
        const result = data as NodeExecutionResult;
        setNodeResults((prev) => ({ ...prev, [result.nodeId]: result }));
      } else if (event === "done" || event === "error") {
        const finalExecution = event === "done" ? (data as WorkflowExecution) : null;
        if (finalExecution) {
          setExecution(finalExecution);
          setNodeResults(finalExecution.nodeResults);
        }
        setRunning(false);
      }
    });
  }

  async function resolveApproval(decision: "approve" | "reject") {
    if (!execution) return;
    const updated = decision === "approve" ? await api.approve(execution.id) : await api.reject(execution.id);
    setExecution(updated);
    setNodeResults(updated.nodeResults);
  }

  if (!workflow) {
    return <p className="oaui-empty">Loading workflow…</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <WorkflowToolbar
        name={workflow.name}
        description={workflow.description}
        saving={saving}
        running={running}
        dirty={dirty}
        onNameChange={(v) => {
          setWorkflow({ ...workflow, name: v });
          setDirty(true);
        }}
        onDescriptionChange={(v) => {
          setWorkflow({ ...workflow, description: v });
          setDirty(true);
        }}
        onSave={handleSave}
        onRun={handleRun}
      />
      <div className="oaui-editor">
        <NodePalette onAdd={handleAddNode} />
        <Canvas
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={setRfNodes}
          onEdgesChange={setRfEdges}
          onConnect={handleConnect}
          onNodeClick={setSelectedId}
        />
        {selectedNode ? (
          <NodeConfigPanel
            nodeId={selectedNode.id}
            nodeType={selectedNode.type}
            data={selectedNode.data}
            catalog={catalog}
            onChange={handleNodeDataChange}
            onDelete={handleDeleteNode}
          />
        ) : (
          <aside className="oaui-panel oaui-panel-right">
            <div className="oaui-panel-title">Select a node</div>
            <p className="oaui-card-desc">Click a node on the canvas to edit it.</p>
          </aside>
        )}
      </div>
      {execution && (
        <RunPanel
          execution={{ ...execution, nodeResults }}
          log={Object.values(nodeResults)}
          onApprove={() => resolveApproval("approve")}
          onReject={() => resolveApproval("reject")}
          onClose={() => setExecution(null)}
        />
      )}
    </div>
  );
}
