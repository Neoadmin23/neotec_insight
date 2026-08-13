import { Component, type ReactNode } from 'react';

/** v2.39.1 — a render crash in the results grid must never white-page the
 *  whole app. This boundary shows the actual error so a screenshot of the
 *  failure IS the diagnosis. */
export default class GridErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  constructor(props: { children: ReactNode }) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err: Error) { return { err }; }
  componentDidCatch(err: Error, info: any) { console.error('Grid render error:', err, info); }
  render() {
    if (this.state.err) {
      return (
        <div style={{ margin: 16, padding: 16, border: '2px solid #b3261e', borderRadius: 10, background: '#fdecea', color: '#7a1712', fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap' }}>
          <strong style={{ fontFamily: 'inherit' }}>Report render error (please screenshot this box):</strong>
          {'\n\n'}{String(this.state.err?.message || this.state.err)}
          {'\n\n'}{String(this.state.err?.stack || '').split('\n').slice(0, 6).join('\n')}
          {'\n\n'}<button onClick={() => this.setState({ err: null })} style={{ fontFamily: 'inherit' }}>Try re-render</button>
        </div>
      );
    }
    return this.props.children;
  }
}
