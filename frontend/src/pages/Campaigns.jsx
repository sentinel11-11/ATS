import React, { useState, useEffect } from 'react';
import { Play, Pause, Square, Plus, Users, Settings2, Trash2, Edit2, ChevronDown, ChevronUp, History, ListOrdered, Calendar, RefreshCw } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';
import ScenarioDecisionTree from '../components/ScenarioDecisionTree';
import TimelineModal from '../components/TimelineModal';

export default function Campaigns({ addToast }) {
  const [campaigns, setCampaigns] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [databases, setDatabases] = useState([]);
  const [loading, setLoading] = useState(true);

  // Accordion Expanded State
  const [expandedCampId, setExpandedCampId] = useState(null);
  const [campDetailTab, setCampDetailTab] = useState('queue'); // 'queue' | 'calls'
  const [campDetailData, setCampDetailData] = useState(null);
  const [campDetailLoading, setCampDetailLoading] = useState(false);

  // VATS Timeline Modal State
  const [timelineCallId, setTimelineCallId] = useState(null);

  // Modals
  const [editingCamp, setEditingCamp] = useState(null); // Create or Edit Modal
  const [addContactsModalCampId, setAddContactsModalCampId] = useState(null); // Add Contacts Modal
  const [selectedContactIds, setSelectedContactIds] = useState([]);
  const [selectedDbId, setSelectedDbId] = useState('');
  const [contactSearchQuery, setContactSearchQuery] = useState('');

  const loadAll = async () => {
    setLoading(true);
    const [cRes, tRes, cntRes, dRes] = await Promise.all([
      api('/campaigns'),
      api('/templates'),
      api('/contacts'),
      api('/databases'),
    ]);

    if (cRes && cRes.campaigns) setCampaigns(cRes.campaigns);
    if (tRes && tRes.templates) setTemplates(tRes.templates);
    if (cntRes && cntRes.contacts) setContacts(cntRes.contacts);
    if (dRes && dRes.databases) setDatabases(dRes.databases);
    setLoading(false);
  };

  useEffect(() => {
    loadAll();
  }, []);

  const loadCampDetail = async (cid) => {
    setCampDetailLoading(true);
    const res = await api(`/campaigns/${cid}`);
    if (res && res.campaign) {
      setCampDetailData(res);
    } else {
      setCampDetailData(null);
    }
    setCampDetailLoading(false);
  };

  const handleToggleExpand = (cid) => {
    if (expandedCampId === cid) {
      setExpandedCampId(null);
      setCampDetailData(null);
    } else {
      setExpandedCampId(cid);
      setCampDetailTab('queue');
      loadCampDetail(cid);
    }
  };

  const handleOpenCreateModal = () => {
    setEditingCamp({
      id: 0,
      name: 'Новая кампания',
      template_id: templates[0]?.id || 1,
      flow: 'operator',
      work_time: '08:00-20:00',
      retry_delay_min: 15,
      retry_max: 2,
    });
  };

  const handleOpenEditModal = (c) => {
    let wt = '08:00-20:00';
    if (c.schedule && typeof c.schedule === 'object') {
      wt = `${c.schedule.start || '08:00'}-${c.schedule.end || '20:00'}`;
    }

    setEditingCamp({
      id: c.id,
      name: c.name || '',
      template_id: c.template_id || templates[0]?.id || 1,
      flow: c.flow || 'operator',
      work_time: wt,
      retry_delay_min: c.retry_delay_min || 15,
      retry_max: c.retry_max || 2,
      scenario: c.scenario || {},
    });
  };

  const handleSaveCampaign = async () => {
    if (!editingCamp || !editingCamp.name) return addToast('Укажите название кампании', 'warn');

    // Parse work_time into start and end
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
      template_id: parseInt(editingCamp.template_id) || 1,
      flow: editingCamp.flow || 'operator',
      retry_delay_min: parseInt(editingCamp.retry_delay_min) || 15,
      retry_max: parseInt(editingCamp.retry_max) || 2,
      schedule: {
        start,
        end,
        days: [0, 1, 2, 3, 4, 5, 6],
      },
      scenario: editingCamp.scenario || {},
    };

    const res = await api('/campaigns/save', { body: payload });
    if (res && res.ok) {
      addToast('Кампания сохранена', 'ok');
      setEditingCamp(null);
      loadAll();
      if (expandedCampId === payload.id) loadCampDetail(payload.id);
    } else {
      addToast('Ошибка сохранения кампании: ' + (res?.error || '?'), 'err');
    }
  };

  const handleControlCampaign = async (cid, action) => {
    const res = await api(`/campaigns/${cid}/${action}`, { method: 'POST', body: {} });
    if (res && res.ok) {
      addToast(`Кампания #${cid}: ${action}`, 'ok');
      loadAll();
      if (expandedCampId === cid) loadCampDetail(cid);
    } else {
      addToast('Ошибка управления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDeleteCampaign = async (cid) => {
    if (!window.confirm(`Удалить кампанию #${cid}?`)) return;
    const res = await api(`/campaigns/${cid}/delete`, { method: 'POST', body: {} });
    if (res && res.ok) {
      addToast(`Кампания #${cid} удалена`, 'ok');
      if (expandedCampId === cid) setExpandedCampId(null);
      loadAll();
    } else {
      addToast('Ошибка удаления', 'err');
    }
  };

  const handleClearContacts = async (cid) => {
    if (!window.confirm(`Очистить всю очередь контактов кампании #${cid}?`)) return;
    const res = await api(`/campaigns/${cid}/clear`, { method: 'POST', body: {} });
    if (res && res.ok) {
      addToast(`Контакты кампании #${cid} очищены`, 'ok');
      loadAll();
      if (expandedCampId === cid) loadCampDetail(cid);
    } else {
      addToast('Ошибка очистки', 'err');
    }
  };

  const handleRetryExhausted = async (cid) => {
    const res = await api(`/campaigns/${cid}/retry-exhausted`, { method: 'POST', body: {} });
    if (res && res.ok) {
      addToast(`Запущен повторный обзвон исчерпанных контактов: +${res.reset || 0}`, 'ok');
      loadAll();
      if (expandedCampId === cid) loadCampDetail(cid);
    } else {
      addToast('Ошибка сброса попыток', 'err');
    }
  };

  // Add Contacts Handlers
  const handleOpenAddContactsModal = (cid) => {
    setAddContactsModalCampId(cid);
    setSelectedContactIds([]);
    setSelectedDbId(databases[0]?.id || '');
    setContactSearchQuery('');
  };

  const handleAddSelectedContacts = async () => {
    if (!addContactsModalCampId || selectedContactIds.length === 0) return;

    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { contact_ids: selectedContactIds },
    });

    if (res && res.ok) {
      addToast(`Добавлено контактов в кампанию: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      loadAll();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleAddDbContacts = async () => {
    if (!addContactsModalCampId || !selectedDbId) return;

    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { database_id: parseInt(selectedDbId) },
    });

    if (res && res.ok) {
      addToast(`Добавлена база контактов: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      loadAll();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления базы', 'err');
    }
  };

  const handleAddAllConsentContacts = async () => {
    const validIds = (Array.isArray(contacts) ? contacts : []).filter((c) => c.consent).map((c) => c.id);
    if (validIds.length === 0) return addToast('Нет контактов с согласием', 'warn');

    const res = await api(`/campaigns/${addContactsModalCampId}/add-contacts`, {
      method: 'POST',
      body: { contact_ids: validIds },
    });

    if (res && res.ok) {
      addToast(`Добавлено контактов с согласием: +${res.added || 0}`, 'ok');
      setAddContactsModalCampId(null);
      loadAll();
      if (expandedCampId === addContactsModalCampId) loadCampDetail(addContactsModalCampId);
    } else {
      addToast('Ошибка добавления', 'err');
    }
  };

  const filteredContacts = (Array.isArray(contacts) ? contacts : []).filter((c) => {
    const q = contactSearchQuery.toLowerCase().trim();
    return !q || (c.name && c.name.toLowerCase().includes(q)) || (c.phone && c.phone.includes(q));
  });

  const campaignsList = Array.isArray(campaigns) ? campaigns : [];
  const templatesList = Array.isArray(templates) ? templates : [];
  const databasesList = Array.isArray(databases) ? databases : [];

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Кампании исходящего обзвона</h1>
        <button
          onClick={handleOpenCreateModal}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Plus className="w-4 h-4" /> Новая кампания
        </button>
      </div>

      {/* Campaigns Accordion List */}
      <div className="flex flex-col gap-4">
        {loading ? (
          <div className="p-8 text-center text-muted animate-pulse">Загрузка кампаний...</div>
        ) : campaignsList.length === 0 ? (
          <div className="glass-panel p-8 rounded-2xl text-center text-dim">
            Кампаний пока нет. Нажмите «Новая кампания» для настройки обзвона.
          </div>
        ) : (
          campaignsList.map((c) => {
            const isExpanded = expandedCampId === c.id;

            return (
              <div key={c.id} className="glass-panel rounded-2xl overflow-hidden transition-all">
                {/* Campaign Main Bar */}
                <div className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs font-bold text-dim">#{c.id}</span>
                    <div>
                      <h3 className="font-bold text-base text-white flex items-center gap-2">
                        {c.name}
                        <span
                          className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                            c.status === 'running'
                              ? 'bg-ok/20 text-ok border border-ok/30'
                              : c.status === 'paused'
                              ? 'bg-warn/20 text-warn border border-warn/30'
                              : 'bg-line text-muted'
                          }`}
                        >
                          {c.status === 'running' ? 'Работает' : c.status === 'paused' ? 'Пауза' : 'Остановлена'}
                        </span>
                      </h3>
                      <p className="text-xs text-muted mt-0.5">
                        Сценарий: <b className="text-white">{c.flow || 'operator'}</b> · Расписание:{' '}
                        <b className="text-white">
                          {c.schedule?.start || '08:00'}–{c.schedule?.end || '20:00'}
                        </b>{' '}
                        · В очереди: <b className="text-acc2">{c.queue_cnt || 0}</b>
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-wrap">
                    {/* Controls */}
                    {c.status === 'running' ? (
                      <button
                        onClick={() => handleControlCampaign(c.id, 'pause')}
                        className="px-3 py-1.5 rounded-xl bg-warn/20 hover:bg-warn/30 text-warn font-semibold text-xs flex items-center gap-1 transition-colors"
                      >
                        <Pause className="w-3.5 h-3.5" /> Пауза
                      </button>
                    ) : (
                      <button
                        onClick={() => handleControlCampaign(c.id, 'start')}
                        className="px-3 py-1.5 rounded-xl bg-ok/20 hover:bg-ok/30 text-ok font-semibold text-xs flex items-center gap-1 transition-colors"
                      >
                        <Play className="w-3.5 h-3.5" /> Запустить
                      </button>
                    )}

                    <button
                      onClick={() => handleControlCampaign(c.id, 'stop')}
                      className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-muted hover:text-white font-semibold text-xs flex items-center gap-1 transition-colors"
                    >
                      <Square className="w-3.5 h-3.5" /> Стоп
                    </button>

                    <button
                      onClick={() => handleOpenEditModal(c)}
                      className="p-2 rounded-xl bg-line/60 hover:bg-line text-muted hover:text-white transition-colors"
                      title="Настройки кампании"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>

                    <button
                      onClick={() => handleDeleteCampaign(c.id)}
                      className="p-2 rounded-xl bg-bad/10 hover:bg-bad/20 text-bad transition-colors"
                      title="Удалить кампанию"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>

                    <button
                      onClick={() => handleToggleExpand(c.id)}
                      className="px-3 py-1.5 rounded-xl bg-acc/10 hover:bg-acc/20 text-acc2 font-semibold text-xs flex items-center gap-1 transition-colors"
                    >
                      Карточка кампании {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {/* Expanded Details Card */}
                {isExpanded && (
                  <div className="border-t border-line bg-[#070e1a]/60 p-5 flex flex-col gap-4 animate-in fade-in duration-150">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setCampDetailTab('queue')}
                          className={`px-3.5 py-1.5 rounded-xl font-semibold text-xs transition-all flex items-center gap-1.5 ${
                            campDetailTab === 'queue'
                              ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow-md'
                              : 'bg-[#0e1c30] text-muted hover:text-white border border-line'
                          }`}
                        >
                          <ListOrdered className="w-4 h-4" /> 📋 Очередь контактов ({campDetailData?.items?.length || 0})
                        </button>
                        <button
                          onClick={() => setCampDetailTab('calls')}
                          className={`px-3.5 py-1.5 rounded-xl font-semibold text-xs transition-all flex items-center gap-1.5 ${
                            campDetailTab === 'calls'
                              ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow-md'
                              : 'bg-[#0e1c30] text-muted hover:text-white border border-line'
                          }`}
                        >
                          <History className="w-4 h-4" /> 📞 Совершенные звонки ({campDetailData?.calls?.length || 0})
                        </button>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleOpenAddContactsModal(c.id)}
                          className="px-3 py-1.5 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white font-semibold text-xs flex items-center gap-1 hover:brightness-110"
                        >
                          <Plus className="w-3.5 h-3.5" /> Добавить контакты
                        </button>
                        <button
                          onClick={() => handleRetryExhausted(c.id)}
                          className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white font-semibold text-xs flex items-center gap-1"
                        >
                          <RefreshCw className="w-3.5 h-3.5 text-acc2" /> Повторить исчерпанные
                        </button>
                        <button
                          onClick={() => handleClearContacts(c.id)}
                          className="px-3 py-1.5 rounded-xl bg-bad/20 hover:bg-bad/30 text-bad font-semibold text-xs flex items-center gap-1"
                        >
                          <Trash2 className="w-3.5 h-3.5" /> Очистить контакты
                        </button>
                      </div>
                    </div>

                    {/* Tab Content */}
                    {campDetailLoading ? (
                      <div className="py-8 text-center text-muted animate-pulse">Загрузка данных кампании...</div>
                    ) : campDetailTab === 'queue' ? (
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
                              (Array.isArray(campDetailData.items) ? campDetailData.items : []).map((it) => (
                                <tr key={it.id} className="border-b border-line/40 hover:bg-white/5">
                                  <td className="py-2.5 px-3 font-mono text-dim">#{it.id}</td>
                                  <td className="py-2.5 px-3 font-mono text-white font-semibold">{it.phone}</td>
                                  <td className="py-2.5 px-3">
                                    <span
                                      className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                                        it.status === 'queued'
                                          ? 'bg-acc/20 text-acc2'
                                          : it.status === 'exhausted'
                                          ? 'bg-bad/20 text-bad'
                                          : 'bg-line text-muted'
                                      }`}
                                    >
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
                              (Array.isArray(campDetailData.calls) ? campDetailData.calls : []).map((call) => (
                                <tr key={call.id} className="border-b border-line/40 hover:bg-white/5">
                                  <td className="py-2.5 px-3 font-mono text-dim">#{call.id}</td>
                                  <td className="py-2.5 px-3 text-muted">{call.started_at?.slice(0, 16).replace('T', ' ')}</td>
                                  <td className="py-2.5 px-3 font-semibold text-white">
                                    {call.contact_name || call.contact_phone}
                                  </td>
                                  <td className="py-2.5 px-3 font-mono text-muted">{call.caller_id || '—'}</td>
                                  <td className="py-2.5 px-3 text-muted">
                                    {call.duration_sec ? `${Math.round(call.duration_sec)} с` : '—'}
                                  </td>
                                  <td className="py-2.5 px-3">
                                    <span className="badge">{call.result || call.status}</span>
                                  </td>
                                  <td className="py-2.5 px-3 text-right">
                                    <button
                                      onClick={() => setTimelineCallId(call.id)}
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
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Edit / Create Campaign Modal */}
      <Modal
        isOpen={!!editingCamp}
        onClose={() => setEditingCamp(null)}
        title={editingCamp?.id ? `Редактирование кампании #${editingCamp.id}` : 'Создание новой кампании'}
        wide={true}
      >
        {editingCamp && (
          <div className="flex flex-col gap-4 text-xs">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Название кампании</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingCamp.name}
                  onChange={(e) => setEditingCamp({ ...editingCamp, name: e.target.value })}
                  placeholder="Обзвон действующих клиентов"
                />
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Шаблон / Голосовое сообщение</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.template_id || 1}
                  onChange={(e) => setEditingCamp({ ...editingCamp, template_id: parseInt(e.target.value) || 1 })}
                >
                  {templatesList.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Тип сценария</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingCamp.flow || 'operator'}
                  onChange={(e) => setEditingCamp({ ...editingCamp, flow: e.target.value })}
                >
                  <option value="operator">Перевод на операторов (ACD)</option>
                  <option value="scenario">ИИ-Бот / Квалификационный опрос</option>
                  <option value="inform">Информатор (Прослушать сообщение)</option>
                </select>
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Рабочее время (Старт-Финиш)</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.work_time || '08:00-20:00'}
                  onChange={(e) => setEditingCamp({ ...editingCamp, work_time: e.target.value })}
                  placeholder="08:00-20:00"
                />
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Макс. попыток на контакт</label>
                <input
                  type="number"
                  min="1"
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingCamp.retry_max || 2}
                  onChange={(e) => setEditingCamp({ ...editingCamp, retry_max: parseInt(e.target.value) || 1 })}
                />
              </div>
            </div>

            {/* Visual Scenario Editor when Flow is scenario */}
            {editingCamp.flow === 'scenario' && (
              <div className="flex flex-col gap-2">
                <span className="font-bold text-white text-xs">Визуальный конструктор ИИ-Сценария:</span>
                <ScenarioDecisionTree
                  scenario={editingCamp.scenario}
                  onChange={(scObj) => setEditingCamp({ ...editingCamp, scenario: scObj })}
                />
              </div>
            )}

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
                onClick={handleSaveCampaign}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
              >
                Сохранить кампанию
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Add Contacts Modal */}
      <Modal
        isOpen={!!addContactsModalCampId}
        onClose={() => setAddContactsModalCampId(null)}
        title={`Добавление контактов в кампанию #${addContactsModalCampId}`}
        wide={true}
      >
        <div className="flex flex-col gap-4 text-xs">
          <div className="p-4 rounded-xl bg-[#081221] border border-line flex flex-col md:flex-row items-center justify-between gap-3">
            <div>
              <span className="font-bold text-white block">Импорт целой базы контактов</span>
              <span className="text-dim">Выберите предварительно загруженную базу или CSV/XLSX</span>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={selectedDbId}
                onChange={(e) => setSelectedDbId(e.target.value)}
              >
                <option value="">-- Выберите зарегистрированную базу --</option>
                {databasesList.map((db) => (
                  <option key={db.id} value={db.id}>
                    {db.name} ({db.contacts_count || 0} контактов)
                  </option>
                ))}
              </select>
              <button
                onClick={handleAddDbContacts}
                disabled={!selectedDbId}
                className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow disabled:opacity-50"
              >
                Загрузить базу
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between pt-2">
            <span className="font-bold text-white">Выбор отдельных контактов поштучно:</span>
            <button
              onClick={handleAddAllConsentContacts}
              className="px-3 py-1 rounded-lg bg-ok/20 hover:bg-ok/30 text-ok font-semibold text-xs"
            >
              Добавить ВСЕ с согласием (152-ФЗ)
            </button>
          </div>

          <input
            type="text"
            className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
            placeholder="Поиск по имени или номеру..."
            value={contactSearchQuery}
            onChange={(e) => setContactSearchQuery(e.target.value)}
          />

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
                      isChecked ? 'bg-acc/20 border border-acc/40' : 'hover:bg-white/5'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => {
                          setSelectedContactIds((prev) =>
                            prev.includes(cnt.id) ? prev.filter((x) => x !== cnt.id) : [...prev, cnt.id]
                          );
                        }}
                        className="accent-acc w-4 h-4"
                      />
                      <div>
                        <span className="font-semibold text-white block">{cnt.name || 'Без имени'}</span>
                        <span className="font-mono text-dim text-[11px]">{cnt.phone}</span>
                      </div>
                    </div>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        cnt.consent ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad'
                      }`}
                    >
                      {cnt.consent ? 'Есть согласие' : 'Нет согласия'}
                    </span>
                  </label>
                );
              })
            )}
          </div>

          <div className="flex items-center justify-between pt-3 border-t border-line">
            <span className="text-muted font-semibold">Выбрано контактов: {selectedContactIds.length}</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setAddContactsModalCampId(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleAddSelectedContacts}
                disabled={selectedContactIds.length === 0}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow disabled:opacity-50"
              >
                Добавить выбранные ({selectedContactIds.length})
              </button>
            </div>
          </div>
        </div>
      </Modal>

      {/* Timeline Modal */}
      {timelineCallId && (
        <TimelineModal callId={timelineCallId} onClose={() => setTimelineCallId(null)} />
      )}
    </div>
  );
}
