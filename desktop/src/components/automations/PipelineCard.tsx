/**
 * One automation card in the Automations grid.
 *
 *   ┌─[status stripe]───────────────────────────────────────────────┐
 *   │ 🪴  Name                                  toggle [on/off]      │
 *   │     Task description…                                          │
 *   │ schedule pill · last run pill                                  │
 *   │                                                                │
 *   │ [run now]  [history]  [edit]  [delete]                         │
 *   └────────────────────────────────────────────────────────────────┘
 */
import {
  IconBolt,
  IconClock,
  IconDots,
  IconHistory,
  IconPlayerPlay,
  IconTrash,
  IconWebhook,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useState } from 'react'
import { api } from '../../lib/api'
import type { Pipeline } from './types'

interface Props {
  pipeline: Pipeline
  onUpdated: (p: Pipeline) => void
  onDeleted: (id: string) => void
}

export function PipelineCard({ pipeline, onUpdated, onDeleted }: Props) {
  const [busy, setBusy] = useState(false)
  const [showActions, setShowActions] = useState(false)

  async function toggle() {
    setBusy(true)
    try {
      const next = await api.patch<Pipeline>(`/api/pipelines/${pipeline.id}`, {
        enabled: !pipeline.enabled,
      })
      onUpdated(next)
    } finally {
      setBusy(false)
    }
  }

  async function runNow() {
    setBusy(true)
    try {
      await api.post(`/api/pipelines/${pipeline.id}/run`)
    } finally {
      setBusy(false)
    }
  }

  async function destroy() {
    if (!confirm(`Delete "${pipeline.name}"? Run history is kept.`)) return
    setBusy(true)
    try {
      await api.delete(`/api/pipelines/${pipeline.id}`)
      onDeleted(pipeline.id)
    } finally {
      setBusy(false)
    }
  }

  const triggerLabel = pipeline.schedule ? 'cron' : 'webhook'

  return (
    <div
      className={clsx(
        'card card-hover p-4 relative overflow-hidden flex flex-col gap-3',
        !pipeline.enabled && 'opacity-65',
      )}
    >
      <span
        className={clsx(
          'absolute left-0 right-0 top-0 h-[3px]',
          pipeline.enabled ? 'bg-accent-gradient' : 'bg-ink-faint/40',
        )}
      />

      <div className="flex items-start gap-3">
        <div className="text-2xl leading-none">🤖</div>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-ink font-medium truncate">{pipeline.name}</div>
          <div className="text-xs text-ink-muted line-clamp-2 mt-0.5">
            {pipeline.task}
          </div>
        </div>
        <Toggle checked={pipeline.enabled} onChange={() => void toggle()} disabled={busy} />
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="inline-flex items-center gap-1 bg-surface-2 border border-line text-ink-muted rounded-full px-2 py-0.5">
          {pipeline.schedule ? <IconClock size={11} /> : <IconWebhook size={11} />}
          {pipeline.schedule || 'webhook only'}
        </span>
        <span className="text-ink-faint">·</span>
        <span className="text-ink-faint">
          last run: {pipeline.last_run_at ? new Date(pipeline.last_run_at).toLocaleString() : 'never'}
        </span>
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => void runNow()}
          disabled={busy}
          className="btn-ghost text-xs inline-flex items-center gap-1.5"
        >
          <IconPlayerPlay size={13} strokeWidth={1.75} /> Run now
        </button>
        <button
          type="button"
          onClick={() => setShowActions((v) => !v)}
          className="btn-ghost text-xs inline-flex items-center gap-1.5"
        >
          <IconDots size={13} strokeWidth={1.75} /> More
        </button>
        {showActions && (
          <>
            <button
              type="button"
              onClick={() => alert('Run history opens in Phase 9D')}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
            >
              <IconHistory size={13} strokeWidth={1.75} /> History
            </button>
            <button
              type="button"
              onClick={() => void destroy()}
              disabled={busy}
              className="btn-ghost text-xs inline-flex items-center gap-1.5 text-red-500"
            >
              <IconTrash size={13} strokeWidth={1.75} /> Delete
            </button>
          </>
        )}
        <div className="flex-1" />
        <div className="text-[10px] font-mono text-ink-faint" title={`Trigger: ${triggerLabel}`}>
          {pipeline.webhook_token && (
            <button
              type="button"
              onClick={() => {
                navigator.clipboard
                  .writeText(`/api/pipelines/webhook/${pipeline.webhook_token}`)
                  .catch(() => {})
              }}
              className="hover:text-ink-muted inline-flex items-center gap-1"
              title="Copy webhook path"
            >
              <IconBolt size={10} /> {pipeline.webhook_token.slice(0, 6)}…
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Toggle({
  checked,
  onChange,
  disabled,
}: { checked: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      disabled={disabled}
      className={clsx(
        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0',
        checked ? 'bg-accent' : 'bg-surface-2 border border-line',
        disabled && 'opacity-50',
      )}
    >
      <span
        className={clsx(
          'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
          checked ? 'translate-x-4' : 'translate-x-0.5',
        )}
      />
    </button>
  )
}
