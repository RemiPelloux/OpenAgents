/**
 * Workflow graph types — mirrors OpenAgents/openagentui/schema.py exactly
 * so JSON round-trips between this UI and the Python engine unchanged.
 */

export type NodeType =
  | "start"
  | "agent"
  | "mcp"
  | "transform"
  | "if-else"
  | "while"
  | "user-approval"
  | "set-state"
  | "http"
  | "note"
  | "end"
  | "codex"
  | "workflow"
  | "arcade"
  | "guardrails";

export const NODE_TYPES: NodeType[] = [
  "start",
  "agent",
  "mcp",
  "codex",
  "workflow",
  "transform",
  "if-else",
  "while",
  "user-approval",
  "set-state",
  "http",
  "note",
  "end",
];

/** Node type -> which outgoing-edge handles it can have (sourceHandle values). */
export const NODE_BRANCHES: Partial<Record<NodeType, string[]>> = {
  "if-else": ["true", "false"],
  while: ["loop", "exit"],
  "user-approval": ["approved"],
};

export interface InputVariableSpec {
  name: string;
  required?: boolean;
  defaultValue?: string;
}

export interface NodeData {
  label?: string;
  // agent
  instructions?: string;
  model?: string;
  tools?: string[];
  outputFormat?: "text" | "json";
  maxIterations?: number;
  // mcp (deterministic tool call)
  mcpTool?: string;
  mcpParams?: Record<string, unknown>;
  // shared output binding
  outputField?: string;
  // transform
  transformScript?: string;
  // http
  url?: string;
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  // if-else / while
  condition?: string;
  // set-state
  stateKey?: string;
  stateValue?: string;
  // user-approval
  approvalMessage?: string;
  // codex
  prompt?: string;
  sandbox?: string;
  fullAuto?: boolean;
  timeoutSeconds?: number;
  cwd?: string;
  // sub-workflow
  subWorkflowId?: string;
  inputs?: Record<string, unknown>;
  // start
  inputVariables?: InputVariableSpec[];
  // end
  outputMapping?: Record<string, string>;
  [key: string]: unknown;
}

export interface WorkflowNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: NodeData;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  label?: string;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  nodeCount: number;
  edgeCount: number;
  createdAt: string;
  updatedAt: string;
  isTemplate: boolean;
}

export interface TemplateCard {
  id: string;
  name: string;
  description: string;
  tags: string[];
  nodeCount: number;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  createdAt: string;
  updatedAt: string;
  isTemplate: boolean;
}

export type NodeExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "pending-approval"
  | "skipped";

export interface NodeExecutionResult {
  nodeId: string;
  status: NodeExecutionStatus;
  input?: unknown;
  output?: unknown;
  error?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
}

export type ExecutionStatus = "running" | "completed" | "failed" | "paused" | "waiting-approval";

export interface WorkflowExecution {
  id: string;
  workflowId: string;
  status: ExecutionStatus;
  currentNodeId?: string | null;
  nodeResults: Record<string, NodeExecutionResult>;
  variables: Record<string, unknown>;
  startedAt: string;
  completedAt?: string | null;
  error?: string | null;
  pendingApprovalId?: string | null;
}

export function emptyWorkflow(id: string): Workflow {
  const now = new Date().toISOString();
  return {
    id,
    name: "Untitled workflow",
    description: "",
    category: "",
    tags: [],
    nodes: [
      { id: "start", type: "start", position: { x: 0, y: 0 }, data: { label: "Start", inputVariables: [] } },
    ],
    edges: [],
    createdAt: now,
    updatedAt: now,
    isTemplate: false,
  };
}
