import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('ErrorBoundary caught an unhandled rendering error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-[400px] flex flex-col items-center justify-center p-8 text-center bg-slate-900/60 border border-slate-800 rounded-2xl m-6">
          <div className="p-3 bg-rose-500/10 text-rose-400 rounded-xl border border-rose-500/20 mb-4">
            <AlertTriangle className="w-8 h-8" />
          </div>
          <h2 className="text-lg font-bold text-slate-100 mb-2">Dashboard Display Error</h2>
          <p className="text-sm text-slate-400 max-w-md mb-4 leading-relaxed">
            A rendering error occurred while mounting this component. You can try refreshing the view.
          </p>
          {this.state.error && (
            <div className="font-mono text-xs text-rose-300 bg-slate-950 p-3 rounded-lg border border-slate-800 max-w-xl text-left mb-6 overflow-x-auto">
              {this.state.error.toString()}
            </div>
          )}
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null });
              window.location.reload();
            }}
            className="flex items-center gap-2 px-4 py-2 bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-semibold text-xs rounded-lg transition-colors"
          >
            <RefreshCw className="w-4 h-4" /> Reload Dashboard
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
