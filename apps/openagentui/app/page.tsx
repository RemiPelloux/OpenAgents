"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { Workflow } from "@/lib/workflow/types";
import { emptyWorkflow } from "@/lib/workflow/types";

export default function HomePage() {
  const router = useRouter();
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [templates, setTemplates] = useState<Workflow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch((e) => setError(String(e)));
    api
      .listTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, []);

  async function createBlank() {
    const id = `wf_${Math.random().toString(36).slice(2, 10)}`;
    const workflow = await api.createWorkflow(emptyWorkflow(id));
    router.push(`/workflows/${workflow.id}`);
  }

  async function installTemplate(templateId: string) {
    const workflow = await api.installTemplate(templateId);
    router.push(`/workflows/${workflow.id}`);
  }

  return (
    <main className="oaui-page">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h1 style={{ fontSize: "1.1rem", margin: 0 }}>Your workflows</h1>
        <button className="oaui-btn oaui-btn-primary" onClick={createBlank}>
          + New workflow
        </button>
      </div>

      {error && (
        <p className="oaui-card" style={{ borderColor: "var(--oaui-danger)" }}>
          Could not reach the OpenAgents dashboard API ({error}). Make sure{" "}
          <code>openagents dashboard</code> is running.
        </p>
      )}

      {workflows === null && !error && <p className="oaui-empty">Loading…</p>}
      {workflows !== null && workflows.length === 0 && (
        <p className="oaui-empty">No workflows yet — start from a template below or create a blank one.</p>
      )}

      <div className="oaui-grid">
        {workflows?.map((wf) => (
          <a key={wf.id} className="oaui-card oaui-card-link" href={`/workflows/${wf.id}`}>
            <span className="oaui-card-title">{wf.name}</span>
            <span className="oaui-card-desc">{wf.description || `${wf.nodes.length} nodes`}</span>
          </a>
        ))}
      </div>

      {templates && templates.length > 0 && (
        <>
          <h2 style={{ fontSize: "0.95rem", marginTop: "2rem" }}>Bundled scenarios</h2>
          <div className="oaui-grid">
            {templates.map((tpl) => (
              <div key={tpl.id} className="oaui-card">
                <div className="oaui-card-title">{tpl.name}</div>
                <div className="oaui-card-desc" style={{ marginBottom: "0.6rem" }}>
                  {tpl.description}
                </div>
                {tpl.tags?.map((t) => (
                  <span key={t} className="oaui-tag">
                    {t}
                  </span>
                ))}
                <div style={{ marginTop: "0.6rem" }}>
                  <button className="oaui-btn" onClick={() => installTemplate(tpl.id)}>
                    Use this scenario
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
