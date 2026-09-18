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
  Search,
  CheckCircle2,
  AlertCircle,
  Radio,
  FileSpreadsheet,
  Database,
  Users,
  Clock,
  PhoneCall
} from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';
import TimelineModal from '../components/TimelineModal';

export default function Campaigns({ addToast }) {
  const [campaigns, setCampaigns] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [databases, setDatabases] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  
  const [editingCamp, setEditingCamp] = useState(null); // null = modal closed
  const [expandedCampId, setExpandedCampId] = useState(null); // campaign ID expanded
  const [campDetailData, setCampDetailData] = useState(null);
  const [detailLoading, setCampDetailLoading] = useState(false);
  const [detailTab, setDetailTab] = useState('items'); // 'items' | 'calls'
  const [timelineCallId, setTimelineCallId] = useState(null);

  // Add Contacts Picker Modal State
  const [addContactsModalCampId, setAddContactsModalCampId] = useState(null);
  const [pickerSearch, setPickerSearch] = useState('');
  const [consentOnly, setConsentOnly] = useState(true);
  const [selectedContactIds, setSelectedContactIds] = useState([]);
  const [selectedDbId, setSelectedDbId] = useState('');

  const loadData = async () => {
    setLoading(true);
    const [cRes, tRes, dRes, cntRes] = await Promise.all([
      api('/campaigns'),
      api('/templates'),
      api('/databases'),
      api('/contacts')
    ]);

    if (cRes && cRes.campaigns) setCampaigns(cRes.campaigns);
    if (tRes && tRes.templates) setTemplates(tRes.templates);
    if (dRes && dRes.databases) setDatabases(dRes.databases);
    if (cntRes && cntRes.contacts) setContacts(cntRes.contacts);
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
    let workTime = '08:00-20:00';
    if (camp?.schedule && typeof camp.schedule === 'object') {
      workTime = `${camp.schedule.start || '08:00'}-${camp.schedule.end || '20:00'}`;
    }

    const c = camp ? { ...camp, work_time: workTime } : {
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

    let start = '08:00';
    let end = '20:00';
    if (editingCamp.work_time && editingCamp.work_time.includes('-')) {
      const parts = editingCamp.work_time.split('-');
      start = parts[0].trim() || '08:00';
      end = parts[1].trim() || '20:00';
    }

    const payload = {
      id: editingCamp.id || 0,
      name: editingCamp.name,
      flow: editingCamp.flow || 'operator',
      template_id: parseInt(editingCamp.template_id || 1),
      schedule: { start, end, days: [0, 1, 2, 3, 4, 5, 6] },
      retry_delay_min: parseInt(editingCamp.retry_delay_min || 15),
      retry_max: parseInt(editingCamp.retry_max || 2),
      max_channels: parseInt(editingCamp.max_channels || 0),
      connect_on_qualify: editingCamp.connect_on_qualify ?? true,
    };

    const res = await api('/campaigns/save', {
      body: payload,
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

  // Add Contacts Handlers
  const handleOpenAddContacts = (cid) => {
    setAddContactsModalCampId(cid);
    setSelectedContactIds([]);
    setPickerSearch('');
    setConsentOnly(true);
    setSelectedDbId(databases[0]?.id || '');
  };

  const toggleSelectContact = (id) => {
    setSelectedContactIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const handleAddSelectedContacts = async () => {
    if (!addContactsModalCampId || selectedContactIds.length === 0) {
      return addToast('Выберите хотя бы один контакт', 'warn');
    }

    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { contact_ids: selectedContactIds },
    });

    if (res && res.ok) {
      addToast(`Добавлено контактов: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      loadData();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleAddAllConsentContacts = async () => {
    const validIds = contacts.filter((c) => c.consent).map((c) => c.id);
    if (validIds.length === 0) return addToast('Нет контактов с согласием', 'warn');

    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { contact_ids: validIds },
    });

    if (res && res.ok) {
      addToast(`Добавлено контактов с согласием: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      loadData();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleAddDatabaseToCamp = async () => {
    if (!addContactsModalCampId || !selectedDbId) return addToast('Выберите базу контактов', 'warn');
    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { database_id: parseInt(selectedDbId) },
    });

    if (res && res.ok) {
      addToast(`Добавлено контактов из базы: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      loadData();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления базы: ' + (res?.error || '?'), 'err');
    }
  };

  // Filter contacts for picker
  const filteredContacts = contacts.filter((c) => {
    if (consentOnly && !c.consent) return false;
    if (pickerSearch) {
      const q = pickerSearch.toLowerCase();
      const matchName = (c.name || '').toLowerCase().includes(q);
      const matchPhone = (c.phone || '').includes(q);
      return matchName || matchPhone;
    }
    return true;
  });

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
                        Сценарий: <b className="text-white uppercase">{c.flow}</b> · Расписание: {c.schedule?.start || '08:00'}–{c.schedule?.end || '20:00'} · В очереди: {c.items_queued || 0}
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
                          onClick={() => handleOpenAddContacts(c.id)}
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
                        {/* Detail Tab Switcher: Items Queue vs Calls Journal */}
                        <div className="flex items-center gap-2 border-b border-line pb-2">
                          <button
                            type="button"
                            onClick={() => setDetailTab('items')}
                            className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition-all ${
                              detailTab === 'items'
                                ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow'
                                : 'bg-[#081221] text-muted hover:text-white'
                            }`}
                          >
                            📋 Очередь контактов ({campDetailData?.items?.length || 0})
                          </button>
                          <button
                            type="button"
                            onClick={() => setDetailTab('calls')}
                            className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition-all ${
                              detailTab === 'calls'
                                ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow'
                                : 'bg-[#081221] text-muted hover:text-white'
                            }`}
                          >
                            📞 Совершенные звонки ({campDetailData?.calls?.length || 0})
                          </button>
                        </div>

                        {detailTab === 'items' ? (
                          <div className="overflow-x-auto border border-line rounded-xl bg-[#081221]">
                            <table className="w-full text-left text-xs">
                              <thead>
                                <tr className="border-b border-line text-muted uppercase text-[10px] bg-[#0d1a2e]">
                                  <th className="py-2.5 px-3"># ID</th>
                                  <th className="py-2.5 px-3">Телефон</th>
                                  <th className="py-2.5 px-3">Статус</th>
                                  <th className="py-2.5 px-3">Попыток</th>
                                  <th className="py-2.5 px-3">След. попытка</th>
                                </tr>
                              </thead>
                              <tbody>
                                {!campDetailData?.items || campDetailData.items.length === 0 ? (
                                  <tr>
                                    <td colSpan={5} className="py-6 text-center text-dim">
                                      В очереди контактов пока нет.
                                    </td>
                                  </tr>
                                ) : (
                                  campDetailData.items.map((it) => (
                                    <tr key={it.id} className="border-b border-line/40 hover:bg-white/5">
                                      <td className="py-2.5 px-3 font-mono text-dim">#{it.id}</td>
                                      <td className="py-2.5 px-3 font-mono text-white font-semibold">{it.phone}</td>
                                      <td className="py-2.5 px-3">
                                        <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                                          it.status === 'queued' ? 'bg-acc/20 text-acc2' : it.status === 'exhausted' ? 'bg-bad/20 text-bad' : 'bg-line text-muted'
                                        }`}>
                                          {it.status}
                                        </span>
                                      </td>
                                      <td className="py-2.5 px-3 text-muted">{it.attempts || 0}</td>
                                      <td className="py-2.5 px-3 text-dim">{it.next_attempt_at?.slice(0, 16) || '—'}</td>
                                    </tr>
                                  ))
                                )}
                              </tbody>
                            </table>
                          </div>
                        ) : (
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
                        )}
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
                  value={editingCamp.template_id || 1}
                  onChange={(e) => setEditingCamp({ ...editingCamp, template_id: parseInt(e.target.value) || 1 })}
                >
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
                <label className="font-semibold text-muted block mb-1">Рабочее время (08:00-20:00)</label>
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
                type="button"
                onClick={() => setEditingCamp(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold text-xs"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleSave}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
              >
                Сохранить кампанию
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Advanced Add Contacts Picker Modal */}
      <Modal
        isOpen={!!addContactsModalCampId}
        onClose={() => setAddContactsModalCampId(null)}
        title={`Добавить контакты в кампанию #${addContactsModalCampId}`}
        wide={true}
      >
        <div className="flex flex-col gap-4 text-xs">
          {/* Section 1: Individual Contacts Picklist */}
          <div className="flex flex-col gap-3 p-4 rounded-2xl bg-[#081221] border border-line">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="font-bold text-white text-sm flex items-center gap-1.5">
                <Users className="w-4 h-4 text-acc2" /> Выбор отдельных контактов галочками
              </span>
              <label className="flex items-center gap-2 cursor-pointer text-muted font-medium">
                <input
                  type="checkbox"
                  checked={consentOnly}
                  onChange={(e) => setConsentOnly(e.target.checked)}
                  className="accent-acc"
                />
                <span>Только с согласием (152-ФЗ)</span>
              </label>
            </div>

            {/* Filter Search */}
            <div className="relative">
              <Search className="w-4 h-4 text-dim absolute left-3 top-2.5" />
              <input
                type="text"
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
                placeholder="Фильтр по имени или телефону..."
                className="w-full bg-[#0a1628] border border-line2 rounded-xl pl-9 pr-3 py-2 text-xs text-white outline-none focus:border-acc"
              />
            </div>

            {/* Contact List */}
            <div className="max-h-56 overflow-y-auto border border-line rounded-xl p-2 bg-[#060d18] flex flex-col gap-1">
              {filteredContacts.length === 0 ? (
                <span className="text-dim p-4 text-center">Контакты не найдены</span>
              ) : (
                filteredContacts.map((cnt) => {
                  const isChecked = selectedContactIds.includes(cnt.id);

                  return (
                    <label
                      key={cnt.id}
                      className={`flex items-center justify-between p-2 rounded-lg cursor-pointer transition-colors ${
                        isChecked ? 'bg-acc/15 border border-acc/30' : 'hover:bg-white/5'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggleSelectContact(cnt.id)}
                          className="accent-acc w-4 h-4"
                        />
                        <span className="font-semibold text-white">{cnt.name || 'Без имени'}</span>
                        <span className="font-mono text-muted">{cnt.phone}</span>
                      </div>
                      {!cnt.consent && (
                        <span className="px-2 py-0.5 rounded bg-bad/20 text-bad text-[10px] font-semibold">
                          Нет согласия
                        </span>
                      )}
                    </label>
                  );
                })
              )}
            </div>

            <div className="flex items-center justify-between pt-1">
              <span className="text-dim">
                Найдено: {filteredContacts.length} · Выбрано: {selectedContactIds.length}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleAddSelectedContacts}
                  disabled={selectedContactIds.length === 0}
                  className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg disabled:opacity-50"
                >
                  Добавить выбранные ({selectedContactIds.length})
                </button>
                <button
                  type="button"
                  onClick={handleAddAllConsentContacts}
                  className="px-4 py-2 rounded-xl bg-line/60 hover:bg-line text-white font-semibold"
                >
                  Все с согласием
                </button>
              </div>
            </div>
          </div>

          {/* Section 2: Add Entire Database Base */}
          <div className="flex flex-col gap-3 p-4 rounded-2xl bg-[#081221] border border-line">
            <span className="font-bold text-white text-sm flex items-center gap-1.5">
              <Database className="w-4 h-4 text-cy" /> Добавить базу целей целиком
            </span>
            <div className="flex items-center gap-3">
              <select
                className="flex-1 bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc text-xs"
                value={selectedDbId}
                onChange={(e) => setSelectedDbId(e.target.value)}
              >
                <option value="">-- Выберите зарегистрированную базу --</option>
                {databases.map((db) => (
                  <option key={db.id} value={db.id}>
                    {db.name} ({db.contacts_count || 0} контактов)
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={handleAddDatabaseToCamp}
                disabled={!selectedDbId}
                className="px-4 py-2 rounded-xl bg-line/60 hover:bg-line text-white font-semibold shrink-0 disabled:opacity-50"
              >
                Добавить базу целиком
              </button>
            </div>
          </div>
        </div>
      </Modal>

      {/* Timeline Modal */}
      <TimelineModal callId={timelineCallId} onClose={() => setTimelineCallId(null)} />
    </div>
  );
}
