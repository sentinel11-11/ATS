import React, { useState, useEffect } from 'react';
import { PhoneCall, Plus, Shield, RefreshCw } from 'lucide-react';
import { api } from '../api';

export default function NumberPool({ addToast }) {
  const [numbers, setNumbers] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadNumbers = async () => {
    setLoading(true);
    const res = await api('/numbers');
    if (res && res.numbers) {
      setNumbers(res.numbers);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadNumbers();
  }, []);

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Пул номеров Caller ID (Ротация & Антиспам)</h1>
        <button
          onClick={() => addToast('Создание номера', 'info')}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Plus className="w-4 h-4" /> Добавить номер
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {loading ? (
          <div className="text-center py-8 text-muted col-span-3">Загрузка номеров...</div>
        ) : numbers.length === 0 ? (
          <div className="glass-panel p-8 rounded-2xl text-center text-dim col-span-3">
            Пул номеров пуст. Добавьте исходящие номера для ротации Caller ID.
          </div>
        ) : (
          numbers.map((n) => (
            <div key={n.id} className="glass-panel p-5 rounded-2xl flex flex-col justify-between gap-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-base font-bold text-white">{n.number}</span>
                <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                  n.active ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad'
                }`}>
                  {n.active ? 'Активен' : 'Отключен'}
                </span>
              </div>
              <p className="text-xs text-muted">
                Название: {n.label || '—'} · Провайдер: <b className="text-white">{n.provider}</b>
              </p>
              <div className="text-[11px] text-dim border-t border-line pt-2">
                Дневной лимит: {n.daily_limit || 100} звонков
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
