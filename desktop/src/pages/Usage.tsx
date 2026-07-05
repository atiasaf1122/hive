/**
 * Usage tab — modelled on the bars Anthropic shows in claude.ai.
 *
 *   Current session (5h rolling) — resets when window cycles ............ X%
 *   Weekly limits · All models   — resets Monday in Xd Xh ............... X%
 *   Weekly limits · Sonnet only  — resets Monday in Xd Xh ............... X%
 *   Daily included routine runs  ............................. used / 15
 *
 *   Local activity (what HIVE measured locally)
 *   External APIs (when you've used a $-billed key)
 *   Ollama (free, local)
 *   Per-project (last 30 days)
 *
 * The percentages are *estimates derived from local activity* — Anthropic
 * doesn't expose Max quotas via API. The disclaimer at the bottom says
 * exactly that. Authoritative numbers live on claude.ai.
 */
import {
  IconBrain,
  IconChartHistogram,
  IconCloud,
  IconCpu,
  IconExternalLink,
  IconRefresh,
} from '@tabler/icons-react'
import clsx from 'clsx'
import { useEffect, useMemo, useState } from 'react'
import { HeroHeader } from '../components/ui/HeroHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { api } from '../lib/api'
import type { CostSummary } from '../lib/types'

interface WindowUsage {
  label: string
  hours: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
}
interface UsageSummary {
  claude: {
    last_hour: WindowUsage
    last_5h: WindowUsage
    last_7d: WindowUsage
    rate_limit_hits_week: number
    burn_ratio: number
  }
  ollama: {
    total_runs_week: number
    by_model: { model: string; runs: number }[]
  }
  notes: string[]
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

/**
 * Heuristic estimates for the Anthropic Max plan limits. We pick numbers
 * that are *conservative* relative to publicly documented Max behaviour —
 * the goal is not to be exact (the API can't be), but to flag the user
 * when they're getting close to a likely cap.
 *
 * 5-hour window: total combined input+output tokens before Anthropic
 *   throttles a single conversation. ~600k is a reasonable yardstick.
 * Weekly all-model: combined tokens per week. ~10M is the right ballpark.
 * Weekly Sonnet-only: tighter limit for the cheaper tier. ~4M.
 * Daily routine runs: 15 included on the Max plan.
 */
const LIMITS = {
  session5h: 600_000,
  weeklyAll: 10_000_000,
  dailyRoutine: 15,
}

function pctFor(used: number, ceiling: number): number {
  return Math.min(100, Math.max(0, Math.round((used / ceiling) * 100)))
}

function colorFor(pct: number): string {
  if (pct < 50) return 'bg-emerald-500'
  if (pct < 80) return 'bg-amber-500'
  return 'bg-red-500'
}

function untilNextMonday(now = new Date()): string {
  const day = now.getDay() // Sunday = 0
  const daysUntilMonday = (8 - day) % 7 || 7
  const next = new Date(now)
  next.setDate(now.getDate() + daysUntilMonday)
  next.setHours(0, 0, 0, 0)
  const diffMs = next.getTime() - now.getTime()
  const d = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  const h = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))
  return `${d}d ${h}h`
}

