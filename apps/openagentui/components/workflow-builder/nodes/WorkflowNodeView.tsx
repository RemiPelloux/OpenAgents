"use client";

import { Handle, Position, type NodeTypes } from "@xyflow/react";
import { NODE_BRANCHES } from "@/lib/workflow/types";
import type { NodeExecutionStatus, NodeType } from "@/lib/workflow/types";

const NODE_ICONS: Record<NodeType, string> = {
  start: "▶",
  agent: "🤖",
  mcp: "🔧",
  transform: "🐍",
  "if-else": "◇",
  while: "↻",
  "user-approval": "🖐",
  "set-state": "＝",
  http: "🌐",
  note: "📝",
  end: "■",
  codex: "⌘",
  workflow: "⎇",
  arcade: "🎮",
  guardrails: "🛡",
};

export interface WorkflowNodeData {
  label: string;
  nodeType: NodeType;
  summary: string;
  status?: NodeExecutionStatus;
  [key: string]: unknown;
}

interface WorkflowNodeViewProps {
  data: WorkflowNodeData;
  selected?: boolean;
}

// react-flow's `NodeTypes` map expects `ComponentType<NodeProps>` where
// `NodeProps.data` is generically typed per-node-type at the call site, not
// per-component — a plain narrower prop type here is the documented pattern
// (see xyflow docs "custom nodes"), so the cast below at export is the
// verified boundary between react-flow's generic node data and this app's
// concrete `WorkflowNodeData` shape.
export function WorkflowNodeView({ data, selected }: WorkflowNodeViewProps) {
  const branches = NODE_BRANCHES[data.nodeType];
  const statusClass = data.status ? `status-${data.status}` : "";

  return (
    <div className={`oaui-node ${selected ? "selected" : ""} ${statusClass}`}>
      {data.nodeType !== "start" && <Handle type="target" position={Position.Top} />}
      <div className="oaui-node-header">
        <span>{NODE_ICONS[data.nodeType] || "•"}</span>
        <span>{data.label}</span>
      </div>
      {data.summary && <div className="oaui-node-body">{data.summary}</div>}
      {data.nodeType !== "end" && !branches && <Handle type="source" position={Position.Bottom} />}
      {branches?.map((branch, i) => (
        <Handle
          key={branch}
          id={branch}
          type="source"
          position={Position.Bottom}
          style={{ left: `${((i + 1) / (branches.length + 1)) * 100}%` }}
        >
          <span style={{ position: "absolute", top: 6, fontSize: "0.6rem", whiteSpace: "nowrap" }}>{branch}</span>
        </Handle>
      ))}
    </div>
  );
}

export const NODE_RENDERER_TYPES = { workflowNode: WorkflowNodeView } as unknown as NodeTypes;
