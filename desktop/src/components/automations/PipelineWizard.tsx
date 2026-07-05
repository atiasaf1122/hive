/**
 * Modal wizard for creating a new automation.
 *
 *   Step 1: When? (Schedule / Webhook / Manual)
 *   Step 2: What? (task description + name + model + approval mode)
 *   Step 3: Notify? (in-app · Telegram · email — stubs for now)
 *
 * On finish: POST /api/pipelines, return the new Pipeline to the parent.
 */
import {
  IconArrowLeft,
  IconArrowRight,
  IconBell,
  IconBolt,
  IconClock,
  IconWebhook,
  IconX,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useState } from 'react'
import { api } from '../../lib/api'
import type { Pipeline } from './types'

type Trigger = 'schedule' | 'webhook' | 'manual'

const CRON_PRESETS = [
  { label: 'Every hour', value: '0 * * * *' },
  { label: 'Every day at 9:00', value: '0 9 * * *' },
  { label: 'Every Monday 9:00', value: '0 9 * * 1' },
  { label: 'First of the month', value: '0 0 1 * *' },
]

interface Props {
  open: boolean
  onClose: () => void
  onCreated: (p: Pipeline) => void
}

export function PipelineWizard({ open, onClose, onCreated }: Props) {
  const [step, setStep] = useState(0)
  const [trigger, setTrigger] = useState<Trigger>('schedule')
  const [schedule, setSchedule] = useState(CRON_PRESETS[1].value)
  const [name, setName] = useState('')
  const [task, setTask] = useState('')
  const [model, setModel] = useState('claude:sonnet')
  const [approval, setApproval] = useState('full-auto')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function reset() {
    setStep(0)
    setTrigger('schedule')
    setSchedule(CRON_PRESETS[1].value)
    setName('')
    setTask('')
    setModel('claude:sonnet')
    setApproval('full-auto')
    setBusy(false)
    setErr(null)
  }

  async function submit() {
    setBusy(true)
    setErr(null)
    try {
      const body: Record<string, unknown> = {
        name: name.trim() || task.trim().slice(0, 60) || 'Untitled automation',
        task: task.trim(),
        model,
        approval_mode: approval,
      }
      if (trigger === 'schedule') body.schedule = schedule
      // webhook & manual leave schedule null
      const created = await api.post<Pipeline>('/api/pipelines', body)
      onCreated(created)
      reset()
      onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create automation')
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  const canAdvance =
    step === 0
      ? true
      : step === 1
      ? task.trim().length > 0
      : true

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[560px] max-w-[92vw] card shadow-hover overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <header className="px-5 py-3 border-b border-line flex items-center justify-between">
          <div className="text-sm text-ink">New automation · step {step + 1} of 3</div>
          <button type="button" onClick={onClose} className="text-ink-faint hover:text-ink">
            <IconX size={16} />
          </button>
        </header>

        <div className="p-5 min-h-[260px]">
          {step === 0 && <StepTrigger value={trigger} onChange={setTrigger} schedule={schedule} onSchedule={setSchedule} />}
          {step === 1 && (
            <StepWhat
              name={name}
              task={task}
              model={model}
              approval={approval}
              onName={setName}
              onTask={setTask}
              onModel={setModel}
              onApproval={setApproval}
            />
          )}
          {step === 2 && <StepNotify />}

          {err && (
            <div className="mt-3 text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
              {err}
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-line flex items-center justify-between">
          <Dots active={step} />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setStep((s) => Math.max(0, s - 1))}
              disabled={step === 0 || busy}
              className="btn-ghost text-xs inline-flex items-center gap-1 disabled:opacity-40"
            >
              <IconArrowLeft size={13} strokeWidth={1.75} /> Back
            </button>
            {step < 2 ? (
              <button
                type="button"
                onClick={() => setStep((s) => s + 1)}
                disabled={!canAdvance}
                className="btn-primary text-xs inline-flex items-center gap-1 disabled:opacity-50"
              >
                Continue <IconArrowRight size={13} strokeWidth={1.75} />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void submit()}
                disabled={busy || !task.trim()}
                className="btn-primary text-xs inline-flex items-center gap-1 disabled:opacity-50"
              >
                {busy ? 'Creating…' : 'Create automation'}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  )
}

function Dots({ active }: { active: number }) {
  return (
    <div className="flex items-center gap-1.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={clsx(
            'w-1.5 h-1.5 rounded-full',
            i === active ? 'bg-accent' : 'bg-line',
          )}
        />
      ))}
    </div>
  )
}

interface TriggerProps {
  value: Trigger
  onChange: (t: Trigger) => void
  schedule: string
  onSchedule: (cron: string) => void
}