export function Usage() {
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [cost, setCost] = useState<CostSummary | null>(null)
  const [err, setErr] = useState<string | null>(null)

  async function load() {
    try {
      const [u, c] = await Promise.all([
        api.get<UsageSummary>('/api/usage/summary'),
        api.get<CostSummary>('/api/cost/summary?days=30'),
      ])
      setUsage(u)
      setCost(c)
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load usage')
    }
  }
  useEffect(() => {
    void load()
    const id = window.setInterval(load, 30_000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-8 space-y-6">
        <HeroHeader
          icon={IconChartHistogram}
          title="Usage"
          blurb="Approximations of the Max-subscription limits, derived from your local activity. Authoritative numbers live on claude.ai — open it in another tab for the real bars."
          tone="soft"
          actions={
            <button
              type="button"
              onClick={() => void load()}
              className="btn-ghost text-xs inline-flex items-center gap-1.5"
            >
              <IconRefresh size={13} strokeWidth={1.75} /> Refresh
            </button>
          }
        />

        {err && (
          <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
            {err}
          </div>
        )}

        <SectionHeader icon={IconBrain} title="Claude (estimated)" subtitle="Modelled on the bars you see in claude.ai." />
        {usage ? <ClaudeBars u={usage} /> : <Skeleton variant="block" className="h-40" />}

        <SectionHeader icon={IconChartHistogram} title="Local activity" subtitle="Raw token counts HIVE actually measured." />
        {usage ? <LocalActivity u={usage} /> : <Skeleton variant="block" className="h-24" />}

        <SectionHeader icon={IconCloud} title="External APIs" subtitle="When you've pointed HIVE at a paid Claude API key, spend lives here." />
        {cost ? <ExternalSection c={cost} /> : <Skeleton variant="block" className="h-24" />}

        <SectionHeader icon={IconCpu} title="Ollama (local)" subtitle="Zero cost." />
        {usage ? <OllamaSection u={usage} /> : <Skeleton variant="block" className="h-24" />}

        <SectionHeader icon={IconChartHistogram} title="Per project (last 30 days)" />
        {cost ? <PerProjectTable c={cost} /> : <Skeleton variant="block" className="h-32" />}

        <div className="text-[11px] text-ink-faint pt-3 border-t border-line space-y-1">
          <div>
            · These limits are estimated locally from your activity. Anthropic doesn't expose
            Max quotas via API. Always check{' '}
            <a
              href="https://claude.ai/usage"
              target="_blank"
              rel="noreferrer"
              className="underline inline-flex items-center gap-1 hover:text-ink-muted"
            >
              claude.ai
              <IconExternalLink size={10} />
            </a>
            {' '}for the authoritative bars.
          </div>
          {usage?.notes?.map((n, i) => <div key={i}>· {n}</div>)}
        </div>
      </div>
    </div>
  )
}

function ClaudeBars({ u }: { u: UsageSummary }) {
  const sessionUsed = u.claude.last_5h.input_tokens + u.claude.last_5h.output_tokens
  const weeklyAllUsed = u.claude.last_7d.input_tokens + u.claude.last_7d.output_tokens
  const routineUsed = 0

  const sessionPct = pctFor(sessionUsed, LIMITS.session5h)
  const showAlert = sessionPct >= 80 || pctFor(weeklyAllUsed, LIMITS.weeklyAll) >= 80

  return (
    <div className="card p-5 space-y-4">
      {showAlert && (
        <div className="text-[11px] text-red-600 bg-red-500/10 border border-red-500/20 rounded-soft px-3 py-2">
          You're approaching an estimated limit. Check{' '}
          <a href="https://claude.ai/usage" target="_blank" rel="noreferrer" className="underline">
            claude.ai/usage
          </a>{' '}for the authoritative number.
        </div>
      )}
      <LimitBar
        label="Current session"
        sub="rolling 5-hour window"
        used={sessionUsed}
        ceiling={LIMITS.session5h}
        suffix="tokens"
      />
      <LimitBar
        label="Weekly limits · All models"
        sub={`resets Monday · in ${untilNextMonday()}`}
        used={weeklyAllUsed}
        ceiling={LIMITS.weeklyAll}
        suffix="tokens"
      />
      <LimitBar
        label="Daily included routine runs"
        sub="resets at midnight local"
        used={routineUsed}
        ceiling={LIMITS.dailyRoutine}
        suffix="runs"
      />
      <div className="text-[11px] text-ink-faint pt-1">
        "Routine runs" only counts Max-plan automated runs, which Anthropic
        doesn't surface separately yet — check{' '}
        <a href="https://claude.ai/usage" target="_blank" rel="noreferrer" className="underline">
          claude.ai/usage
        </a>{' '}for per-model breakdowns.
      </div>
    </div>
  )
}

function LimitBar({
  label, sub, used, ceiling, suffix, muted,
}: {
  label: string
  sub: string
  used: number
  ceiling: number
  suffix: string
  muted?: boolean
}) {
  const pct = pctFor(used, ceiling)
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <div className="text-sm text-ink">
          {label}
          {muted && <span className="ml-1 text-[10px] text-ink-faint">(estimated)</span>}
        </div>
        <div className="text-xs text-ink-muted font-mono">
          {compact(used)} / {compact(ceiling)} {suffix}
          <span className="ml-2 text-ink">{pct}%</span>
        </div>
      </div>
      <div className="h-2 bg-surface-2 rounded-full overflow-hidden">
        <div className={clsx('h-full transition-all', colorFor(pct))} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[11px] text-ink-faint mt-1">{sub}</div>
    </div>
  )
}

function LocalActivity({ u }: { u: UsageSummary }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <Stat
        label="Last hour"
        primary={`${compact(u.claude.last_hour.input_tokens + u.claude.last_hour.output_tokens)} toks`}
        secondary={`${compact(u.claude.last_hour.input_tokens)} in · ${compact(u.claude.last_hour.output_tokens)} out`}
      />
      <Stat
        label="Burn rate"
        primary={`${u.claude.burn_ratio.toFixed(2)}×`}
        secondary={
          u.claude.burn_ratio >= 2 ? 'hot — well above your 7-day average'
            : u.claude.burn_ratio >= 1.2 ? 'warm — a bit busier than usual'
            : 'cool — under your 7-day average'
        }
      />
      <Stat
        label="Rate-limit hits / week"
        primary={String(u.claude.rate_limit_hits_week)}
        secondary={u.claude.rate_limit_hits_week === 0 ? "you've been clear" : 'count of system/rate_limit events'}
      />
    </div>
  )
}

