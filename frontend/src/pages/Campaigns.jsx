import React, { useState, useEffect } from 'react';
import { Play, Pause, Square, Plus, Trash2, RotateCcw, ChevronRight } from 'lucide-react';
import { api } from '../api';

export default function Campaigns({ addToast }) {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedCamp, setSelectedCamp] = useState(null);

  const loadCampaigns = async () => {
    const res = await api('/campaigns');
    if (res && res.campaigns) {
      setCampaigns(res.campaigns);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadCampaigns();
  }, []);

  const handleAction = async (id, action) => {
    const res = await api(`/campaigns/${id}/${action}`, { method: 'POST' });
    if (res && res.ok) {
      addToast(action === 'start' ? 'Кампания запущена' : action === 'pause' ? 'Кампания на паузе' : 'Кампания остановлена', 'ok');
      loadCampaigns();
    } else {
      addToast('Ошибка: ' + (res?.error || '?'), 'err');
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Кампании исходящего обзвона</h1>
        <button
          onClick={() => addToast('Используйте диалог создания кампании', 'info')}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Plus className="w-4 h-4" /> Новая кампания
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4">
        {loading ? (
          <div className="text-center py-8 text-muted">Загрузка кампаний...</div>
        ) : campaigns.length === 0 ? (
          <div className="glass-panel p-8 rounded-2xl text-center text-dim">
            Кампаний пока нет. Создайте первую кампанию для начала обзвона.
          </div>
        ) : (
          campaigns.map((c) => (
            <div key={c.id} className="glass-panel p-5 rounded-2xl flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-3">
                  <span className="font-bold text-white text-base">#{c.id} {c.name}</span>
                  <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                    c.status === 'active' ? 'bg-ok/20 text-ok' : c.status === 'paused' ? 'bg-warn/20 text-warn' : 'bg-line text-muted'
                  }`}>
                    {c.status === 'active' ? 'Работает' : c.status === 'paused' ? 'Пауза' : 'Остановлена'}
                  </span>
                </div>
                <p className="text-xs text-muted">
                  Сценарий: <b className="text-white">{c.flow}</b> · Расписание: {c.work_time || 'круглосуточно'}
                </p>
              </div>

              <div className="flex items-center gap-2">
                {c.status !== 'active' && (
                  <button
                    onClick={() => handleAction(c.id, 'start')}
                    className="p-2 rounded-xl bg-ok/20 hover:bg-ok/30 text-ok font-semibold text-xs flex items-center gap-1.5 transition-all"
                  >
                    <Play className="w-4 h-4" /> Запустить
                  </button>
                )}
                {c.status === 'active' && (
                  <button
                    onClick={() => handleAction(c.id, 'pause')}
                    className="p-2 rounded-xl bg-warn/20 hover:bg-warn/30 text-warn font-semibold text-xs flex items-center gap-1.5 transition-all"
                  >
                    <Pause className="w-4 h-4" /> Пауза
                  </button>
                )}
                <button
                  onClick={() => handleAction(c.id, 'stop')}
                  className="p-2 rounded-xl bg-bad/20 hover:bg-bad/30 text-bad font-semibold text-xs flex items-center gap-1.5 transition-all"
                >
                  <Square className="w-4 h-4" /> Стоп
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
