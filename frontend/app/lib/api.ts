// AgentShield API client — talks to the FastAPI backend.
export const API_BASE =
  (process.env.NEXT_PUBLIC_API_URL as string | undefined) || "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ---- types (mirror the backend) ----
export interface Agent {
  id: number;
  name: string;
  kind: string;
  endpoint_url: string;
  response_path: string;
  description?: string | null;
}

export interface RunCounts {
  scenarios: number;
  conversations: number;
  messages: number;
  judged: number;
}

export interface RunStatus {
  run_id: number;
  status: "queued" | "running" | "done" | "error";
  reliability_score: number | null;
  counts: RunCounts;
}

export interface Trace {
  tool_calls?: { name: string; result?: string; ok?: boolean }[];
  retrieved_docs?: { id: string; title: string; stale?: boolean }[];
  latency_ms?: number;
  tokens?: number;
  error?: string;
}

export interface Message {
  turn_index: number;
  role: "tester" | "agent";
  content: string;
  trace?: Trace | null;
}

export interface Conversation {
  id: number | string;
  scenario_title?: string;
  test_type?: string;
  assigned_fault?: string;
  verdict?: "pass" | "fail" | null;
  severity?: "low" | "med" | "high" | null;
  recovered?: boolean | null;
  scores?: Record<string, number | null | string> | null;
  explanation?: string | null;
  suggested_fix?: string | null;
  evidence?: string | null;
  messages: Message[];
}

export interface Performance {
  agent_turns: number;
  avg_latency_ms: number;
  max_latency_ms: number;
  total_tokens: number;
  est_cost_usd: number;
  price_per_1k: number;
}

export interface Breakdown {
  total_scenarios: number;
  passed: number;
  failed: number;
  by_test_type: Record<string, { pass: number; fail: number }>;
  by_failure_category: Record<string, number>;
  performance?: Performance;
}

export interface Report {
  run: { id: number | string; status: string };
  reliability_score: number | null;
  breakdown: Breakdown | null;
  conversations: Conversation[];
  is_demo?: boolean;
}

// ---- calls ----
export function registerAgent(body: {
  name: string;
  endpoint_url: string;
  response_path?: string;
  request_template?: string;
  auth_header?: string | null;
  description?: string | null;
}): Promise<{ agent_id: number }> {
  return req("/agents", { method: "POST", body: JSON.stringify(body) });
}

export function probeAgent(agentId: number): Promise<{ ok: boolean; reply: string; error?: string | null }> {
  return req(`/agents/${agentId}/probe`, { method: "POST" });
}

export function discoverAgent(agentId: number): Promise<{ description: string }> {
  return req(`/agents/${agentId}/discover`, { method: "POST" });
}

export function createRun(
  agentId: number,
  tests: string[],
  guidance = "",
  knowledge = ""
): Promise<{ run_id: number }> {
  return req("/runs", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, tests, guidance, knowledge }),
  });
}

export function getRun(runId: number): Promise<RunStatus> {
  return req(`/runs/${runId}`);
}

export function getReport(runId: number): Promise<Report> {
  return req(`/runs/${runId}/report`);
}

export function getDemoReport(): Promise<Report> {
  return req(`/runs/demo/report`);
}
