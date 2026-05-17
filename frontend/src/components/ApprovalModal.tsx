import { useState } from 'react'
import { useSessionsStore } from '../stores/sessions'
import { apiPost } from '../ws'
import type { TeamMember } from '../types'

interface Props {
  sessionId: string
}

export function ApprovalModal({ sessionId }: Props) {
  const { sessions, handleWsEvent } = useSessionsStore()
  const session = sessions[sessionId]
  const interrupt = session?.interrupt
  const [submitting, setSubmitting] = useState(false)

  if (!interrupt || interrupt.type !== 'team_approval') return null

  const { team_composition, confidence, reason } = interrupt
  const pct = Math.round(confidence * 100)

  async function respond(approved: boolean) {
    setSubmitting(true)
    try {
      await apiPost(`/api/sessions/${sessionId}/approve`, { approved })
      handleWsEvent(sessionId, { type: approved ? 'session_start' : 'session_end', status: approved ? undefined : 'cancelled' })
    } catch (err) {
      console.error('Approval failed', err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="absolute inset-0 bg-black/60 flex items-center justify-center z-50 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md mx-4 shadow-2xl">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-yellow-400 text-xl">⚠</span>
          <h2 className="text-white font-semibold">Approval Required</h2>
        </div>

        <div className="mb-4 text-sm text-gray-300">
          <span className="text-gray-500">Reason: </span>
          {reason === 'low_confidence' ? `Low confidence (${pct}%)` : `Approval mode: ${session?.approvalMode}`}
        </div>

        <div className="mb-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Proposed Team</div>
          <div className="space-y-1.5">
            {team_composition.team.map((m: TeamMember, i: number) => (
              <div key={i} className="flex items-center justify-between bg-gray-800 rounded px-3 py-1.5 text-sm">
                <div className="flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${m.passive ? 'bg-gray-500' : 'bg-violet-400'}`} />
                  <span className="text-white">{m.role}</span>
                  {m.passive && <span className="text-xs text-gray-500">(passive)</span>}
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <span>×{m.count}</span>
                  <span>{m.model}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center mb-6 text-sm">
          <span className="text-gray-500">Confidence:</span>
          <div className="ml-2 flex-1 bg-gray-800 rounded-full h-1.5">
            <div
              className={`h-full rounded-full ${pct >= 70 ? 'bg-green-500' : pct >= 50 ? 'bg-yellow-500' : 'bg-red-500'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="ml-2 text-white">{pct}%</span>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => respond(true)}
            disabled={submitting}
            className="flex-1 bg-violet-600 hover:bg-violet-500 disabled:bg-gray-700 text-white py-2 rounded-lg text-sm font-medium transition-colors"
          >
            ✓ Approve
          </button>
          <button
            onClick={() => respond(false)}
            disabled={submitting}
            className="flex-1 bg-gray-800 hover:bg-gray-700 disabled:bg-gray-800 text-gray-300 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            ✗ Reject
          </button>
        </div>
      </div>
    </div>
  )
}
