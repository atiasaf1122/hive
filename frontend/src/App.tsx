import { useEffect, useState } from 'react'
import { Dashboard } from './components/Dashboard'
import { SessionView } from './components/SessionView'
import { TabBar } from './components/TabBar'
import { useSessionsStore } from './stores/sessions'
import { apiGet } from './ws'

interface ApiSession {
  session_id: string
  name: string
  status: string
  approval_mode: string
  created_at: string
}

export default function App() {
  const { activeSessionId, loadFromApi } = useSessionsStore()
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    apiGet<ApiSession[]>('/api/sessions')
      .then((sessions) => {
        loadFromApi(sessions)
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [loadFromApi])

  if (!loaded) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-gray-400">Connecting to HIVE backend…</div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TabBar />
      <div className="flex-1 overflow-hidden">
        {activeSessionId ? <SessionView sessionId={activeSessionId} /> : <Dashboard />}
      </div>
    </div>
  )
}
