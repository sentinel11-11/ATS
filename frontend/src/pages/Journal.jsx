import React, { useState, useEffect } from 'react';
import { Phone, Download, Search, Play } from 'lucide-react';
import { api, getToken } from '../api';
import TimelineModal from '../components/TimelineModal';

export default function Journal({ addToast, refreshKey }) {
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [timelineCallId, setTimelineCallId] = useState(null);

  const loadCalls = async () => {
    setLoading(true);
    const res = await api('/calls', { method: 'POST', body: { limit: 500 } });
    if (res && res.calls) {
      setCalls(Array.isArray(res.calls) ? res.calls : []);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadCalls();
  }, [refreshKey]);

  const handleExportCSV = () => {
    const token = getToken();
    window.open(`/api/v2/export/calls.csv?token=${encodeURIComponent(token)}`, '_blank');
  };

  const callsList = Array.isArray(calls) ? calls : [];

  const filteredCalls = callsList.filter((c) => {
    const q = searchQuery.toLowerCase().trim();
    return (
      !q ||
      (c.contact_phone && c.contact_phone.includes(q)) ||
      (c.contact_name && c.contact_name.toLowerCase().includes(q)) ||
      (c.caller_id && c.caller_id.includes(q))
    );
  });

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-white">Журнал совершенных звонков</h1>
        <button
          onClick={handleExportCSV}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Download className="w-4 h-4" /> Экспорт в CSV
        </button>
      </div>

      <div className="relative">
        <Search className="w-4 h-4 text-dim absolute left-3.5 top-3" />
        <input
          type="text"
          className="w-full bg-[#0a1628] border border-line2 rounded-xl pl-10 pr-4 py-2.5 text-xs text-white outline-none focus:border-acc"
          placeholder="Поиск по номеру телефона или имени клиента..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
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
              <th className="py-2.5 px-3 text-right">Хронология</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="py-8 text-center text-muted">
                  Загрузка журнала вызовов...
                </td>
              </tr>
            ) : filteredCalls.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-8 text-center text-dim">
                  Записи вызовов не найдены.
                </td>
              </tr>
            ) : (
              filteredCalls.map((c) => (
                <tr key={c.id} className="border-b border-line/40 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-mono text-dim">#{c.id}</td>
                  <td className="py-3 px-3 text-muted">{c.started_at?.slice(0, 16).replace('T', ' ')}</td>
                  <td className="py-3 px-3">
                    <span className="font-semibold text-white block">{c.contact_name || c.contact_phone}</span>
                    {c.contact_name && <span className="font-mono text-dim text-[11px]">{c.contact_phone}</span>}
                  </td>
                  <td className="py-3 px-3 font-mono text-muted">{c.caller_id || '—'}</td>
                  <td className="py-3 px-3 text-muted">#{c.campaign_id || 0}</td>
                  <td className="py-3 px-3 text-muted">
                    {c.duration_sec ? `${Math.round(c.duration_sec)} с` : '—'}
                  </td>
                  <td className="py-3 px-3">
                    <span className="badge">{c.result || c.status}</span>
                  </td>
                  <td className="py-3 px-3 text-right">
                    <button
                      onClick={() => setTimelineCallId(c.id)}
                      className="px-2.5 py-1 rounded-lg bg-line/60 hover:bg-line text-white text-xs font-semibold"
                    >
                      ВАТС Логи
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {timelineCallId && (
        <TimelineModal callId={timelineCallId} onClose={() => setTimelineCallId(null)} />
      )}
    </div>
  );
}
