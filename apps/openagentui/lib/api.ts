import type { Workflow, WorkflowExecution, WorkflowSummary, TemplateCard } from "./workflow/types";

const BASE = "/api/openagentui";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method || "GET"} ${path} failed (${res.status}): ${body}`);
  }
  return res.json() as Promise<T>;
}

export interface ToolCatalog {
  toolsets: { id: string; label: string; description: string }[];
  tools: { id: string; toolset: string | null; emoji: string }[];
  mcpServers: { id: string }[];
}

export const api = {
  home: () =>
    request<{ workflows: WorkflowSummary[]; templates: TemplateCard[] }>("/home"),
  listWorkflows: () =>
    request<{ workflows: WorkflowSummary[] }>("/workflows").then((r) => r.workflows),
  getWorkflow: (id: string) => request<Workflow>(`/workflows/${id}`),
  editorBootstrap: (id: string) =>
    request<{ workflow: Workflow; catalog: ToolCatalog }>(`/workflows/${id}/editor`),
  createWorkflow: (workflow: Workflow) =>
    request<Workflow>("/workflows", { method: "POST", body: JSON.stringify(workflow) }),
  saveWorkflow: (workflow: Workflow) =>
    request<Workflow>(`/workflows/${workflow.id}`, { method: "PUT", body: JSON.stringify(workflow) }),
  deleteWorkflow: (id: string) => request<{ deleted: boolean }>(`/workflows/${id}`, { method: "DELETE" }),
  listTemplates: () => request<{ templates: Workflow[] }>("/templates").then((r) => r.templates),
  installTemplate: (templateId: string) =>
    request<Workflow>(`/templates/${templateId}/install`, { method: "POST" }),
  catalog: () => request<ToolCatalog>("/catalog"),
  runWorkflow: (id: string, inputs: Record<string, unknown>) =>
    request<WorkflowExecution>(`/workflows/${id}/run`, { method: "POST", body: JSON.stringify({ inputs }) }),
  listExecutions: (workflowId: string) =>
    request<{ executions: WorkflowExecution[] }>(`/workflows/${workflowId}/executions`).then((r) => r.executions),
  getExecution: (id: string) => request<WorkflowExecution>(`/executions/${id}`),
  approve: (executionId: string) =>
    request<WorkflowExecution>(`/executions/${executionId}/approve`, { method: "POST" }),
  reject: (executionId: string) =>
    request<WorkflowExecution>(`/executions/${executionId}/reject`, { method: "POST" }),

  /** Streams SSE `node`/`done`/`error` events; returns an unsubscribe function. */
  streamExecution(
    workflowId: string,
    inputs: Record<string, unknown>,
    onEvent: (event: string, data: unknown) => void
  ): () => void {
    const controller = new AbortController();
    fetch(`${BASE}/workflows/${workflowId}/execute-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() || "";
          for (const chunk of chunks) {
            const eventLine = chunk.split("\n").find((l) => l.startsWith("event:"));
            const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"));
            if (!eventLine || !dataLine) continue;
            const eventName = eventLine.slice(6).trim();
            try {
              onEvent(eventName, JSON.parse(dataLine.slice(5).trim()));
            } catch {
              // ignore malformed SSE frame
            }
          }
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) onEvent("error", { error: String(err) });
      });
    return () => controller.abort();
  },
};
