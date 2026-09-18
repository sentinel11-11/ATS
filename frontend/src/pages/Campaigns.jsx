import React, { useState, useEffect } from 'react';
import {
  Play,
  Pause,
  Square,
  Plus,
  Trash2,
  Edit2,
  RotateCcw,
  ChevronDown,
  ChevronUp,
  Clock,
  PhoneCall,
  Users,
  CheckCircle2,
  AlertCircle,
  Radio,
  FileSpreadsheet
} from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';
import TimelineModal from '../components/TimelineModal';

export default function Campaigns({ addToast }) {
  const [campaigns, setCampaigns] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [databases, setDatabases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingCamp, setEditingCamp] = useState(null); // null = modal closed
  const [expandedCampId, setExpandedCampId] = useState(null); // campaign ID expanded
  const [campDetailData, setCampDetailData] = useState(null);
  const [detailLoading, setCampDetailLoading] = useState(false);
  const [timelineCallId, setTimelineCallId] = useState(null);
  const [addContactsModalCampId, setAddContactsModalCampId] = useState(null);
  const [selectedDbId, setSelectedDbId] = useState('');

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

  const loadCampDetail = async (cid) => {
    setCampDetailLoading(true);
    const res = await api(`/campaigns/${cid}`);
    if (res) {
      setCampDetailData(res);
    }
    setCampDetailLoading(false);
  };

  const toggleExpandCamp = (cid) => {
    if (expandedCampId === cid) {
      setExpandedCampId(null);
      setCampDetailData(null);
    } else {
      setExpandedCampId(cid);
      loadCampDetail(cid);
    }
  };

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
      if (expandedCampId) loadCampDetail(expandedCampId);
    } else {
      addToast('Ошибка сохранения: ' + (res?.error || '?'), 'err');
    }
  };

  const handleAction = async (id, action) => {
    const res = await api(`/campaigns/${id}/${action}`, { method: 'POST' });
    if (res && (res.ok || res.status === 'ok')) {
      addToast(
        action === 'start'
          ? 'Кампания запущена'
          : action === 'pause'
          ? 'Кампания на паузе'
          : action === 'retry-exhausted'
          ? `Перезапущено вызовов: ${res.requeued || 0}`
          : 'Кампания остановлена',
        'ok'
      );
      loadData();
      if (expandedCampId === id) loadCampDetail(id);
    } else {
      addToast('Ошибка: ' + (res?.error || '?'), 'err');
    }
  };

  const handleClearCamp = async (id) => {
    if (!window.confirm('Очистить контакты кампании? Все невыполненные вызовы будут удалены.')) return;
    const res = await api(`/campaigns/${id}/clear`, { method: 'POST' });
    if (res && res.ok) {
      addToast('Контакты кампании очищены', 'ok');
      loadData();
      if (expandedCampId === id) loadCampDetail(id);
    } else {
      addToast('Ошибка очистки: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDeleteCamp = async (id) => {
    if (!window.confirm('УДАЛИТЬ КАМПАНИЮ? Кампания и её элементы будут полностью удалены.')) return;
    const res = await api(`/campaigns/${id}/delete`, { method: 'POST' });
    if (res && res.ok) {
      addToast('Кампания удалена', 'ok');
      if (expandedCampId === id) setExpandedCampId(null);
      loadData();
    } else {
      addToast('Ошибка удаления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleAddContactsToCamp = async () => {
    if (!addContactsModalCampId || !selectedDbId) return addToast('Выберите базу контактов', 'warn');
    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { database_id: parseInt(selectedDbId) },
    });

    if (res && res.ok) {
      addToast(`Добавлено контактов: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      setSelectedDbId('');
      loadData();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления контактов: ' + (res?.error || '?'), 'err');
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
          campaigns.map((c) => {
            const isExpanded = expandedCampId === c.id;

            return (
              <div key={c.id} className="glass-panel rounded-2xl overflow-hidden transition-all">
                {/* Campaign Main Bar */}
                <div className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="flex items-center gap-3 cursor-pointer flex-1" onClick={() => toggleExpandCamp(c.id)}>
                    <button className="p-1 rounded-lg bg-line/50 text-muted hover:text-white">
                      {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-white text-base">#{c.id} {c.name}</span>
                        <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                          c.status === 'active' || c.status === 'running' ? 'bg-ok/20 text-ok' : c.status === 'paused' ? 'bg-warn/20 text-warn' : 'bg-line text-muted'
                        }`}>
                          {c.status === 'active' || c.status === 'running' ? 'Работает' : c.status === 'paused' ? 'Пауза' : 'Остановлена'}
                        </span>
                      </div>
                      <p className="text-xs text-muted mt-0.5">
                        Сценарий: <b className="text-white uppercase">{c.flow}</b> · Расписание: {c.work_time || '08:00-20:00'} · Контактов: {c.items_queued || 0} в очереди
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => toggleExpandCamp(c.id)}
                      className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white font-semibold text-xs flex items-center gap-1"
                    >
                      Карточка кампании
                    </button>

                    <button
                      onClick={() => handleOpenEdit(c)}
                      className="px-2.5 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white font-semibold text-xs"
                      title="Редактировать"
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>

                    {c.status !== 'active' && c.status !== 'running' && (
                      <button
                        onClick={() => handleAction(c.id, 'start')}
                        className="px-3 py-1.5 rounded-xl bg-ok/20 hover:bg-ok/30 text-ok font-semibold text-xs flex items-center gap-1 transition-all"
                      >
                        <Play className="w-3.5 h-3.5" /> Запустить
                      </button>
                    )}
                    {(c.status === 'active' || c.status === 'running') && (
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
                      onClick={() => handleDeleteCamp(c.id)}
                      className="p-1.5 rounded-lg text-muted hover:text-bad hover:bg-bad/10"
                      title="Удалить кампанию"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Expanded Campaign Details Card */}
                {isExpanded && (
                  <div className="p-5 border-t border-line bg-[#060c17]/60 flex flex-col gap-6 animate-in fade-in duration-200">
                    <div className="flex items-center justify-between flex-wrap gap-3">
                      <h3 className="text-sm font-bold text-white flex items-center gap-2">
                        Детализация и звонки кампании #{c.id}
                      </h3>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setAddContactsModalCampId(c.id)}
                          className="px-3 py-1.5 rounded-xl bg-acc/20 hover:bg-acc/30 text-acc2 font-semibold text-xs flex items-center gap-1"
                        >
                          <Plus className="w-3.5 h-3.5" /> Добавить контакты
                        </button>
                        <button
                          onClick={() => handleAction(c.id, 'retry-exhausted')}
                          className="px-3 py-1.5 rounded-xl bg-warn/20 hover:bg-warn/30 text-warn font-semibold text-xs flex items-center gap-1"
                        >
                          <RotateCcw className="w-3.5 h-3.5" /> Повторить исчерпанные
                        </button>
                        <button
                          onClick={() => handleClearCamp(c.id)}
                          className="px-3 py-1.5 rounded-xl bg-bad/20 hover:bg-bad/30 text-bad font-semibold text-xs flex items-center gap-1"
                        >
                          <Trash2 className="w-3.5 h-3.5" /> Очистить контакты
                        </button>
                      </div>
                    </div>

                    {detailLoading ? (
                      <div className="py-6 text-center text-muted animate-pulse">Загрузка карточки кампании...</div>
                    ) : (
                      <div className="flex flex-col gap-4">
                        {/* Calls Table for Campaign */}
                        <div className="overflow-x-auto border border-line rounded-xl bg-[#081221]">
                          <table className="w-full text-left text-xs">
                            <thead>
                              <tr className="border-b border-line text-muted uppercase text-[10px] bg-[#0d1a2e]">
                                <th className="py-2.5 px-3"># Call ID</th>
                                <th className="py-2.5 px-3">Время</th>
                                <th className="py-2.5 px-3">Клиент</th>
                                <th className="py-2.5 px-3">С номера</th>
                                <th className="py-2.5 px-3">Длительность</th>
                                <th className="py-2.5 px-3">Результат</th>
                                <th className="py-2.5 px-3 text-right">Хронология</th>
                              </tr>
                            </thead>
                            <tbody>
                              {!campDetailData?.calls || campDetailData.calls.length === 0 ? (
                                <tr>
                                  <td colSpan={7} className="py-6 text-center text-dim">
                                    Звонков по этой кампании пока нет.
                                  </td>
                                </tr>
                              ) : (
                                campDetailData.calls.map((call) => (
                                  <tr key={call.id} className="border-b border-line/40 hover:bg-white/5">
                                    <td className="py-2.5 px-3 font-mono text-dim">#{call.id}</td>
                                    <td className="py-2.5 px-3 text-muted">{call.started_at?.slice(0, 16).replace('T', ' ')}</td>
                                    <td className="py-2.5 px-3 font-semibold text-white">
                                      {call.contact_name || call.contact_phone}
                                      <span className="block text-[10px] text-dim font-normal">{call.contact_phone}</span>
                                    </td>
                                    <td className="py-2.5 px-3 text-muted">{call.caller_id || '—'}</td>
                                    <td className="py-2.5 px-3 text-muted">{call.duration_sec ? `${Math.round(call.duration_sec)} с` : '—'}</td>
                                    <td className="py-2.5 px-3">
                                      <span className="px-2 py-0.5 rounded-full bg-line text-muted text-[11px]">
                                        {call.result || call.status}
                                      </span>
                                    </td>
                                    <td className="py-2.5 px-3 text-right">
                                      <button
                                        onClick={() => setTimelineCallId(call.id)}
                                        className="px-2.5 py-1 rounded-lg bg-acc/20 hover:bg-acc/30 text-acc2 text-[11px] font-semibold flex items-center gap-1 ml-auto"
                                      >
                                        <Radio className="w-3 h-3" /> ВАТС Таймлайн
                                      </button>
                                    </td>
                                  </tr>
                                ))
                              )}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
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
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingCamp.flow || 'operator'}
                  onChange={(e) => setEditingCamp({ ...editingCamp, flow: e.target.value })}
                >
                  <option value="operator">Перевод на оператора (ACD)</option>
                  <option value="agent">ИИ-агент (Сценарий бота L1)</option>
                  <option value="message">Озвучка сообщения (Шаблон)</option>
                </select>
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Шаблон / сценарий бота</label>
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

      {/* Add Contacts to Campaign Modal */}
      <Modal
        isOpen={!!addContactsModalCampId}
        onClose={() => setAddContactsModalCampId(null)}
        title="Добавить базу контактов в кампанию"
      >
        <div className="flex flex-col gap-4 text-xs">
          <div>
            <label className="font-semibold text-muted block mb-1">Выберите базу контактов</label>
            <select
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc text-sm"
              value={selectedDbId}
              onChange={(e) => setSelectedDbId(e.target.value)}
            >
              <option value="">-- Выберите базу --</option>
              {databases.map((db) => (
                <option key={db.id} value={db.id}>
                  {db.name} ({db.contacts_count || 0} контактов)
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
            <button
              onClick={() => setAddContactsModalCampId(null)}
              className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold text-xs"
            >
              Отмена
            </button>
            <button
              onClick={handleAddContactsToCamp}
              className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
            >
              Добавить контакты
            </button>
          </div>
        </div>
      </Modal>

      {/* Timeline Modal */}
      <TimelineModal callId={timelineCallId} onClose={() => setTimelineCallId(null)} />
    </div>
  );
}
