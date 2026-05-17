const WS_BASE =
  typeof window !== 'undefined'
    ? `ws://${window.location.host}`
    : 'ws://localhost:8765'

export function connectSession(
  sessionId: string,
  onEvent: (event: Record<string, unknown>) => void,
): () => void {
  const ws = new WebSocket(`${WS_BASE}/ws/${sessionId}`)

  ws.onmessage = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data as string) as Record<string, unknown>
      if (data['type'] !== 'ping') {
        onEvent(data)
      }
    } catch {
      // ignore malformed frames
    }
  }

  ws.onerror = () => console.warn('WS error for session', sessionId)

  return () => ws.close()
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`${res.status}: ${err}`)
  }
  return res.json() as Promise<T>
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(String(res.status))
  return res.json() as Promise<T>
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(String(res.status))
  return res.json() as Promise<T>
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(path, { method: 'DELETE' })
  if (!res.ok) throw new Error(String(res.status))
}
