"use client";

import { useState } from "react";
import type { NodeData, NodeType } from "@/lib/workflow/types";
import type { ToolCatalog } from "@/lib/api";

interface NodeConfigPanelProps {
  nodeId: string;
  nodeType: NodeType;
  data: NodeData;
  catalog: ToolCatalog | null;
  workflowOptions: { id: string; name: string }[];
  onChange: (data: NodeData) => void;
  onDelete: () => void;
}

function TextField({
  label,
  value,
  onChange,
  mono = true,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  mono?: boolean;
  placeholder?: string;
}) {
  return (
    <div className="oaui-field">
      <label>{label}</label>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={mono ? undefined : { fontFamily: "var(--oaui-font)" }}
      />
    </div>
  );
}

function TextAreaField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="oaui-field">
      <label>{label}</label>
      <textarea value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/** JSON-backed field: keeps the raw text while invalid, only commits parsed JSON once valid. */
function JsonField({ label, value, onChange }: { label: string; value: unknown; onChange: (v: unknown) => void }) {
  const [raw, setRaw] = useState(() => JSON.stringify(value ?? {}, null, 2));
  return (
    <div className="oaui-field">
      <label>{label} (JSON)</label>
      <textarea
        value={raw}
        onChange={(e) => {
          setRaw(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            // keep editing until valid JSON — don't clobber node data with garbage
          }
        }}
      />
    </div>
  );
}

export function NodeConfigPanel({
  nodeId,
  nodeType,
  data,
  catalog,
  workflowOptions,
  onChange,
  onDelete,
}: NodeConfigPanelProps) {
  const set = (patch: Partial<NodeData>) => onChange({ ...data, ...patch });

  return (
    <aside className="oaui-panel oaui-panel-right">
      <div className="oaui-panel-title">Node: {nodeId}</div>
      <TextField label="Label" value={data.label || ""} onChange={(v) => set({ label: v })} />

      {nodeType === "agent" && (
        <>
          <TextAreaField label="Instructions" value={data.instructions || ""} onChange={(v) => set({ instructions: v })} />
          <TextField
            label="Model (blank = Mistral/OpenAgents default)"
            placeholder="mistral-medium-latest"
            value={data.model || ""}
            onChange={(v) => set({ model: v })}
          />
          <div className="oaui-field">
            <label>Tools (toolsets, comma-separated)</label>
            <input
              value={(data.tools || []).join(", ")}
              onChange={(e) => set({ tools: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
            />
            {catalog && <div className="oaui-card-desc">Available: {catalog.toolsets.map((t) => t.id).join(", ")}</div>}
          </div>
          <TextField label="Output field (variable name)" value={data.outputField || ""} onChange={(v) => set({ outputField: v })} />
        </>
      )}

      {nodeType === "mcp" && (
        <>
          <div className="oaui-field">
            <label>Tool</label>
            <input list="oaui-tool-options" value={data.mcpTool || ""} onChange={(e) => set({ mcpTool: e.target.value })} />
            <datalist id="oaui-tool-options">
              {catalog?.tools.map((t) => (
                <option key={t.id} value={t.id} />
              ))}
            </datalist>
          </div>
          <JsonField label="Params" value={data.mcpParams || {}} onChange={(v) => set({ mcpParams: v as Record<string, unknown> })} />
          <TextField label="Output field (variable name)" value={data.outputField || ""} onChange={(v) => set({ outputField: v })} />
        </>
      )}

      {nodeType === "transform" && (
        <TextAreaField
          label="Python script (reads INPUT dict, print(json.dumps(...)))"
          value={data.transformScript || ""}
          onChange={(v) => set({ transformScript: v })}
        />
      )}

      {nodeType === "http" && (
        <>
          <TextField label="URL" value={data.url || ""} onChange={(v) => set({ url: v })} />
          <TextField label="Method" value={data.method || "GET"} onChange={(v) => set({ method: v })} />
          <JsonField label="Headers" value={data.headers || {}} onChange={(v) => set({ headers: v as Record<string, string> })} />
          <JsonField label="Body" value={data.body || {}} onChange={(v) => set({ body: v })} />
        </>
      )}

      {(nodeType === "if-else" || nodeType === "while") && (
        <TextField label="Condition (Python-like expression)" value={data.condition || ""} onChange={(v) => set({ condition: v })} />
      )}

      {nodeType === "set-state" && (
        <>
          <TextField label="Variable name" value={data.stateKey || ""} onChange={(v) => set({ stateKey: v })} />
          <TextField label="Value" value={data.stateValue || ""} onChange={(v) => set({ stateValue: v })} />
        </>
      )}

      {nodeType === "codex" && (
        <>
          <TextAreaField label="Prompt" value={data.prompt || data.instructions || ""} onChange={(v) => set({ prompt: v })} />
          <TextField label="Working directory (optional)" value={data.cwd || ""} onChange={(v) => set({ cwd: v })} />
          <TextField label="Output field (variable name)" value={data.outputField || ""} onChange={(v) => set({ outputField: v })} />
        </>
      )}

      {nodeType === "workflow" && (
        <>
          <div className="oaui-field">
            <label>Sub-workflow</label>
            <input
              list="oaui-workflow-options"
              value={data.subWorkflowId || ""}
              onChange={(e) => set({ subWorkflowId: e.target.value })}
            />
            <datalist id="oaui-workflow-options">
              {workflowOptions.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </datalist>
          </div>
          <JsonField label="Inputs" value={data.inputs || {}} onChange={(v) => set({ inputs: v as Record<string, unknown> })} />
          <TextField label="Output field (variable name)" value={data.outputField || ""} onChange={(v) => set({ outputField: v })} />
        </>
      )}

      {nodeType === "user-approval" && (
        <TextAreaField label="Approval message" value={data.approvalMessage || ""} onChange={(v) => set({ approvalMessage: v })} />
      )}

      {nodeType === "start" && (
        <JsonField label="Input variables" value={data.inputVariables || []} onChange={(v) => set({ inputVariables: v as NodeData["inputVariables"] })} />
      )}

      {nodeType === "end" && (
        <JsonField label="Output mapping" value={data.outputMapping || {}} onChange={(v) => set({ outputMapping: v as Record<string, string> })} />
      )}

      {nodeType === "note" && (
        <TextAreaField label="Note text" value={(data.text as string) || ""} onChange={(v) => set({ text: v })} />
      )}

      {nodeId !== "start" && (
        <button className="oaui-btn oaui-btn-danger" style={{ marginTop: "0.5rem", width: "100%" }} onClick={onDelete}>
          Delete node
        </button>
      )}
    </aside>
  );
}
