"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export interface ExecutionSummary {
  id: string;
  workflowId: string;
  status: string;
  startedAt: string;
  completedAt?: string | null;
  error?: string | null;
}

interface ExecutionHistoryPanelProps {
  workflowId: string;
  refreshKey: number;
  onSelect: (executionId: string) => void;
}

export function ExecutionHistoryPanel({ workflowId, refreshKey, onSelect }: ExecutionHistoryPanelProps) {
  const [rows, setRows] = useState<ExecutionSummary[] | null>(null);

  useEffect(() => {
    api
      .listExecutionSummaries(workflowId)
      .then(setRows)
      .catch(() => setRows([]));
  }, [workflowId, refreshKey]);

  return (
    <aside className="oaui-panel oaui-panel-right" style={{ maxWidth: "280px" }}>
      <div className="oaui-panel-title">Run history</div>
      {rows === null && <p className="oaui-card-desc">Loading…</p>}
      {rows !== null && rows.length === 0 && <p className="oaui-card-desc">No runs yet.</p>}
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {rows?.map((row) => (
          <li key={row.id} style={{ marginBottom: "0.35rem" }}>
            <button
              type="button"
              className="oaui-btn"
              style={{ width: "100%", textAlign: "left", fontSize: "0.75rem" }}
              onClick={() => onSelect(row.id)}
            >
              <span>{row.status}</span>
              <span className="oaui-card-desc" style={{ display: "block" }}>
                {row.startedAt?.slice(0, 19) || row.id}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
