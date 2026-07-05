/**
 * Boot splash.
 *
 * We poll `GET /health` on the backend until it answers — works whether the
 * backend is the WSL `hive start` process (dev) or a future bundled sidecar
 * spawned by the Tauri shell (packaged .msi). The user can't actually tell
 * which is which from here, so the UX just says "Connecting…" → ✓ "Connected"
 * → fade into the dashboard.
 *
 * If `/health` never answers we show an actionable hint instead of a scary
 * "did not start" — the most common cause is "WSL backend isn't running yet".
 */
import { useEffect, useState } from 'react'
import { BACKEND_URL, waitForBackend } from '../lib/api'
import { HiveLogo } from './HiveLogo'

type State = 'connecting' | 'connected' | 'failed'

interface Props {
  onReady: () => void
}

export function Splash({ onReady }: Props) {
  const [state, setState] = useState<State>('connecting')
  const [elapsed, setElapsed] = useState(0)
  // `attempt` is the retry counter — bumping it re-runs the effect that
  // probes the backend. Without this the user is stuck on the failed
  // splash with no way to recover except force-quit.
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState('connecting')
    setElapsed(0)
    const start = Date.now()
    const tick = window.setInterval(() => {
      if (!cancelled) setElapsed(Math.floor((Date.now() - start) / 1000))
    }, 250)

    waitForBackend(45_000)
      .then((ok) => {
        if (cancelled) return
        if (ok) {
          setState('connected')
          // Brief "Connected" flash before handing off to the main UI.
          window.setTimeout(() => onReady(), 350)
        } else {
          setState('failed')
        }
      })
      .finally(() => window.clearInterval(tick))

    return () => {
      cancelled = true
      window.clearInterval(tick)
    }
  }, [onReady, attempt])

  return (
    <div className="h-full w-full flex flex-col items-center justify-center bg-bg select-none px-8">
      <div className={state === 'connecting' ? 'animate-pulse mb-6' : 'mb-6'}>
        <HiveLogo size={72} />
      </div>

      <div className="text-ink text-base font-medium mb-1">HIVE</div>

      <div
        className="text-ink-muted text-sm text-center max-w-md whitespace-pre-line"
        role="status"
        aria-live="polite"
      >
        {state === 'connecting' && (
          <>
            {elapsed < 2
              ? 'Connecting to backend…'
              : `Connecting to backend… ${elapsed}s`}
          </>
        )}

        {state === 'connected' && (
          <span className="text-emerald-500 inline-flex items-center gap-1.5">
            <span aria-hidden>✓</span>
            <span>Connected</span>
          </span>
        )}

        {state === 'failed' && (
          <div className="text-left">
            <div className="text-red-500 mb-2 text-center">
              Couldn't reach the backend
            </div>
            <div className="text-ink-muted text-xs leading-relaxed">
              Tried <code className="text-ink">{BACKEND_URL}</code> for ~45 s.
              {'\n\n'}
              If you're developing with the backend in WSL, open a WSL
              terminal and run:{'\n'}
              <code className="text-ink">  hive start</code>
              {'\n\n'}
              The packaged .msi ships its own backend, so this only affects
              the dev workflow.
            </div>
            <div className="flex gap-2 mt-4 justify-center">
              <button
                type="button"
                className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90"
                onClick={() => setAttempt((n) => n + 1)}
              >
                Retry
              </button>
              <button
                type="button"
                className="px-3 py-1.5 rounded border border-surface-2 text-sm hover:bg-surface-1"
                onClick={() => onReady()}
              >
                Continue offline
              </button>
            </div>
          </div>
        )}
      </div>

      {state !== 'failed' && (
        <div className="mt-8 w-32 h-1 rounded-full bg-surface-2 overflow-hidden">
          <div className="h-full bg-accent-gradient animate-[loading_1.8s_ease-in-out_infinite]" />
        </div>
      )}

      <style>{`
        @keyframes loading {
          0%   { width: 0%;  margin-left: 0%; }
          50%  { width: 60%; margin-left: 20%; }
          100% { width: 0%;  margin-left: 100%; }
        }
      `}</style>
    </div>
  )
}
