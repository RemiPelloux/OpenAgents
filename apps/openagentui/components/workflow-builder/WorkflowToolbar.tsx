"use client";

interface WorkflowToolbarProps {
  name: string;
  description: string;
  saving: boolean;
  running: boolean;
  dirty: boolean;
  validationErrors: string[];
  onNameChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onSave: () => void;
  onRun: () => void;
  onDuplicate: () => void;
  onExportYaml: () => void;
  onImportYaml: () => void;
  onToggleHistory: () => void;
  showHistory: boolean;
}

export function WorkflowToolbar({
  name,
  description,
  saving,
  running,
  dirty,
  validationErrors,
  onNameChange,
  onDescriptionChange,
  onSave,
  onRun,
  onDuplicate,
  onExportYaml,
  onImportYaml,
  onToggleHistory,
  showHistory,
}: WorkflowToolbarProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.35rem",
        padding: "0.6rem 1rem",
        borderBottom: "1px solid var(--oaui-border)",
        background: "var(--oaui-bg-elevated)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
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
            minWidth: "160px",
          }}
        />
        <button className="oaui-btn" type="button" onClick={onDuplicate}>
          Duplicate
        </button>
        <button className="oaui-btn" type="button" onClick={onExportYaml}>
          Export YAML
        </button>
        <button className="oaui-btn" type="button" onClick={onImportYaml}>
          Import YAML
        </button>
        <button className="oaui-btn" type="button" onClick={onToggleHistory}>
          {showHistory ? "Hide history" : "History"}
        </button>
        <button className="oaui-btn" onClick={onSave} disabled={saving || !dirty}>
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
        <button className="oaui-btn oaui-btn-primary" onClick={onRun} disabled={running}>
          {running ? "Running…" : "▶ Run"}
        </button>
      </div>
      {validationErrors.length > 0 && (
        <p className="oaui-card-desc" style={{ color: "var(--oaui-danger)", margin: 0 }}>
          {validationErrors.join(" · ")}
        </p>
      )}
    </div>
  );
}
