import { useState } from 'react'
import { useSessionsStore } from '../stores/sessions'
import { apiPost } from '../ws'

interface Props {
  sessionId: string
}

export function InputSection({ sessionId }: Props) {
  const { sessions } = useSessionsStore()
  const [text, setText] = useState('')
  const [urgency, setUrgency] = useState<'question' | 'correction' | 'urgent' | 'broadcast'>('question')
  const session = sessions[sessionId]

  if (!session || !['running', 'spawning', 'planning'].includes(session.status)) return null

  async function send(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim()) return
    try {
      await apiPost(`/api/sessions/${sessionId}/message`, {
        text: text.trim(),
        agent_id: 'orchestrator',
        urgency,
      })
      setText('')
    } catch (err) {
      console.error('Send message failed', err)
    }
  }

  const urgencyButtons: Array<{ key: typeof urgency; label: string; title: string }> = [
    { key: 'question', label: '💬', title: 'Ask (async, non-interrupting)' },
    { key: 'correction', label: '✏️', title: 'Correction (high priority)' },
    { key: 'urgent', label: '⛔', title: 'Stop urgent' },
    { key: 'broadcast', label: '⚡', title: 'Broadcast to all agents' },
  ]

  return (
    <form onSubmit={send} className="flex items-center gap-2 p-2 border-t border-gray-800 bg-gray-950">
      <div className="flex gap-1">
        {urgencyButtons.map((b) => (
          <button
            key={b.key}
            type="button"
            title={b.title}
            onClick={() => setUrgency(b.key)}
            className={`text-base px-1.5 py-0.5 rounded transition-colors ${
              urgency === b.key ? 'bg-gray-700' : 'hover:bg-gray-800'
            }`}
          >
            {b.label}
          </button>
        ))}
      </div>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Message to orchestrator…"
        className="flex-1 bg-gray-900 border border-gray-800 rounded px-3 py-1.5 text-sm text-white placeholder-gray-600 outline-none focus:border-gray-700"
      />
      <button
        type="submit"
        disabled={!text.trim()}
        className="bg-gray-800 hover:bg-gray-700 disabled:opacity-40 text-white px-3 py-1.5 rounded text-sm transition-colors"
      >
        →
      </button>
    </form>
  )
}
