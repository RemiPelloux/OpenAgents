"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import type { Connection, Edge, Node } from "@xyflow/react";
import { api, type ToolCatalog } from "@/lib/api";
import type {
  InputVariableSpec,
  NodeData,
  NodeExecutionResult,
  NodeType,
  Workflow,
  WorkflowExecution,
  WorkflowNode as WFNode,
  WorkflowSummary,
} from "@/lib/workflow/types";
import { Canvas } from "@/components/workflow-builder/Canvas";
import { ExecutionHistoryPanel } from "@/components/workflow-builder/ExecutionHistoryPanel";
import { NodeConfigPanel } from "@/components/workflow-builder/NodeConfigPanel";
import { NodePalette } from "@/components/workflow-builder/NodePalette";
import { RunInputModal } from "@/components/workflow-builder/RunInputModal";
import { RunPanel } from "@/components/workflow-builder/RunPanel";
import { WorkflowToolbar } from "@/components/workflow-builder/WorkflowToolbar";
import type { WorkflowNodeData } from "@/components/workflow-builder/nodes/WorkflowNodeView";

function summarize(type: NodeType, data: NodeData): string {
  switch (type) {
    case "agent":
      return data.instructions || "(no instructions)";
    case "mcp":
      return data.mcpTool || "(no tool selected)";
    case "codex":
      return data.prompt || "(no prompt)";
    case "workflow":
      return data.subWorkflowId || "(no sub-workflow)";
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
  const router = useRouter();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [rfNodes, setRfNodes] = useState<Node<WorkflowNodeData>[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ToolCatalog | null>(null);
  const [workflowOptions, setWorkflowOptions] = useState<WorkflowSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runVariables, setRunVariables] = useState<InputVariableSpec[]>([]);
  const [execution, setExecution] = useState<WorkflowExecution | null>(null);
  const [nodeResults, setNodeResults] = useState<Record<string, NodeExecutionResult>>({});
  const stopStreamRef = useRef<(() => void) | null>(null);
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;

  useEffect(() => {
    setLoadError(null);
    api
      .editorBootstrap(params.workflowId)
      .then(async ({ workflow: wf, catalog: cat, workflows }) => {
        setWorkflow(wf);
        setRfNodes(wf.nodes.map(toRFNode));
        setRfEdges(wf.edges.map(toRFEdge));
        setCatalog(cat);
        setWorkflowOptions(workflows.filter((w) => w.id !== wf.id));
        const v = await api.validateWorkflow(wf.id);
        setValidationErrors(v.errors);
      })
      .catch((e) => setLoadError(String(e)));
    return () => stopStreamRef.current?.();
  }, [params.workflowId]);

  useEffect(() => {
    if (!dirty || !workflowRef.current) return;
    const timer = setTimeout(() => void persistWorkflow(workflowRef.current!), 800);
    return () => clearTimeout(timer);
  }, [dirty, workflow]);

  useEffect(() => {
    setRfNodes((nodes) =>
      nodes.map((n) => {
        const status = nodeResults[n.id]?.status;
        return status && n.data.status !== status ? { ...n, data: { ...n.data, status } } : n;
      })
    );
  }, [nodeResults]);

  const selectedNode = useMemo(() => workflow?.nodes.find((n) => n.id === selectedId) || null, [workflow, selectedId]);

  async function persistWorkflow(wf: Workflow) {
    setSaving(true);
    try {
      const saved = await api.saveWorkflow(wf);
      setWorkflow(saved);
      setDirty(false);
      const v = await api.validateWorkflow(saved.id);
      setValidationErrors(v.errors);
    } finally {
      setSaving(false);
    }
  }

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
    const edge: Edge = { id, source: connection.source!, target: connection.target!, sourceHandle: connection.sourceHandle };
    setRfEdges((edges) => [...edges, edge]);
    setWorkflow((wf) =>
      wf ? { ...wf, edges: [...wf.edges, { id, source: connection.source!, target: connection.target!, sourceHandle: connection.sourceHandle }] } : wf
    );
    setDirty(true);
  }

  function beginRun() {
    if (!workflow) return;
    const startNode = workflow.nodes.find((n) => n.type === "start");
    setRunVariables(startNode?.data.inputVariables || []);
    setRunModalOpen(true);
  }

  async function executeRun(inputs: Record<string, string>) {
    if (!workflow) return;
    setRunModalOpen(false);
    if (dirty) await persistWorkflow(workflow);
    const payload: Record<string, unknown> = { ...inputs };
    setRunning(true);
    setNodeResults({});
    setExecution({
      id: "(streaming)",
      workflowId: workflow.id,
      status: "running",
      nodeResults: {},
      variables: payload,
      startedAt: new Date().toISOString(),
    });
    stopStreamRef.current = api.streamExecution(workflow.id, payload, (event, data) => {
      if (event === "node") {
        const result = data as NodeExecutionResult;
        setNodeResults((prev) => ({ ...prev, [result.nodeId]: result }));
      } else if (event === "done" || event === "error") {
        if (event === "done") {
          const finalExecution = data as WorkflowExecution;
          setExecution(finalExecution);
          setNodeResults(finalExecution.nodeResults);
        }
        setRunning(false);
        setHistoryRefresh((k) => k + 1);
      }
    });
  }

  async function resolveApproval(decision: "approve" | "reject") {
    if (!execution) return;
    const updated = decision === "approve" ? await api.approve(execution.id) : await api.reject(execution.id);
    setExecution(updated);
    setNodeResults(updated.nodeResults);
    setHistoryRefresh((k) => k + 1);
  }

  async function handleDuplicate() {
    if (!workflow) return;
    const copy = await api.duplicateWorkflow(workflow.id);
    router.push(`/workflows/${copy.id}`);
  }

  async function handleExportYaml() {
    if (!workflow) return;
    const { yaml } = await api.exportYaml(workflow.id);
    await navigator.clipboard.writeText(yaml);
  }

  async function handleImportYaml() {
    if (!workflow) return;
    const yaml = window.prompt("Paste workflow YAML:");
    if (!yaml?.trim()) return;
    const saved = await api.importYaml(workflow.id, yaml);
    setWorkflow(saved);
    setRfNodes(saved.nodes.map(toRFNode));
    setRfEdges(saved.edges.map(toRFEdge));
    setDirty(false);
    const v = await api.validateWorkflow(saved.id);
    setValidationErrors(v.errors);
  }

  async function inspectExecution(executionId: string) {
    const ex = await api.getExecution(executionId);
    setExecution(ex);
    setNodeResults(ex.nodeResults);
  }

  if (loadError) {
    return (
      <p className="oaui-empty">
        Could not load workflow ({loadError}). Is <code>openagents dashboard</code> running?
      </p>
    );
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
        validationErrors={validationErrors}
        showHistory={showHistory}
        onNameChange={(v) => {
          setWorkflow({ ...workflow, name: v });
          setDirty(true);
        }}
        onDescriptionChange={(v) => {
          setWorkflow({ ...workflow, description: v });
          setDirty(true);
        }}
        onSave={() => void persistWorkflow(workflow)}
        onRun={beginRun}
        onDuplicate={() => void handleDuplicate()}
        onExportYaml={() => void handleExportYaml()}
        onImportYaml={() => void handleImportYaml()}
        onToggleHistory={() => setShowHistory((v) => !v)}
      />
      <div className="oaui-editor">
        <NodePalette onAdd={handleAddNode} />
        <Canvas nodes={rfNodes} edges={rfEdges} onNodesChange={setRfNodes} onEdgesChange={setRfEdges} onConnect={handleConnect} onNodeClick={setSelectedId} />
        {showHistory ? (
          <ExecutionHistoryPanel workflowId={workflow.id} refreshKey={historyRefresh} onSelect={(id) => void inspectExecution(id)} />
        ) : selectedNode ? (
          <NodeConfigPanel
            nodeId={selectedNode.id}
            nodeType={selectedNode.type}
            data={selectedNode.data}
            catalog={catalog}
            workflowOptions={workflowOptions}
            onChange={handleNodeDataChange}
            onDelete={handleDeleteNode}
          />
        ) : (
          <aside className="oaui-panel oaui-panel-right">
            <div className="oaui-panel-title">Select a node</div>
            <p className="oaui-card-desc">Click a node on the canvas to edit it, or open History.</p>
          </aside>
        )}
      </div>
      <RunInputModal open={runModalOpen} variables={runVariables} onCancel={() => setRunModalOpen(false)} onSubmit={(v) => void executeRun(v)} />
      {execution && (
        <RunPanel
          execution={{ ...execution, nodeResults }}
          log={Object.values(nodeResults)}
          onApprove={() => void resolveApproval("approve")}
          onReject={() => void resolveApproval("reject")}
          onClose={() => setExecution(null)}
        />
      )}
    </div>
  );
}
