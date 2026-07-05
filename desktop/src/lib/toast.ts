/**
 * Tiny dependency-free toast util.
 *
 * Why hand-rolled instead of adding `sonner` / `react-hot-toast`: the only
 * thing the codebase needs today is `toast.error(...)` and a friendly
 * banner; we don't have any other toast use cases. Adding a library would
 * be ~30 KB of JS we don't otherwise need. If toast volume grows past a
 * single error banner, swap this for `sonner` and the call sites stay the
 * same.
 */

export type ToastKind = 'error' | 'success' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
  /** Auto-dismiss after this many ms. 0 = sticky (manual close). */
  ttl: number
}

type Subscriber = (toasts: readonly Toast[]) => void

let nextId = 1
let toasts: Toast[] = []
const subscribers = new Set<Subscriber>()

function emit(): void {
  const snapshot = toasts.slice()
  for (const sub of subscribers) sub(snapshot)
}

function push(kind: ToastKind, message: string, ttl: number): number {
  const id = nextId++
  toasts = [...toasts, { id, kind, message, ttl }]
  emit()
  if (ttl > 0) {
    window.setTimeout(() => dismiss(id), ttl)
  }
  return id
}

export function dismiss(id: number): void {
  toasts = toasts.filter((t) => t.id !== id)
  emit()
}

export const toast = {
  error: (message: string, ttl = 6000) => push('error', message, ttl),
  success: (message: string, ttl = 3000) => push('success', message, ttl),
  info: (message: string, ttl = 4000) => push('info', message, ttl),
}

export function subscribeToToasts(sub: Subscriber): () => void {
  subscribers.add(sub)
  sub(toasts.slice())
  return () => {
    subscribers.delete(sub)
  }
}
