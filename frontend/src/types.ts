export type AgentStatus = 'idle' | 'running' | 'completed' | 'failed' | 'waiting'

export interface Agent {
  agent_id: string
  role: string
  model: string
  status: AgentStatus
  currentActivity: string
  inputTokens: number
  outputTokens: number
  costUsd: number
  eventLog: string[]
}

export type SessionStatus =
  | 'starting'
  | 'planning'
  | 'spawning'
  | 'running'
  | 'waiting_approval'
  | 'awaiting_user'
  | 'closed'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface Session {
  id: string
  name: string
  status: SessionStatus
  approvalMode: string
  agents: Record<string, Agent>
  events: WSEvent[]
  interrupt: InterruptPayload | null
  createdAt: string
  totalCost: number
  textOutput: string
}

export interface TeamMember {
  role: string
  model: string
  count: number
  passive?: boolean
}

export interface TeamComposition {
  team: TeamMember[]
  confidence: number
  rationale: string
}

export interface InterruptPayload {
  type: 'team_approval'
  team_composition: TeamComposition
  confidence: number
  reason: string
}

export interface Pipeline {
  id: string
  name: string
  task: string
  model: string
  approval_mode: string
  schedule: string | null
  webhook_token: string
  enabled: boolean
  created_at: string
  last_run_at: string | null
  next_run_at: string | null
}

export interface PipelineRun {
  id: string
  pipeline_id: string
  session_id: string | null
  triggered_by: string
  status: 'running' | 'completed' | 'failed'
  started_at: string
  ended_at: string | null
}

export interface WSEvent {
  type: string
  session_id?: string
  agent_id?: string
  text?: string
  ts?: string
  error?: string
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  payload?: InterruptPayload
  team_composition?: TeamComposition
  agents?: unknown[]
  status?: string
  task?: string
  text_output?: string
}
