/**
 * Per-session WebSocket helper.
 *
 * `subscribeSession(id, onEvent)` opens a socket to /ws/{id}, reconnects
 * with exponential backoff on disconnect, and returns a `close()` handle.
 *
 * Phase 10 hardening (Section 8.2): the helper remembers the highest
 * `event_id` it has seen for this session and, on every reconnect,
 * sends `{"resume_from": <last_seen_id>}` as the first frame. The
 * backend (`backend/api/ws.py`) replays any retained events since
 * that id before the live stream resumes.
 *
 * `event_id` is server-assigned in `backend/api/event_bus.py:emit`. If
 * the field is missing (older backend or non-emit-routed event), the
 * client just keeps the last value it had.
 */
import { BACKEND_URL } from './api'
import type { WSEvent } from './types'

function wsUrl(sessionId: string): string {
  return BACKEND_URL.replace('http://', 'ws://').replace('https://', 'wss://') + `/ws/${sessionId}`
}

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

export interface Subscription {
  close: () => void
  /** The highest event_id this subscription has seen (for diagnostics). */
  lastEventId: () => number
}

export function subscribeSession(
  sessionId: string,
  onEvent: (e: WSEvent) => void,
  onStatus?: (s: WsStatus) => void,
): Subscription {
  let closed = false
  let socket: WebSocket | null = null
  let retryMs = 500
  let lastEventId = 0

  const open = () => {
    if (closed) return
    onStatus?.('connecting')
    socket = new WebSocket(wsUrl(sessionId))

    socket.onopen = () => {
      retryMs = 500
      onStatus?.('open')
      // Send the resume handshake first thing. The server has a short
      // window to receive it; missing the window just means "no replay"
      // — both sides degrade gracefully.
      try {
        socket?.send(JSON.stringify({ resume_from: lastEventId }))
      } catch {
        /* swallow — we'll catch the disconnect via onclose */
      }
    }
    socket.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data) as WSEvent & { event_id?: number }
        if (typeof parsed.event_id === 'number' && parsed.event_id > lastEventId) {
          lastEventId = parsed.event_id
        }
        onEvent(parsed)
      } catch {
        /* drop malformed payload */
      }
    }
    socket.onerror = () => onStatus?.('error')
    socket.onclose = () => {
      onStatus?.('closed')
      if (!closed) {
        window.setTimeout(open, retryMs)
        retryMs = Math.min(retryMs * 2, 8000)
      }
    }
  }

  open()

  return {
    close() {
      closed = true
      socket?.close()
    },
    lastEventId() {
      return lastEventId
    },
  }
}