function StepTrigger({ value, onChange, schedule, onSchedule }: TriggerProps) {
  return (
    <div>
      <div className="text-xs text-ink-muted mb-3">What kicks it off?</div>
      <div className="grid grid-cols-3 gap-2 mb-4">
        <TriggerChoice
          icon={IconClock}
          label="Schedule"
          hint="Cron expression"
          active={value === 'schedule'}
          onClick={() => onChange('schedule')}
        />
        <TriggerChoice
          icon={IconWebhook}
          label="Webhook"
          hint="HTTP POST"
          active={value === 'webhook'}
          onClick={() => onChange('webhook')}
        />
        <TriggerChoice
          icon={IconBolt}
          label="Manual"
          hint="Only when I click run"
          active={value === 'manual'}
          onClick={() => onChange('manual')}
        />
      </div>

      {value === 'schedule' && (
        <div className="space-y-2">
          <div className="text-xs text-ink-muted">Common schedules:</div>
          <div className="flex flex-wrap gap-1.5">
            {CRON_PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => onSchedule(p.value)}
                className={clsx(
                  'text-xs px-2.5 py-1 rounded-full border transition-colors',
                  schedule === p.value
                    ? 'border-accent bg-surface-2 text-ink'
                    : 'border-line text-ink-muted hover:border-ink-faint',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="text-xs text-ink-muted mt-3">Or paste a cron expression:</div>
          <input
            value={schedule}
            onChange={(e) => onSchedule(e.target.value)}
            placeholder="0 9 * * 1"
            className="input-soft text-sm font-mono w-56"
          />
        </div>
      )}

      {value === 'webhook' && (
        <div className="text-xs text-ink-muted">
          You'll get a secret URL after creation — POST to it to fire the
          automation. Useful for GitHub Actions / Zapier / cron on another box.
        </div>
      )}

      {value === 'manual' && (
        <div className="text-xs text-ink-muted">
          A "Run now" button on the card is all you'll need.
        </div>
      )}
    </div>
  )
}

function TriggerChoice({
  icon: Icon,
  label,
  hint,
  active,
  onClick,
}: {
  icon: typeof IconClock
  label: string
  hint: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'p-3 rounded-soft border text-left transition-colors',
        active ? 'border-accent bg-surface-2' : 'border-line hover:border-ink-faint',
      )}
    >
      <Icon size={16} strokeWidth={1.5} className="text-ink-muted mb-1.5" />
      <div className="text-sm text-ink">{label}</div>
      <div className="text-[11px] text-ink-faint">{hint}</div>
    </button>
  )
}

interface WhatProps {
  name: string
  task: string
  model: string
  approval: string
  onName: (s: string) => void
  onTask: (s: string) => void
  onModel: (s: string) => void
  onApproval: (s: string) => void
}

function StepWhat({ name, task, model, approval, onName, onTask, onModel, onApproval }: WhatProps) {
  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs text-ink-muted block mb-1">Name</label>
        <input
          value={name}
          onChange={(e) => onName(e.target.value)}
          placeholder="Daily haiku"
          className="input-soft text-sm w-full"
        />
      </div>
      <div>
        <label className="text-xs text-ink-muted block mb-1">Task description</label>
        <textarea
          value={task}
          onChange={(e) => onTask(e.target.value)}
          rows={3}
          placeholder="Write a haiku about a sleepy bee and save it to haiku.md"
          className="input-soft text-sm w-full resize-none"
        />
      </div>
      <div className="flex items-center gap-3">
        <div>
          <label className="text-xs text-ink-muted block mb-1">Model</label>
          <select
            value={model}
            onChange={(e) => {
              const next = e.target.value
              // Pipelines run autonomously — letting Opus drive a recurring
              // pipeline is the most expensive thing the user can do here.
              // Warn loudly but don't block. Invariant #7.
              if (next === 'claude:opus') {
                const ok = window.confirm(
                  'Opus on a scheduled pipeline is significantly more expensive than Sonnet. Use it anyway?',
                )
                if (!ok) return
              }
              onModel(next)
            }}
            className="input-soft text-sm"
          >
            <option value="claude:sonnet">Sonnet</option>
            <option value="claude:haiku">Haiku</option>
            <option value="claude:opus">Opus (costly)</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-ink-muted block mb-1">Approval</label>
          <select
            value={approval}
            onChange={(e) => onApproval(e.target.value)}
            className="input-soft text-sm"
          >
            <option value="full-auto">Full auto</option>
            <option value="checkpoint">Checkpoint</option>
            <option value="manual">Manual</option>
          </select>
        </div>
      </div>
    </div>
  )
}

function StepNotify() {
  return (
    <div className="space-y-3">
      <div className="text-xs text-ink-muted">Where will HIVE ping you?</div>
      <div className="card p-3 flex items-center gap-3">
        <IconBell size={16} className="text-ink-muted" />
        <div className="flex-1 text-sm text-ink">In-app banner</div>
        <span className="text-[11px] text-ink-faint">Always on</span>
      </div>
      <div className="card p-3 flex items-center gap-3 opacity-70">
        <IconBell size={16} className="text-ink-muted" />
        <div className="flex-1">
          <div className="text-sm text-ink">Telegram</div>
          <div className="text-[11px] text-ink-faint">
            On globally when configured in Settings → Integrations · attach this session with <code>/attach</code>
          </div>
        </div>
      </div>
    </div>
  )
}
