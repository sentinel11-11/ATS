import React from 'react';
import { Search, Radio, Headphones, RefreshCw } from 'lucide-react';

export default function Topbar({ user, opStatus, setOpStatus, onRefresh, activeCallsCount }) {
  const isOperator = user?.role === 'operator';

  return (
    <header className="sticky top-0 z-10 flex items-center justify-between px-6 py-3 bg-[#070d17]/80 backdrop-blur-md border-b border-line">
      {/* Search / Command trigger */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 bg-[#0a1628] border border-line2 rounded-xl px-3 py-1.5 text-xs text-muted">
          <Search className="w-3.5 h-3.5 text-dim" />
          <span>Быстрый поиск...</span>
          <kbd className="bg-[#0e1c31] border border-line2 rounded px-1.5 text-[10px] text-muted ml-4">Ctrl+K</kbd>
        </div>

        {activeCallsCount > 0 && (
          <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-acc/20 border border-acc/40 text-acc2 text-xs font-semibold pulse-active">
            <Radio className="w-3.5 h-3.5" />
            <span>Активных вызовов: {activeCallsCount}</span>
          </div>
        )}
      </div>

      {/* Actions / Status */}
      <div className="flex items-center gap-3">
        {/* Operator Quick Status Toggle */}
        {isOperator && (
          <div className="flex bg-[#0a1628] border border-line rounded-xl p-1 gap-1">
            <button
              onClick={() => setOpStatus('free')}
              className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                opStatus === 'free' ? 'bg-gradient-to-r from-[#22c68c] to-[#159a6e] text-white shadow-sm' : 'text-muted hover:text-white'
              }`}
            >
              Свободен
            </button>
            <button
              onClick={() => setOpStatus('break')}
              className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                opStatus === 'break' ? 'bg-gradient-to-r from-[#ffd166] to-[#f0a832] text-[#3a2800] shadow-sm' : 'text-muted hover:text-white'
              }`}
            >
              Перерыв
            </button>
          </div>
        )}

        <button
          onClick={onRefresh}
          title="Обновить данные"
          className="p-2 rounded-xl bg-[#0a1628] border border-line2 text-muted hover:text-white hover:border-acc transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
