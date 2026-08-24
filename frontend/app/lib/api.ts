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

// One test case in the reviewable suite — mirrors a backend `scenarios` row.
// `source` is carried through so a reloaded suite still distinguishes AI from user cases.
export interface TestCase {
  title: string;
  user_goal: string;
  test_type: string;
  assigned_fault: string;
  expected_behavior: string;
  seed_turns: string[];
  source?: "ai" | "user";
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

// ---- parallel runs: one group holds one run per selected agent ----

// One agent to crash-test in a batch, with its own reviewed suite. Suites belong to a
// customer-agent combination, so every target carries its own.
export interface RunTargetInput {
  agent_id: number;
  customer_agent_id: number | null;
  scenarios: TestCase[];
}

export interface GroupRun {
  run_id: number;
  agent_id: number;
  agent_name: string | null;
  customer_name: string | null;
  customer_agent_id: number | null;
  status: "queued" | "running" | "done" | "error";
  reliability_score: number | null;
  counts: RunCounts;
}

export interface RunGroupStatus {
  group_id: number;
  status: "running" | "done";
  total: number;
  finished: number;
  errored: number;
  runs: GroupRun[];
}

export interface Report {
  run: { id: number | string; status: string };
  reliability_score: number | null;
  breakdown: Breakdown | null;
  conversations: Conversation[];
  is_demo?: boolean;
}

// A group report is one Report per agent, each labelled with whose agent it is.
export type AgentReport = Report & {
  agent_id: number;
  agent_name: string | null;
  customer_name: string | null;
  customer_agent_id: number | null;
};

export interface GroupReport {
  group_id: number;
  reports: AgentReport[];
}

// One customer and the agents onboarded for them (backend/inventory.yaml).
export interface InventoryAgent {
  key: string;
  name: string;
  customer_agent_id: number | null;
  agent_id: number | null;
}

export interface InventoryCustomer {
  name: string;
  agents: InventoryAgent[];
}

export interface Inventory {
  customers: InventoryCustomer[];
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

// Attach the agent's docs so every later run for it generates grounded test cases.
export function saveAgentKnowledge(
  agentId: number,
  text: string,
  name: string
): Promise<{ agent_id: number; knowledge_name: string | null; chars: number }> {
  return req(`/agents/${agentId}/knowledge`, {
    method: "POST",
    body: JSON.stringify({ text, name }),
  });
}

// Stage 1 — generate the test suite for review. Runs nothing.
export function generateScenarios(
  agentId: number,
  tests: string[],
  guidance = "",
  knowledge = ""
): Promise<{ scenarios: TestCase[] }> {
  return req("/runs/scenarios", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, tests, guidance, knowledge }),
  });
}

// Stage 3 — execute. `scenarios` is the finalized suite the user reviewed; when it is
// passed the backend runs exactly these and never regenerates.
export function createRun(
  agentId: number,
  tests: string[],
  guidance = "",
  knowledge = "",
  scenarios: TestCase[] = [],
  customerAgentId: number | null = null
): Promise<{ run_id: number }> {
  return req("/runs", {
    method: "POST",
    body: JSON.stringify({
      agent_id: agentId, tests, guidance, knowledge, scenarios,
      customer_agent_id: customerAgentId,
    }),
  });
}

// Execute every selected agent in parallel. One run per agent, one group over them —
// each agent keeps its own scenarios, verdicts and reliability score.
export function createRunGroup(
  targets: RunTargetInput[],
  tests: string[],
  guidance = "",
  knowledge = ""
): Promise<{ group_id: number; runs: { run_id: number; agent_id: number; customer_agent_id: number | null }[] }> {
  return req("/runs/group", {
    method: "POST",
    body: JSON.stringify({ targets, tests, guidance, knowledge }),
  });
}

export function getRunGroup(groupId: number): Promise<RunGroupStatus> {
  return req(`/runs/group/${groupId}`);
}

export function getGroupReport(groupId: number): Promise<GroupReport> {
  return req(`/runs/group/${groupId}/report`);
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

// Customer -> agents combinations from backend/inventory.yaml (see /inventory).
export function getInventory(): Promise<Inventory> {
  return req("/inventory");
}

// The test cases already stored for one customer-agent combination (may be empty).
export interface StoredTestCases {
  customer_agent_id: number;
  customer_name: string;
  agent_id: number;
  agent_name: string;
  scenarios: TestCase[];
}

export function getStoredTestCases(customerAgentId: number): Promise<StoredTestCases> {
  return req(`/inventory/${customerAgentId}/test-cases`);
}

// Save the reviewed suite without running it (same storage path as createRun).
export function saveTestCases(
  agentId: number,
  scenarios: TestCase[],
  customerAgentId: number | null = null
): Promise<{ customer_agent_id: number; saved: number }> {
  return req("/runs/test-cases", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, scenarios, customer_agent_id: customerAgentId }),
  });
}

// Generate ONE test case from the user's description (Add Test Case → Generate with AI).
export function generateOneScenario(
  agentId: number,
  description: string,
  testType: string,
  assignedFault: string
): Promise<{ scenario: TestCase }> {
  return req("/runs/scenarios/one", {
    method: "POST",
    body: JSON.stringify({
      agent_id: agentId, description, test_type: testType, assigned_fault: assignedFault,
    }),
  });
}
