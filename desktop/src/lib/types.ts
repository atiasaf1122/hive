/**
 * TypeScript mirrors of the FastAPI backend DTOs. Only the fields the UI
 * actually uses are required — extra fields from the server are ignored.
 */

export type SessionStatus =
  | 'active'
  | 'idle'
  | 'starting'
  | 'planning'
  | 'spawning'
  | 'running'
  | 'awaiting_user'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'closed'

export interface AgentInfo {
  agent_id: string
  role: string
  model: string
  status: 'idle' | 'running' | 'completed' | 'failed' | string
  /** C3/C4: MCP servers this agent is equipped with (catalog ids). */
  mcp_servers?: string[]
}

export interface SessionInfo {
  session_id: string
  name: string
  status: SessionStatus
  approval_mode: string
  created_at: string
  last_active: string
  agents?: AgentInfo[]
}

export interface ConversationEntry {
  role: 'user' | 'assistant' | 'system'
  content: string
  ts: number
}

export interface TeamMember {
  role: string
  model: string
  count: number
  passive?: boolean
  /** B1: each agent's own brief — shown in approval cards. */
  subtask?: string
  files_hint?: string[] | null
  max_turns?: number | null
  /** C3: MCP servers assigned to this agent — part of what's approved. */
  mcp_servers?: string[]
}

export interface TeamComposition {
  team: TeamMember[]
  confidence: number
  rationale: string
}

export interface InterruptPayload {
  type: 'team_approval' | 'awaiting_input'
  session_id: string
  /** Unique per-interrupt id. Required on POST /approve so two parallel
   *  approvals for the same session don't clobber each other (invariant #5). */
  correlation_id?: string
  team_composition?: TeamComposition
  confidence?: number
  reason?: string
  last_response?: string
  /** D2: plan-quality gate result — issues shown before the user approves. */
  plan_check?: { score: number; issues: string[]; passed: boolean } | null
}

/* ── Live WS events (loose — only the fields we read are typed) ───────── */

export interface WSEvent {
  type: string
  session_id?: string
  agent_id?: string
  ts?: string
  text?: string
  error?: string
  /** Monotonic per-process id stamped by event_bus.emit — use as the
   *  stable React key for event lists instead of array indices. */
  event_id?: number
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  payload?: InterruptPayload
  /** Surfaced at the top level too so reducers don't have to dig into payload. */
  correlation_id?: string
  team_composition?: TeamComposition
  agents?: AgentInfo[]
  /** mcp_servers_attached (C4): catalog ids the agent was equipped with. */
  servers?: string[]
  status?: string
  task_type?: string
  last_response?: string
  /** tool_use details (when type === 'tool/use'). */
  tool_name?: string
  tool_input?: Record<string, unknown>
  tool_use_id?: string
  /** tool_result details (when type === 'tool/result'). */
  tool_result?: unknown
  tool_result_error?: boolean
  /** planner_event sub-kind, e.g. 'tool/use', 'text/delta'. */
  kind?: string
  /** Human-readable hint for orchestrator_stall_hint. */
  hint?: string
}

/* ── Cost summary (Phase 8 endpoint, reused for the dashboard sparkline) */

export interface DailyCost {
  date: string
  cost_usd: number
}
export interface SessionCost {
  session_id: string
  name: string
  cost_usd: number
  input_tokens: number
  output_tokens: number
}
export interface CostSummary {
  days: number
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
  by_session: SessionCost[]
  by_day: DailyCost[]
}
