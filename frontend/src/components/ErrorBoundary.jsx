import React from 'react';
import { AlertTriangle, RefreshCw, LogOut } from 'lucide-react';
import { setSession } from '../api';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('React ErrorBoundary caught error:', error, errorInfo);
  }

  handleReset = () => {
    setSession('', null);
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-[#070d17] text-white">
          <div className="max-w-md w-full bg-[#0f1d32] border border-bad/30 rounded-3xl p-8 text-center flex flex-col items-center gap-4 shadow-2xl">
            <div className="w-14 h-14 rounded-2xl bg-bad/20 border border-bad/40 text-bad flex items-center justify-center">
              <AlertTriangle className="w-8 h-8" />
            </div>
            <h1 className="text-xl font-bold text-white">Произошла ошибка при загрузке интерфейса</h1>
            <p className="text-xs text-muted leading-relaxed">
              {this.state.error?.message || 'Не удалось отрисовать компонент интерфейса.'}
            </p>
            <div className="flex flex-col sm:flex-row gap-3 w-full mt-2">
              <button
                onClick={() => window.location.reload()}
                className="flex-1 py-2.5 rounded-xl bg-line hover:bg-line2 font-semibold text-xs text-white flex items-center justify-center gap-2"
              >
                <RefreshCw className="w-4 h-4" /> Перезагрузить
              </button>
              <button
                onClick={this.handleReset}
                className="flex-1 py-2.5 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-xs text-white flex items-center justify-center gap-2 shadow-lg shadow-acc/30"
              >
                <LogOut className="w-4 h-4" /> Сбросить сессию
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
