"use client";

import type { NodeExecutionResult, WorkflowExecution } from "@/lib/workflow/types";

interface RunPanelProps {
  execution: WorkflowExecution | null;
  log: NodeExecutionResult[];
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}

export function RunPanel({ execution, log, onApprove, onReject, onClose }: RunPanelProps) {
  if (!execution) return null;

  return (
    <div
      style={{
        borderTop: "1px solid var(--oaui-border)",
        background: "var(--oaui-bg-elevated)",
        padding: "0.75rem 1rem",
        maxHeight: "40vh",
        overflowY: "auto",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
        <strong style={{ fontSize: "0.85rem" }}>
          Execution {execution.id} — {execution.status}
        </strong>
        <button className="oaui-btn" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="oaui-log">
        {log.map((r) => (
          <div key={r.nodeId} className={`oaui-log-line ${r.status}`}>
            {r.status === "completed" && "✅"}
            {r.status === "failed" && "❌"}
            {r.status === "pending-approval" && "⏸️"}
            {r.status === "running" && "▶️"} {r.nodeId}: {r.status}
            {r.error ? ` — ${r.error}` : ""}
          </div>
        ))}
      </div>

      {execution.status === "waiting-approval" && (
        <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem" }}>
          <span style={{ fontSize: "0.8rem", color: "var(--oaui-text-dim)" }}>Approval required to continue:</span>
          <button className="oaui-btn oaui-btn-primary" onClick={onApprove}>
            Approve
          </button>
          <button className="oaui-btn oaui-btn-danger" onClick={onReject}>
            Reject
          </button>
        </div>
      )}

      {execution.status === "completed" && (
        <pre style={{ marginTop: "0.75rem", fontSize: "0.72rem", color: "var(--oaui-text-dim)", whiteSpace: "pre-wrap" }}>
          {JSON.stringify(execution.variables, null, 2)}
        </pre>
      )}
    </div>
  );
}
