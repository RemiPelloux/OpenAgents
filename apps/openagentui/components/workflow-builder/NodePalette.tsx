"use client";

import { NODE_TYPES, type NodeType } from "@/lib/workflow/types";

const LABELS: Record<NodeType, string> = {
  start: "Start",
  agent: "Agent (LLM turn)",
  mcp: "Tool call",
  transform: "Transform (Python)",
  "if-else": "If / Else",
  while: "While loop",
  "user-approval": "User approval",
  "set-state": "Set variable",
  http: "HTTP request",
  note: "Note",
  end: "End",
  codex: "Codex CLI",
  workflow: "Sub-workflow",
  arcade: "Arcade (unsupported)",
  guardrails: "Guardrails (unsupported)",
};

interface NodePaletteProps {
  onAdd: (type: NodeType) => void;
}

export function NodePalette({ onAdd }: NodePaletteProps) {
  return (
    <aside className="oaui-panel">
      <div className="oaui-panel-title">Add node</div>
      {NODE_TYPES.map((type) => (
        <button key={type} className="oaui-palette-item" onClick={() => onAdd(type)} style={{ width: "100%" }}>
          {LABELS[type]}
        </button>
      ))}
    </aside>
  );
}
