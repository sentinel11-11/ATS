import React, { useState, useEffect } from 'react';
import { Download, Play, RefreshCw, FileText } from 'lucide-react';
import { api, getToken } from '../api';

export default function Journal({ addToast }) {
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadCalls = async () => {
    setLoading(true);
    const res = await api('/calls?limit=100');
    if (res && res.calls) {
      setCalls(res.calls);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadCalls();
  }, []);

  const handleExport = async () => {
    const token = getToken();
    const res = await fetch('/api/v2/export/calls.csv', {
      headers: { 'X-Ats-Token': token },
    });
    if (!res.ok) return addToast('Ошибка экспорта', 'err');
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'calls_journal.csv';
    a.click();
    addToast('Журнал сохранён в CSV', 'ok');
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Журнал совершенных звонков</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={loadCalls}
            className="p-2 rounded-xl bg-[#0a1628] border border-line text-muted hover:text-white transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={handleExport}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
          >
            <Download className="w-4 h-4" /> Экспорт в CSV
          </button>
        </div>
      </div>

      <div className="glass-panel p-6 rounded-2xl overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-line text-muted uppercase text-[10px]">
              <th className="py-2.5 px-3"># ID</th>
              <th className="py-2.5 px-3">Время</th>
              <th className="py-2.5 px-3">Контакт</th>
              <th className="py-2.5 px-3">С номера</th>
              <th className="py-2.5 px-3">Кампания</th>
              <th className="py-2.5 px-3">Длительность</th>
              <th className="py-2.5 px-3">Результат</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-muted">
                  Загрузка истории...
                </td>
              </tr>
            ) : calls.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-dim">
                  Журнал пока пуст.
                </td>
              </tr>
            ) : (
              calls.map((c) => (
                <tr key={c.id} className="border-b border-line/50 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-mono text-dim">#{c.id}</td>
                  <td className="py-3 px-3 text-muted">{c.started_at?.slice(0, 16).replace('T', ' ')}</td>
                  <td className="py-3 px-3 font-semibold text-white">
                    {c.contact_name || c.contact_phone}
                    <span className="block text-[10px] text-dim font-normal">{c.contact_phone}</span>
                  </td>
                  <td className="py-3 px-3 text-muted">{c.caller_id || '—'}</td>
                  <td className="py-3 px-3 text-muted">#{c.campaign_id || 0}</td>
                  <td className="py-3 px-3 text-muted">{c.duration_sec ? `${Math.round(c.duration_sec)} с` : '—'}</td>
                  <td className="py-3 px-3">
                    <span className="px-2.5 py-0.5 rounded-full bg-line text-muted text-[11px] font-medium">
                      {c.result || c.status}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
