"use client";

import { useEffect, useState } from "react";
import type { InputVariableSpec } from "@/lib/workflow/types";

interface RunInputModalProps {
  open: boolean;
  variables: InputVariableSpec[];
  onCancel: () => void;
  onSubmit: (values: Record<string, string>) => void;
}

export function RunInputModal({ open, variables, onCancel, onSubmit }: RunInputModalProps) {
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    const initial: Record<string, string> = {};
    for (const spec of variables) {
      if (spec.name) initial[spec.name] = spec.defaultValue || "";
    }
    setValues(initial);
  }, [open, variables]);

  if (!open) return null;

  return (
    <div className="oaui-modal-backdrop" onClick={onCancel}>
      <div className="oaui-modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ margin: "0 0 0.75rem", fontSize: "0.95rem" }}>Run inputs</h2>
        {variables.length === 0 && <p className="oaui-card-desc">No input variables — click Run to start.</p>}
        {variables.map((spec) => (
          <div key={spec.name} className="oaui-field">
            <label>
              {spec.name}
              {spec.required ? " *" : ""}
            </label>
            <input
              value={values[spec.name] || ""}
              onChange={(e) => setValues((v) => ({ ...v, [spec.name]: e.target.value }))}
              placeholder={spec.defaultValue || ""}
            />
          </div>
        ))}
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem", justifyContent: "flex-end" }}>
          <button type="button" className="oaui-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="oaui-btn oaui-btn-primary"
            onClick={() => onSubmit(values)}
          >
            Run
          </button>
        </div>
      </div>
    </div>
  );
}
