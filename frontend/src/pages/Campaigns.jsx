import React, { useState, useEffect } from 'react';
import { Play, Pause, Square, Plus, Trash2, Edit2, RotateCcw } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function Campaigns({ addToast }) {
  const [campaigns, setCampaigns] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [databases, setDatabases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingCamp, setEditingCamp] = useState(null); // null = modal closed

  const loadData = async () => {
    setLoading(true);
    const [cRes, tRes, dRes] = await Promise.all([
      api('/campaigns'),
      api('/templates'),
      api('/databases')
    ]);

    if (cRes && cRes.campaigns) setCampaigns(cRes.campaigns);
    if (tRes && tRes.templates) setTemplates(tRes.templates);
    if (dRes && dRes.databases) setDatabases(dRes.databases);
    setLoading(false);
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleOpenEdit = (camp) => {
    const c = camp || {
      id: 0,
      name: '',
      flow: 'operator',
      template_id: templates[0]?.id || 1,
      database_id: databases[0]?.id || 0,
      work_time: '08:00-20:00',
      retry_delay_min: 15,
      retry_max: 2,
    };
    setEditingCamp(c);
  };

  const handleSave = async () => {
    if (!editingCamp || !editingCamp.name) return addToast('Укажите название кампании', 'warn');
    const res = await api('/campaigns/save', {
      body: editingCamp,
    });

    if (res && res.ok) {
      addToast('Кампания сохранена', 'ok');
      setEditingCamp(null);
      loadData();
    } else {
      addToast('Ошибка сохранения: ' + (res?.error || '?'), 'err');
    }
  };

  const handleAction = async (id, action) => {
    const res = await api(`/campaigns/${id}/${action}`, { method: 'POST' });
    if (res && res.ok) {
      addToast(
        action === 'start' ? 'Кампания запущена' : action === 'pause' ? 'Кампания на паузе' : 'Кампания остановлена',
        'ok'
      );
      loadData();
    } else {
      addToast('Ошибка: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Удалить кампанию? Все звонки кампании отвяжутся.')) return;
    const res = await api('/campaigns/clear_all', { body: { id } });
    if (res && res.ok) {
      addToast('Кампания удалена', 'ok');
      loadData();
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Кампании исходящего обзвона</h1>
        <button
          onClick={() => handleOpenEdit(null)}
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
            Кампаний пока нет. Нажмите «Новая кампания» для настройки обзвона.
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
                  Сценарий: <b className="text-white uppercase">{c.flow}</b> · Расписание: {c.work_time || '08:00-20:00'}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleOpenEdit(c)}
                  className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white font-semibold text-xs flex items-center gap-1"
                >
                  <Edit2 className="w-3.5 h-3.5" /> Ред.
                </button>

                {c.status !== 'active' && (
                  <button
                    onClick={() => handleAction(c.id, 'start')}
                    className="px-3 py-1.5 rounded-xl bg-ok/20 hover:bg-ok/30 text-ok font-semibold text-xs flex items-center gap-1 transition-all"
                  >
                    <Play className="w-3.5 h-3.5" /> Запустить
                  </button>
                )}
                {c.status === 'active' && (
                  <button
                    onClick={() => handleAction(c.id, 'pause')}
                    className="px-3 py-1.5 rounded-xl bg-warn/20 hover:bg-warn/30 text-warn font-semibold text-xs flex items-center gap-1 transition-all"
                  >
                    <Pause className="w-3.5 h-3.5" /> Пауза
                  </button>
                )}
                <button
                  onClick={() => handleAction(c.id, 'stop')}
                  className="px-3 py-1.5 rounded-xl bg-bad/20 hover:bg-bad/30 text-bad font-semibold text-xs flex items-center gap-1 transition-all"
                >
                  <Square className="w-3.5 h-3.5" /> Стоп
                </button>
                <button
                  onClick={() => handleDelete(c.id)}
                  className="p-1.5 rounded-lg text-muted hover:text-bad hover:bg-bad/10"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Edit / New Campaign Modal */}
      <Modal
        isOpen={!!editingCamp}
        onClose={() => setEditingCamp(null)}
        title={editingCamp?.id ? `Кампания #${editingCamp.id}` : 'Новая кампания'}
      >
        {editingCamp && (
          <div className="flex flex-col gap-4 text-xs">
            <div>
              <label className="font-semibold text-muted block mb-1">Название кампании</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm text-white outline-none focus:border-acc"
                value={editingCamp.name || ''}
                onChange={(e) => setEditingCamp({ ...editingCamp, name: e.target.value })}
                placeholder="Кампания #1 Обзвон клиентов"
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Сценарий (Flow)</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.flow || 'operator'}
                  onChange={(e) => setEditingCamp({ ...editingCamp, flow: e.target.value })}
                >
                  <option value="operator">Перевод на оператора (ACD)</option>
                  <option value="agent">ИИ-агент (Сценарий бота L1)</option>
                  <option value="message">Озвучка сообщения (Шаблон)</option>
                </select>
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Шаблон озвучки / бота</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.template_id || ''}
                  onChange={(e) => setEditingCamp({ ...editingCamp, template_id: parseInt(e.target.value) || 0 })}
                >
                  <option value={0}>Без шаблона</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Рабочее время</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.work_time || '08:00-20:00'}
                  onChange={(e) => setEditingCamp({ ...editingCamp, work_time: e.target.value })}
                  placeholder="08:00-20:00"
                />
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Интервал перезвона (мин)</label>
                <input
                  type="number"
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.retry_delay_min || 15}
                  onChange={(e) => setEditingCamp({ ...editingCamp, retry_delay_min: parseInt(e.target.value) || 15 })}
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
              <button
                onClick={() => setEditingCamp(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold text-xs"
              >
                Отмена
              </button>
              <button
                onClick={handleSave}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
              >
                Сохранить кампанию
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
