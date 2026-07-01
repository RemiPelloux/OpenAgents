"use client";

interface WorkflowToolbarProps {
  name: string;
  description: string;
  saving: boolean;
  running: boolean;
  dirty: boolean;
  onNameChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onSave: () => void;
  onRun: () => void;
}

export function WorkflowToolbar({
  name,
  description,
  saving,
  running,
  dirty,
  onNameChange,
  onDescriptionChange,
  onSave,
  onRun,
}: WorkflowToolbarProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.6rem 1rem",
        borderBottom: "1px solid var(--oaui-border)",
        background: "var(--oaui-bg-elevated)",
      }}
    >
      <input
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
        placeholder="Workflow name"
        style={{
          background: "transparent",
          border: "none",
          color: "var(--oaui-text)",
          fontSize: "0.95rem",
          fontWeight: 600,
          outline: "none",
          width: "220px",
        }}
      />
      <input
        value={description}
        onChange={(e) => onDescriptionChange(e.target.value)}
        placeholder="Description"
        style={{
          background: "transparent",
          border: "none",
          color: "var(--oaui-text-dim)",
          fontSize: "0.8rem",
          outline: "none",
          flex: 1,
        }}
      />
      <button className="oaui-btn" onClick={onSave} disabled={saving || !dirty}>
        {saving ? "Saving…" : dirty ? "Save" : "Saved"}
      </button>
      <button className="oaui-btn oaui-btn-primary" onClick={onRun} disabled={running}>
        {running ? "Running…" : "▶ Run"}
      </button>
    </div>
  );
}
