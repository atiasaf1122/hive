/** Backend pipeline DTO — mirror of backend/api/pipelines_http.py PipelineOut. */
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
