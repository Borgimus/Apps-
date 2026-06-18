import { Component } from 'react'
import type { ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-bg-primary p-8">
          <div className="max-w-lg w-full bg-bg-secondary border border-bear/40 rounded-lg p-6">
            <h1 className="text-bear font-semibold text-lg mb-2">Application Error</h1>
            <p className="text-text-secondary text-sm mb-4">
              The app crashed during render. Open DevTools (F12 → Console) for details.
            </p>
            <pre className="bg-bg-tertiary rounded p-3 text-xs text-text-muted overflow-auto max-h-48">
              {this.state.error.message}
              {'\n'}
              {this.state.error.stack}
            </pre>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 px-4 py-2 bg-accent text-white rounded text-sm hover:bg-accent/80 transition-colors"
            >
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
