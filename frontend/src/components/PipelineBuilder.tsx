import { useState } from 'react'
import { apiPost } from '../ws'
import type { Pipeline } from '../types'

interface Props {
  onCreated: (pipeline: Pipeline) => void
}

export function PipelineBuilder({ onCreated }: Props) {
  const [name, setName] = useState('')
  const [task, setTask] = useState('')
  const [model, setModel] = useState('claude:sonnet')
  const [approvalMode, setApprovalMode] = useState('full-auto')
  const [schedule, setSchedule] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !task.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const p = await apiPost<Pipeline>('/api/pipelines', {
        name: name.trim(),
        task: task.trim(),
        model,
        approval_mode: approvalMode,
        schedule: schedule.trim() || null,
      })
      onCreated(p)
      setName('')
      setTask('')
      setSchedule('')
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create pipeline')
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-300 border border-dashed border-gray-800 hover:border-gray-700 rounded-lg px-4 py-3 w-full transition-colors"
      >
        <span className="text-base">+</span>
        New pipeline
      </button>
    )
  }

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="bg-gray-900 border border-gray-700 rounded-xl p-4"
    >
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm font-medium text-white">New Pipeline</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-gray-600 hover:text-gray-400 text-xs"
        >
          ✕
        </button>
      </div>

      <div className="space-y-3">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Pipeline name (e.g. Daily haiku)"
          className="w-full bg-gray-800 border border-gray-700 focus:border-gray-600 rounded px-3 py-2 text-xs text-white placeholder-gray-600 outline-none transition-colors"
        />
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Task description…"
          rows={2}
          className="w-full bg-gray-800 border border-gray-700 focus:border-gray-600 rounded px-3 py-2 text-xs text-white placeholder-gray-600 outline-none resize-none transition-colors"
        />
        <input
          value={schedule}
          onChange={(e) => setSchedule(e.target.value)}
          placeholder="Cron schedule (e.g. 0 17 * * *) — leave blank for manual only"
          className="w-full bg-gray-800 border border-gray-700 focus:border-gray-600 rounded px-3 py-2 text-xs text-white placeholder-gray-600 font-mono outline-none transition-colors"
        />
        <div className="flex gap-2">
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="flex-1 bg-gray-800 text-gray-300 text-xs rounded px-2 py-2 border border-gray-700 outline-none"
          >
            <option value="claude:sonnet">claude:sonnet</option>
            <option value="claude:haiku">claude:haiku</option>
            <option value="claude:opus">claude:opus</option>
          </select>
          <select
            value={approvalMode}
            onChange={(e) => setApprovalMode(e.target.value)}
            className="flex-1 bg-gray-800 text-gray-300 text-xs rounded px-2 py-2 border border-gray-700 outline-none"
          >
            <option value="full-auto">full-auto</option>
            <option value="checkpoint">checkpoint</option>
          </select>
        </div>
      </div>

      {error && <p className="text-red-400 text-xs mt-2">{error}</p>}

      <div className="flex gap-2 mt-4">
        <button
          type="submit"
          disabled={!name.trim() || !task.trim() || submitting}
          className="flex-1 bg-violet-600 hover:bg-violet-500 disabled:bg-gray-800 disabled:text-gray-600 text-white text-xs py-2 rounded-lg font-medium transition-colors"
        >
          {submitting ? 'Creating…' : 'Create Pipeline'}
        </button>
      </div>
    </form>
  )
}
