import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Optional override for the wrapped content, useful for tests. */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface State {
  error: Error | null
  componentStack: string
}

/**
 * Root error boundary. Without this, an unhandled render exception inside
 * any page (Skills, Plugins, Usage, Project) replaces the whole window
 * with a blank canvas — the user has no way to recover except force-quit.
 *
 * The fallback shows the message + stack and offers "Reload window" plus
 * "Copy details" so users can paste the error into a bug report.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ componentStack: info.componentStack ?? '' })
    // Still surface to devtools so unhandled errors are visible during dev.
    console.error('ErrorBoundary caught:', error, info)
  }

  reset = (): void => {
    this.setState({ error: null, componentStack: '' })
  }

  render(): ReactNode {
    const { error, componentStack } = this.state
    if (!error) return this.props.children

    if (this.props.fallback) return this.props.fallback(error, this.reset)

    const details = `${error.name}: ${error.message}\n\n${error.stack ?? ''}\n\nComponent stack:${componentStack}`

    return (
      <div className="h-full w-full flex items-center justify-center bg-bg text-ink p-8">
        <div className="card max-w-2xl w-full p-6">
          <div className="text-lg font-medium text-red-500 mb-2">Something broke in the UI</div>
          <div className="text-sm text-ink-muted mb-4">
            HIVE caught a render error so the whole window didn't blank out. The backend is unaffected.
          </div>
          <pre className="text-xs bg-surface-2 rounded p-3 overflow-auto max-h-64 whitespace-pre-wrap">
            {details}
          </pre>
          <div className="flex gap-2 mt-4">
            <button
              type="button"
              className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90"
              onClick={() => window.location.reload()}
            >
              Reload window
            </button>
            <button
              type="button"
              className="px-3 py-1.5 rounded border border-surface-2 text-sm hover:bg-surface-1"
              onClick={() => {
                void navigator.clipboard.writeText(details)
              }}
            >
              Copy details
            </button>
            <button
              type="button"
              className="px-3 py-1.5 rounded border border-surface-2 text-sm hover:bg-surface-1"
              onClick={this.reset}
            >
              Try to recover
            </button>
          </div>
        </div>
      </div>
    )
  }
}
