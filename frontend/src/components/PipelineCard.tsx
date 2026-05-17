import { useState } from 'react'
import { apiGet, apiPatch, apiPost } from '../ws'
import type { Pipeline, PipelineRun } from '../types'

interface Props {
  pipeline: Pipeline
  onUpdated: (pipeline: Pipeline) => void
  onDeleted: (id: string) => void
}

const statusColor: Record<string, string> = {
  completed: 'text-green-400',
  failed: 'text-red-400',
  running: 'text-yellow-400',
}

export function PipelineCard({ pipeline, onUpdated, onDeleted }: Props) {
  const [runs, setRuns] = useState<PipelineRun[] | null>(null)
  const [showRuns, setShowRuns] = useState(false)
  const [running, setRunning] = useState(false)

  async function handleRun() {
    if (running) return
    setRunning(true)
    try {
      await apiPost(`/api/pipelines/${pipeline.id}/run`, {})
    } finally {
      setRunning(false)
    }
  }

  async function toggleEnabled() {
    try {
      const updated = await apiPatch<Pipeline>(
        `/api/pipelines/${pipeline.id}`,
        { enabled: !pipeline.enabled },
      )
      onUpdated(updated)
    } catch {
      // ignore
    }
  }

  async function loadRuns() {
    if (showRuns) {
      setShowRuns(false)
      return
    }
    const data = await apiGet<PipelineRun[]>(`/api/pipelines/${pipeline.id}/runs`)
    setRuns(data)
    setShowRuns(true)
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0">
          <div className="text-sm font-medium text-white truncate">{pipeline.name}</div>
          <div className="text-xs text-gray-600 mt-0.5 line-clamp-2">{pipeline.task}</div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => void toggleEnabled()}
            title={pipeline.enabled ? 'Disable' : 'Enable'}
            className={`w-8 h-4 rounded-full transition-colors relative ${pipeline.enabled ? 'bg-violet-600' : 'bg-gray-700'}`}
          >
            <span
              className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${pipeline.enabled ? 'left-4' : 'left-0.5'}`}
            />
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 text-xs text-gray-600 mb-3">
        <span className="bg-gray-800 px-1.5 py-0.5 rounded">{pipeline.model}</span>
        {pipeline.schedule ? (
          <span className="font-mono bg-gray-800 px-1.5 py-0.5 rounded">{pipeline.schedule}</span>
        ) : (
          <span className="text-gray-700">manual only</span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => void handleRun()}
          disabled={running}
          className="flex-1 bg-gray-800 hover:bg-gray-700 disabled:opacity-40 text-white text-xs py-1.5 rounded-lg transition-colors"
        >
          {running ? 'Starting…' : '▶ Run now'}
        </button>
        <button
          onClick={() => void loadRuns()}
          className="bg-gray-800 hover:bg-gray-700 text-gray-400 text-xs px-3 py-1.5 rounded-lg transition-colors"
        >
          {showRuns ? '▲' : 'History'}
        </button>
        <button
          onClick={() => onDeleted(pipeline.id)}
          className="text-gray-700 hover:text-red-400 text-xs px-2 py-1.5 transition-colors"
          title="Delete pipeline"
        >
          ✕
        </button>
      </div>

      {showRuns && runs && (
        <div className="mt-3 border-t border-gray-800 pt-3 space-y-1.5">
          {runs.length === 0 ? (
            <div className="text-xs text-gray-700 text-center py-2">No runs yet</div>
          ) : (
            runs.map((r) => (
              <div key={r.id} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className={statusColor[r.status] ?? 'text-gray-500'}>●</span>
                  <span className="text-gray-500">{r.triggered_by}</span>
                  {r.session_id && (
                    <span className="text-gray-700 font-mono">{r.session_id}</span>
                  )}
                </div>
                <span className="text-gray-700">
                  {r.started_at.slice(11, 16)}
                  {r.ended_at && ` → ${r.ended_at.slice(11, 16)}`}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
