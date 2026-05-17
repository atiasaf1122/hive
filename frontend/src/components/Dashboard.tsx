import { useEffect, useState } from 'react'
import { useSessionsStore } from '../stores/sessions'
import { apiDelete, apiGet, apiPost } from '../ws'
import { PipelineBuilder } from './PipelineBuilder'
import { PipelineCard } from './PipelineCard'
import { ProjectCard } from './ProjectCard'
import type { Pipeline } from '../types'

export function Dashboard() {
  const { sessions, addSession, setActiveSession } = useSessionsStore()
  const [task, setTask] = useState('')
  const [model, setModel] = useState('claude:sonnet')
  const [approvalMode, setApprovalMode] = useState('full-auto')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [tab, setTab] = useState<'sessions' | 'pipelines'>('sessions')

  const sessionList = Object.values(sessions).sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  )

  useEffect(() => {
    apiGet<Pipeline[]>('/api/pipelines')
      .then(setPipelines)
      .catch(() => {})
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!task.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await apiPost<{ session_id: string }>('/api/sessions', {
        task: task.trim(),
        model,
        approval_mode: approvalMode,
        max_turns: 20,
      })
      const sessionId = res.session_id
      addSession(sessionId, task.trim().slice(0, 60), approvalMode)
      setTask('')
      setActiveSession(sessionId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start session')
    } finally {
      setSubmitting(false)
    }
  }

  function handlePipelineCreated(p: Pipeline) {
    setPipelines((prev) => [p, ...prev])
  }

  function handlePipelineUpdated(p: Pipeline) {
    setPipelines((prev) => prev.map((x) => (x.id === p.id ? p : x)))
  }

  async function handlePipelineDeleted(id: string) {
    try {
      await apiDelete(`/api/pipelines/${id}`)
      setPipelines((prev) => prev.filter((p) => p.id !== id))
    } catch {
      // ignore
    }
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-2xl font-bold text-white mb-1">HIVE</h1>
        <p className="text-gray-600 text-sm mb-8">AI agent swarm orchestration</p>

        <form onSubmit={(e) => void handleSubmit(e)} className="mb-8">
          <div className="bg-gray-900 border border-gray-800 focus-within:border-gray-700 rounded-xl p-4 transition-colors">
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Describe your task… e.g. 'Build a REST API for a todo app with SQLite'"
              className="w-full bg-transparent text-white placeholder-gray-600 resize-none outline-none text-sm leading-relaxed"
              rows={3}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  void handleSubmit(e)
                }
              }}
            />
            <div className="flex items-center gap-3 mt-3 pt-3 border-t border-gray-800">
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="bg-gray-800 text-gray-300 text-xs rounded px-2 py-1.5 border border-gray-700 outline-none"
              >
                <option value="claude:sonnet">claude:sonnet</option>
                <option value="claude:opus">claude:opus</option>
                <option value="claude:haiku">claude:haiku</option>
              </select>
              <select
                value={approvalMode}
                onChange={(e) => setApprovalMode(e.target.value)}
                className="bg-gray-800 text-gray-300 text-xs rounded px-2 py-1.5 border border-gray-700 outline-none"
              >
                <option value="full-auto">full-auto</option>
                <option value="checkpoint">checkpoint</option>
                <option value="manual">manual</option>
              </select>
              <span className="text-xs text-gray-600 ml-auto mr-2">⌘↵</span>
              <button
                type="submit"
                disabled={!task.trim() || submitting}
                className="bg-violet-600 hover:bg-violet-500 disabled:bg-gray-800 disabled:text-gray-600 text-white text-xs px-4 py-1.5 rounded-lg font-medium transition-colors"
              >
                {submitting ? 'Starting…' : 'Run →'}
              </button>
            </div>
            {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
          </div>
        </form>

        {/* Tab bar */}
        <div className="flex gap-4 mb-4 border-b border-gray-800">
          {(['sessions', 'pipelines'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`pb-2 text-xs capitalize transition-colors ${
                tab === t
                  ? 'text-white border-b-2 border-violet-500'
                  : 'text-gray-600 hover:text-gray-400'
              }`}
            >
              {t}
              {t === 'sessions' && sessionList.length > 0 && (
                <span className="ml-1.5 bg-gray-800 text-gray-500 px-1.5 py-0.5 rounded text-xs">
                  {sessionList.length}
                </span>
              )}
              {t === 'pipelines' && pipelines.length > 0 && (
                <span className="ml-1.5 bg-gray-800 text-gray-500 px-1.5 py-0.5 rounded text-xs">
                  {pipelines.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {tab === 'sessions' && (
          <>
            {sessionList.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {sessionList.map((s) => (
                  <ProjectCard key={s.id} session={s} />
                ))}
              </div>
            ) : (
              <div className="text-center py-20 text-gray-700">
                <div className="text-4xl mb-3">🐝</div>
                <div className="text-sm">No sessions yet. Start your first task above.</div>
              </div>
            )}
          </>
        )}

        {tab === 'pipelines' && (
          <div className="space-y-3">
            <PipelineBuilder onCreated={handlePipelineCreated} />
            {pipelines.map((p) => (
              <PipelineCard
                key={p.id}
                pipeline={p}
                onUpdated={handlePipelineUpdated}
                onDeleted={(id) => void handlePipelineDeleted(id)}
              />
            ))}
            {pipelines.length === 0 && (
              <div className="text-center py-12 text-gray-700 text-sm">
                No pipelines yet. Create one above to automate recurring tasks.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
