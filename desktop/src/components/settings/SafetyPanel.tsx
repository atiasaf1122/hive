/**
 * Settings → Safety.
 *
 * Read-only view of the six hard-stop limits (these are NON-overridable
 * via UI — they're build-time defaults) plus the live per-worker
 * circuit-breaker table with reset buttons.
 *
 * Per-project safety caps live with the project itself (Phase 9C added
 * `backgroundAutomations` to settings; per-project token/agent ceilings
 * are a follow-up — see SUMMARY for the deferral).
 */
import { IconAlertTriangle, IconCircleCheck, IconRefresh } from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { SettingCard, SettingRow } from './SettingsLayout'

interface HardStops {
  max_concurrent_agents: number
  max_session_duration_hours: number
  max_same_file_edits: number
  vram_threshold_percent: number
  disk_min_free_gb: number
  max_tokens_per_autonomous_run: number
}

interface Breaker {
  worker_id: string
  state: 'closed' | 'open' | 'half_open'
  consecutive_failures: number
  time_until_close_seconds: number
  total_trips: number
}

const STATE_STYLE: Record<string, { dot: string; text: string; label: string }> = {
  closed:    { dot: 'bg-emerald-400',     text: 'text-emerald-500', label: 'closed' },
  half_open: { dot: 'bg-amber-400 animate-pulse', text: 'text-amber-500',  label: 'half-open (probing)' },
  open:      { dot: 'bg-red-400',         text: 'text-red-500',     label: 'open (rejecting)' },
}

export function SafetyPanel() {
  const [limits, setLimits] = useState<HardStops | null>(null)
  const [breakers, setBreakers] = useState<Breaker[]>([])
  const [err, setErr] = useState<string | null>(null)

  async function refresh() {
    try {
      const [lim, brk] = await Promise.all([
        api.get<HardStops>('/api/safety/limits/defaults'),
        api.get<{ items: Breaker[] }>('/api/safety/breakers'),
      ])
      setLimits(lim)
      setBreakers(brk.items)
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'failed to load')
    }
  }

  useEffect(() => {
    void refresh()
    const id = window.setInterval(refresh, 10_000)
    return () => window.clearInterval(id)
  }, [])

  async function resetBreaker(worker_id: string) {
    await api.post(`/api/safety/breakers/${encodeURIComponent(worker_id)}/reset`)
    await refresh()
  }

  return (
    <>
      <SettingCard
        title="Hard limits (ceiling)"
        description="Non-overridable ceilings on any autonomous run. Hitting one of these pauses execution and surfaces the reason."
      >
        {!limits ? (
          <div className="text-xs text-ink-faint">Loading…</div>
        ) : (
          <>
            <SettingRow
              label="Max tokens per autonomous run"
              hint="The single biggest cost gate. Going past requires explicit user OK to continue."
            >
              <span className="font-mono text-sm text-ink">
                {limits.max_tokens_per_autonomous_run.toLocaleString()}
              </span>
            </SettingRow>
            <SettingRow
              label="Max session duration"
              hint="Long-running sessions are the most common way to exhaust weekly Max quota."
            >
              <span className="font-mono text-sm text-ink">
                {limits.max_session_duration_hours} h
              </span>
            </SettingRow>
            <SettingRow
              label="Max parallel agents"
              hint="Higher = more rate-limit pressure on Claude."
            >
              <span className="font-mono text-sm text-ink">
                {limits.max_concurrent_agents}
              </span>
            </SettingRow>
            <SettingRow
              label="Max edits to one file"
              hint="Hitting this usually means agents are thrashing."
            >
              <span className="font-mono text-sm text-ink">
                {limits.max_same_file_edits}
              </span>
            </SettingRow>
            <SettingRow
              label="VRAM threshold"
              hint="Local Ollama workers are paused above this."
            >
              <span className="font-mono text-sm text-ink">
                {limits.vram_threshold_percent}%
              </span>
            </SettingRow>
            <SettingRow
              label="Disk space floor"
              hint="Worktrees + audit logs can fill the disk fast under autonomous runs."
            >
              <span className="font-mono text-sm text-ink">
                {limits.disk_min_free_gb} GB
              </span>
            </SettingRow>
          </>
        )}
        <div className="mt-3 text-[11px] text-ink-faint">
          Hard limits are build-time constants today. A per-project override
          UI is coming — see SUMMARY for the deferral.
        </div>
      </SettingCard>

      <SettingCard
        title="Circuit breakers"
        description="One per worker model. After 3 consecutive failures the breaker opens; new attempts are rejected for a 5-minute cool-down."
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] text-ink-faint">
            {breakers.length} worker{breakers.length === 1 ? '' : 's'} tracked
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            className="btn-ghost text-xs inline-flex items-center gap-1.5"
          >
            <IconRefresh size={12} /> Refresh
          </button>
        </div>

        {err && (
          <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
            {err}
          </div>
        )}

        {breakers.length === 0 ? (
          <div className="text-xs text-ink-muted italic py-3 inline-flex items-center gap-2">
            <IconCircleCheck size={14} className="text-emerald-500" />
            Nothing has tripped yet — no failures recorded.
          </div>
        ) : (
          <ul className="space-y-1.5">
            {breakers.map((b) => {
              const s = STATE_STYLE[b.state] ?? STATE_STYLE.closed
              return (
                <li
                  key={b.worker_id}
                  className="flex items-center gap-3 px-3 py-2 rounded-soft border border-line"
                >
                  <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', s.dot)} />
                  <span className="text-sm text-ink min-w-[160px]">{b.worker_id}</span>
                  <span className={clsx('text-[11px] uppercase tracking-wider', s.text)}>
                    {s.label}
                  </span>
                  <span className="text-[11px] text-ink-faint">
                    {b.consecutive_failures} consec. fail
                    {b.total_trips > 0 ? ` · ${b.total_trips} total trips` : ''}
                    {b.state === 'open' && b.time_until_close_seconds > 0
                      ? ` · ${Math.ceil(b.time_until_close_seconds)} s until probe`
                      : ''}
                  </span>
                  <div className="flex-1" />
                  {b.state !== 'closed' && (
                    <button
                      type="button"
                      onClick={() => void resetBreaker(b.worker_id)}
                      className="btn-ghost text-xs inline-flex items-center gap-1.5"
                      title="Force the breaker back to CLOSED"
                    >
                      <IconAlertTriangle size={12} /> Reset
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </SettingCard>
    </>
  )
}
