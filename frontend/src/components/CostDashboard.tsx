import { useEffect, useState } from 'react'
import { apiGet } from '../ws'

interface SessionCost {
  session_id: string
  name: string
  cost_usd: number
  input_tokens: number
  output_tokens: number
}

interface DailyCost {
  date: string
  cost_usd: number
}

interface CostSummary {
  days: number
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
  by_session: SessionCost[]
  by_day: DailyCost[]
}

function fmtUsd(v: number): string {
  return `$${v.toFixed(4)}`
}

function fmtCompactInt(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return String(v)
}

export function CostDashboard() {
  const [summary, setSummary] = useState<CostSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      apiGet<CostSummary>('/api/cost/summary?days=7')
        .then((data) => {
          if (!cancelled) setSummary(data)
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'failed')
        })
    }
    load()
    const id = window.setInterval(load, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  if (error) {
    return (
      <div className="rounded-lg bg-gray-900 border border-gray-800 p-3 text-xs text-red-400">
        Cost data error: {error}
      </div>
    )
  }

  if (!summary) {
    return (
      <div className="rounded-lg bg-gray-900 border border-gray-800 p-3 text-xs text-gray-600">
        Loading cost summary…
      </div>
    )
  }

  const maxDaily = Math.max(0.0001, ...summary.by_day.map((d) => d.cost_usd))

  return (
    <div className="rounded-lg bg-gray-900 border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-gray-300">Cost — last {summary.days} days</h2>
        <span className="text-violet-300 text-sm font-mono">{fmtUsd(summary.total_cost_usd)}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs text-gray-500 mb-4">
        <div>
          input: <span className="text-gray-300">{fmtCompactInt(summary.total_input_tokens)}</span>
        </div>
        <div>
          output: <span className="text-gray-300">{fmtCompactInt(summary.total_output_tokens)}</span>
        </div>
      </div>

      {/* daily bars */}
      <div className="mb-4">
        <div className="text-xs text-gray-600 mb-2">Daily</div>
        {summary.by_day.length === 0 ? (
          <div className="text-xs text-gray-700">no spend recorded</div>
        ) : (
          <div className="space-y-1">
            {summary.by_day.map((d) => {
              const pct = (d.cost_usd / maxDaily) * 100
              return (
                <div key={d.date} className="flex items-center gap-2 text-xs">
                  <div className="text-gray-600 w-20 font-mono shrink-0">{d.date.slice(5)}</div>
                  <div className="flex-1 h-1.5 bg-gray-800 rounded overflow-hidden">
                    <div className="h-full bg-violet-500/70" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="text-gray-500 w-16 text-right font-mono">{fmtUsd(d.cost_usd)}</div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* top sessions */}
      <div>
        <div className="text-xs text-gray-600 mb-2">Top sessions</div>
        {summary.by_session.length === 0 ? (
          <div className="text-xs text-gray-700">no sessions with cost yet</div>
        ) : (
          <ul className="space-y-1">
            {summary.by_session.map((s) => (
              <li key={s.session_id} className="flex items-center gap-2 text-xs">
                <span className="text-gray-300 truncate flex-1" title={s.name}>{s.name}</span>
                <span className="text-gray-600 font-mono">{fmtUsd(s.cost_usd)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
