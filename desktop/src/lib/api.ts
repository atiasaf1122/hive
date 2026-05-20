/**
 * Backend client — talks to FastAPI at localhost:8765 (the Python sidecar).
 * The base URL is fixed because Tauri runs the backend as a child process.
 */
export const BACKEND_URL = 'http://127.0.0.1:8765'

export interface ApiError extends Error {
  status?: number
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    const err = new Error(`${res.status} ${detail}`) as ApiError
    err.status = res.status
    throw err
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}

export interface HealthResponse {
  status: string
  version: string
}

export async function waitForBackend(timeoutMs = 30_000): Promise<boolean> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${BACKEND_URL}/health`)
      if (res.ok) return true
    } catch {
      // backend not up yet — keep polling
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  return false
}