function ExternalSection({ c }: { c: CostSummary }) {
  return (
    <div className="card p-4">
      <div className="flex items-baseline gap-3">
        <div className="text-2xl text-ink font-medium">${c.total_cost_usd.toFixed(2)}</div>
        <div className="text-[11px] text-ink-faint">
          over the last {c.days} days · {compact(c.total_input_tokens)} input · {compact(c.total_output_tokens)} output
        </div>
      </div>
      <div className="text-[11px] text-ink-faint mt-2">
        Max-subscription sessions show as $0 here — they aren't billed per token.
      </div>
    </div>
  )
}

function OllamaSection({ u }: { u: UsageSummary }) {
  const total = u.ollama.total_runs_week
  return (
    <div className="card p-4">
      <div className="flex items-baseline gap-3">
        <div className="text-2xl text-ink font-medium">{total}</div>
        <div className="text-[11px] text-ink-faint">
          local runs this week · saving you {total} cloud requests
        </div>
      </div>
      {u.ollama.by_model.length > 0 && (
        <div className="grid grid-cols-3 gap-2 mt-3">
          {u.ollama.by_model.map((m) => (
            <div key={m.model} className="border border-line rounded-soft p-2">
              <div className="text-xs text-ink">{m.model}</div>
              <div className="text-[11px] text-ink-faint">{m.runs} runs</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PerProjectTable({ c }: { c: CostSummary }) {
  const top = useMemo(() => c.by_session.slice(0, 10), [c])
  if (top.length === 0) {
    return (
      <div className="card p-4 text-xs text-ink-muted">
        No spend recorded yet. Start a project and check back here.
      </div>
    )
  }
  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead className="text-xs text-ink-faint">
          <tr className="text-left">
            <th className="px-4 py-2 font-normal">Project</th>
            <th className="px-4 py-2 font-normal">Tokens</th>
            <th className="px-4 py-2 font-normal text-right">Cost (API)</th>
          </tr>
        </thead>
        <tbody>
          {top.map((s) => (
            <tr key={s.session_id} className="border-t border-line">
              <td className="px-4 py-2 text-ink truncate max-w-[400px]">{s.name}</td>
              <td className="px-4 py-2 text-ink-muted text-xs">
                {compact(s.input_tokens)} in · {compact(s.output_tokens)} out
              </td>
              <td className="px-4 py-2 text-ink text-right font-mono">${s.cost_usd.toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SectionHeader({
  icon: Icon, title, subtitle,
}: { icon: typeof IconBrain; title: string; subtitle?: string }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <Icon size={18} strokeWidth={1.5} className="text-ink-muted" />
      <div>
        <div className="text-sm text-ink">{title}</div>
        {subtitle && <div className="text-[11px] text-ink-faint">{subtitle}</div>}
      </div>
    </div>
  )
}

function Stat({
  label, primary, secondary,
}: { label: string; primary: string; secondary: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-ink-muted">{label}</div>
      <div className="text-2xl text-ink font-medium mt-1">{primary}</div>
      <div className="text-[11px] text-ink-faint mt-1.5">{secondary}</div>
    </div>
  )
}
