import { useSessionsStore } from '../stores/sessions'

const statusDot: Record<string, string> = {
  running: 'bg-green-400',
  spawning: 'bg-green-400',
  planning: 'bg-blue-400 animate-pulse',
  waiting_approval: 'bg-yellow-400 animate-pulse',
  completed: 'bg-gray-500',
  failed: 'bg-red-500',
  starting: 'bg-blue-400 animate-pulse',
  cancelled: 'bg-gray-600',
}

export function TabBar() {
  const { sessions, activeSessionId, setActiveSession } = useSessionsStore()
  const sessionList = Object.values(sessions)

  return (
    <header className="flex items-center gap-0 bg-gray-950 border-b border-gray-800 h-10 px-3 shrink-0">
      <span className="text-violet-400 font-bold mr-4 text-sm tracking-widest">HIVE</span>

      <div className="flex items-center gap-1 overflow-x-auto flex-1">
        {sessionList.map((s) => (
          <button
            key={s.id}
            onClick={() => setActiveSession(activeSessionId === s.id ? null : s.id)}
            className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs whitespace-nowrap transition-colors ${
              activeSessionId === s.id
                ? 'bg-gray-800 text-white'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${statusDot[s.status] ?? 'bg-gray-600'}`} />
            <span className="max-w-[120px] truncate">{s.name || s.id}</span>
          </button>
        ))}

        <button
          onClick={() => setActiveSession(null)}
          className="px-2 py-1 text-xs text-gray-500 hover:text-gray-300 ml-1"
        >
          + New
        </button>
      </div>

      <div className="text-xs text-gray-600 ml-2">
        {sessionList.filter(s => ['running', 'planning', 'spawning'].includes(s.status)).length > 0
          ? `${sessionList.filter(s => ['running', 'planning', 'spawning'].includes(s.status)).length} active`
          : null}
      </div>
    </header>
  )
}
