/**
 * Bottom strip on the dashboard — last 7 days of cost + tiny sparkline.
 * Clicking the strip jumps to the Usage tab.
 *
 * The Max subscription doesn't bill in dollars; Phase 9C's Usage page splits
 * Claude / external API / Ollama into honest sub-sections. Here we just show
 * what we have today: aggregated cost_usd from the cost_log table.
 */
import { IconChartHistogram } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'
import type { CostSummary } from '../../lib/types'

function fmtUsd(v: number): string {
  if (v < 0.01) return '$0.00'
  return `$${v.toFixed(2)}`
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length === 0) {
    return <div className="h-8 w-32 rounded bg-surface-2" />
  }
  const max = Math.max(0.0001, ...values)
  const w = 132
  const h = 32
  const step = values.length > 1 ? w / (values.length - 1) : 0
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`)
    .join(' ')
  return (
    <svg width={w} height={h} className="overflow-visible">
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#F5A623" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#F5A623" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline
        points={points}
        fill="none"
        stroke="rgb(245 166 35)"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {values.length > 1 && (
        <polygon
          points={`0,${h} ${points} ${w},${h}`}
          fill="url(#spark-grad)"
        />
      )}
    </svg>
  )
}

export function UsageStrip() {
  const [summary, setSummary] = useState<CostSummary | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    api
      .get<CostSummary>('/api/cost/summary?days=7')
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const values = summary?.by_day.map((d) => d.cost_usd) ?? []

  return (
    <button
      type="button"
      onClick={() => navigate('/usage')}
      className="card card-hover w-full text-left p-4 flex items-center gap-4"
    >
      <div className="w-9 h-9 rounded-soft bg-surface-2 flex items-center justify-center text-ink-muted">
        <IconChartHistogram size={18} strokeWidth={1.5} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-ink-faint">Last 7 days</div>
        <div className="text-ink text-sm">
          {summary ? `${fmtUsd(summary.total_cost_usd)} on API · ${summary.by_session.length} active project${summary.by_session.length === 1 ? '' : 's'}` : 'Loading usage…'}
        </div>
      </div>
      <Sparkline values={values} />
      <div className="text-xs text-ink-faint">Open usage →</div>
    </button>
  )
}
