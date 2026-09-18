import React, { useState, useEffect } from 'react';
import { Upload, Plus, Trash2, Users, FileSpreadsheet, ShieldAlert, Search, Filter, AlertTriangle, CheckCircle, Info } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function Contacts({ addToast }) {
  const [contacts, setContacts] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);

  // Search & Filter
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedGroupFilter, setSelectedGroupFilter] = useState('');

  // Selection
  const [selectedContactIds, setSelectedContactIds] = useState([]);
  const [selectedTargetCampId, setSelectedTargetCampId] = useState('');

  // Modals
  const [showImportModal, setShowImportModal] = useState(false);
  const [showNewModal, setShowNewModal] = useState(false);
  const [cardContactData, setCardContactData] = useState(null); // Contact Details Modal
  const [cardLoading, setCardLoading] = useState(false);

  // Import Report Modal
  const [importReport, setImportReport] = useState(null);

  const [selectedFile, setSelectedFile] = useState(null);
  const [importLoading, setImportLoading] = useState(false);

  const [newContact, setNewContact] = useState({
    phone: '',
    name: '',
    group_name: '',
    consent: true,
  });

  const loadContacts = async () => {
    setLoading(true);
    const [cRes, campRes] = await Promise.all([
      api('/contacts'),
      api('/campaigns')
    ]);

    if (cRes && cRes.contacts) setContacts(cRes.contacts);
    if (campRes && campRes.campaigns) setCampaigns(campRes.campaigns);
    setLoading(false);
  };

  useEffect(() => {
    loadContacts();
  }, []);

  const loadContactCard = async (cid) => {
    setCardLoading(true);
    const res = await api(`/contacts/history?id=${cid}`);
    if (res && res.contact) {
      setCardContactData(res);
    }
    setCardLoading(false);
  };

  // Filter contacts
  const availableGroups = Array.from(
    new Set(contacts.map((c) => c.group_name || c.grp).filter(Boolean))
  );

  const filteredContacts = contacts.filter((c) => {
    const q = searchQuery.toLowerCase().trim();
    const matchesSearch =
      !q ||
      (c.name && c.name.toLowerCase().includes(q)) ||
      (c.phone && c.phone.includes(q)) ||
      (c.group_name && c.group_name.toLowerCase().includes(q));

    const matchesGroup =
      !selectedGroupFilter || (c.group_name || c.grp) === selectedGroupFilter;

    return matchesSearch && matchesGroup;
  });

  const handleSelectAll = (e) => {
    if (e.target.checked) {
      setSelectedContactIds(filteredContacts.map((c) => c.id));
    } else {
      setSelectedContactIds([]);
    }
  };

  const toggleSelectContact = (id) => {
    setSelectedContactIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const handleAddSelectedToCampaign = async () => {
    if (!selectedTargetCampId || selectedContactIds.length === 0) {
      return addToast('Выберите контакты и целевую кампанию', 'warn');
    }

    const res = await api(`/campaigns/${selectedTargetCampId}/add-contacts`, {
      method: 'POST',
      body: { contact_ids: selectedContactIds },
    });

    if (res && res.ok) {
      addToast(`Добавлено в кампанию: +${res.added || 0} (дубли в очереди пропущены)`, 'ok');
      setSelectedContactIds([]);
      setSelectedTargetCampId('');
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDeleteSelected = async () => {
    if (selectedContactIds.length === 0) return;
    if (!window.confirm(`Удалить выбранные контакты (${selectedContactIds.length})?`)) return;

    const res = await api('/contacts/delete', {
      body: { ids: selectedContactIds },
    });

    if (res && res.ok) {
      addToast('Выбранные контакты удалены', 'ok');
      setSelectedContactIds([]);
      loadContacts();
    } else {
      addToast('Ошибка удаления', 'err');
    }
  };

  const handleSaveContact = async () => {
    if (!newContact.phone) return addToast('Укажите телефон', 'warn');
    const res = await api('/contacts/save', { body: newContact });
    if (res && res.ok) {
      if (res.updated_existing) {
        addToast('Контакт с этим номером уже был в БД — данные обновлены (защита от дублей)', 'ok');
      } else {
        addToast('Новый контакт успешно создан', 'ok');
      }
      setShowNewModal(false);
      setNewContact({ phone: '', name: '', group_name: '', consent: true });
      loadContacts();
    } else {
      addToast('Ошибка сохранения контакта: ' + (res?.error || '?'), 'err');
    }
  };

  const handleImportFile = async (e) => {
    e.preventDefault();
    if (!selectedFile) return addToast('Выберите файл CSV или Excel', 'warn');
    setImportLoading(true);

    try {
      const b64 = await new Promise((res, rej) => {
        const rd = new FileReader();
        rd.onload = () => res(rd.result.split(',')[1]);
        rd.onerror = rej;
        rd.readAsDataURL(selectedFile);
      });

      const res = await api('/contacts/import-file', {
        body: { filename: selectedFile.name, content_b64: b64 },
      });

      setImportLoading(false);
      if (res && res.ok) {
        setShowImportModal(false);
        setSelectedFile(null);
        setImportReport(res); // Show detailed import report modal with duplicate breakdown
        loadContacts();
      } else {
        addToast('Ошибка импорта: ' + (res?.error || '?'), 'err');
      }
    } catch (err) {
      setImportLoading(false);
      addToast('Ошибка при чтении файла', 'err');
    }
  };

  const handleComplaint = async (phone) => {
    const res = await api('/complaint', { body: { phone, reason: 'Жалоба / Отказ клиента' } });
    if (res && res.ok) {
      addToast('Жалоба зарегистрирована, номер внесен в ЧС', 'ok');
      if (cardContactData) loadContactCard(cardContactData.contact.id);
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">База контактов и клиенты</h1>
          <p className="text-xs text-muted">Всего в базе: {contacts.length} контактов</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowNewModal(true)}
            className="px-4 py-2 rounded-xl bg-line/60 hover:bg-line font-semibold text-xs text-white flex items-center gap-1.5 transition-all"
          >
            <Plus className="w-4 h-4" /> Добавить контакт
          </button>
          <button
            onClick={() => setShowImportModal(true)}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
          >
            <Upload className="w-4 h-4" /> Импорт из CSV / Excel
          </button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="relative md:col-span-2">
          <Search className="w-4 h-4 text-dim absolute left-3.5 top-3" />
          <input
            type="text"
            className="w-full bg-[#0a1628] border border-line2 rounded-xl pl-10 pr-4 py-2.5 text-xs text-white outline-none focus:border-acc"
            placeholder="Поиск по имени, номеру телефона или группе..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="relative">
          <Filter className="w-4 h-4 text-dim absolute left-3.5 top-3" />
          <select
            className="w-full bg-[#0a1628] border border-line2 rounded-xl pl-10 pr-4 py-2.5 text-xs text-white outline-none focus:border-acc font-semibold"
            value={selectedGroupFilter}
            onChange={(e) => setSelectedGroupFilter(e.target.value)}
          >
            <option value="">Все группы и базы ({availableGroups.length})</option>
            {availableGroups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Bulk Action Toolbar */}
      {selectedContactIds.length > 0 && (
        <div className="p-3 rounded-2xl bg-acc/20 border border-acc/40 flex items-center justify-between flex-wrap gap-3 animate-in fade-in duration-150">
          <span className="font-semibold text-xs text-white">
            Выбрано контактов: {selectedContactIds.length}
          </span>
          <div className="flex items-center gap-2">
            <select
              className="bg-[#0a1628] border border-line rounded-xl px-3 py-1.5 text-xs text-white outline-none"
              value={selectedTargetCampId}
              onChange={(e) => setSelectedTargetCampId(e.target.value)}
            >
              <option value="">-- Добавить в кампанию --</option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  #{c.id} {c.name}
                </option>
              ))}
            </select>
            <button
              onClick={handleAddSelectedToCampaign}
              disabled={!selectedTargetCampId}
              className="px-3 py-1.5 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-xs text-white shadow disabled:opacity-50"
            >
              Добавить
            </button>
            <button
              onClick={handleDeleteSelected}
              className="px-3 py-1.5 rounded-xl bg-bad/20 hover:bg-bad/30 text-bad font-semibold text-xs flex items-center gap-1"
            >
              <Trash2 className="w-3.5 h-3.5" /> Удалить
            </button>
          </div>
        </div>
      )}

      {/* Contacts Table */}
      <div className="glass-panel p-6 rounded-2xl overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-line text-muted uppercase text-[10px]">
              <th className="py-2.5 px-3 w-8">
                <input
                  type="checkbox"
                  onChange={handleSelectAll}
                  checked={filteredContacts.length > 0 && selectedContactIds.length === filteredContacts.length}
                  className="accent-acc w-4 h-4 cursor-pointer"
                />
              </th>
              <th className="py-2.5 px-3">Имя</th>
              <th className="py-2.5 px-3">Телефон</th>
              <th className="py-2.5 px-3">Группа</th>
              <th className="py-2.5 px-3">Согласие (152-ФЗ)</th>
              <th className="py-2.5 px-3 text-right">Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="py-8 text-center text-muted">
                  Загрузка контактов...
                </td>
              </tr>
            ) : filteredContacts.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-8 text-center text-dim">
                  Контакты не найдены.
                </td>
              </tr>
            ) : (
              filteredContacts.slice(0, 300).map((c) => {
                const isChecked = selectedContactIds.includes(c.id);

                return (
                  <tr key={c.id} className={`border-b border-line/50 hover:bg-white/5 transition-colors ${isChecked ? 'bg-acc/10' : ''}`}>
                    <td className="py-3 px-3">
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleSelectContact(c.id)}
                        className="accent-acc w-4 h-4 cursor-pointer"
                      />
                    </td>
                    <td className="py-3 px-3 font-semibold text-white">{c.name || 'Без имени'}</td>
                    <td className="py-3 px-3 font-mono text-muted">{c.phone}</td>
                    <td className="py-3 px-3 text-muted">{c.group_name || c.grp || '—'}</td>
                    <td className="py-3 px-3">
                      <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                        c.consent ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad'
                      }`}>
                        {c.consent ? 'Есть' : 'Нет'}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-right">
                      <button
                        onClick={() => loadContactCard(c.id)}
                        className="px-2.5 py-1 rounded-lg bg-line/60 hover:bg-line text-white text-xs font-semibold"
                      >
                        Карточка
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* New Contact Modal */}
      <Modal isOpen={showNewModal} onClose={() => setShowNewModal(false)} title="Новый контакт">
        <div className="flex flex-col gap-4 text-xs">
          <div>
            <label className="font-semibold text-muted block mb-1">Телефон (E.164)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm font-mono text-white outline-none focus:border-acc"
              value={newContact.phone}
              onChange={(e) => setNewContact({ ...newContact, phone: e.target.value })}
              placeholder="79260000000"
            />
          </div>
          <div>
            <label className="font-semibold text-muted block mb-1">Имя клиента</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={newContact.name}
              onChange={(e) => setNewContact({ ...newContact, name: e.target.value })}
              placeholder="Иван Петров"
            />
          </div>
          <div>
            <label className="font-semibold text-muted block mb-1">Группа / Категория</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={newContact.group_name}
              onChange={(e) => setNewContact({ ...newContact, group_name: e.target.value })}
              placeholder="Клиенты VIP"
            />
          </div>
          <div className="flex items-center gap-2 pt-1">
            <input
              type="checkbox"
              id="cConsent"
              checked={newContact.consent}
              onChange={(e) => setNewContact({ ...newContact, consent: e.target.checked })}
              className="accent-acc"
            />
            <label htmlFor="cConsent" className="text-white font-medium cursor-pointer">
              Есть согласие на обзвон (152-ФЗ)
            </label>
          </div>
          <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
            <button onClick={() => setShowNewModal(false)} className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white">
              Отмена
            </button>
            <button onClick={handleSaveContact} className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg">
              Сохранить
            </button>
          </div>
        </div>
      </Modal>

      {/* Import File Modal */}
      <Modal isOpen={showImportModal} onClose={() => setShowImportModal(false)} title="Импорт контактов из файла">
        <form onSubmit={handleImportFile} className="flex flex-col gap-4 text-xs">
          <div className="p-6 border-2 border-dashed border-line2 rounded-2xl flex flex-col items-center justify-center gap-2 bg-[#081221]">
            <FileSpreadsheet className="w-8 h-8 text-acc2" />
            <span className="font-semibold text-white">Выберите файл Excel (.xlsx) или CSV (.csv)</span>
            <input
              type="file"
              accept=".csv, .xlsx, .xls"
              onChange={(e) => setSelectedFile(e.target.files[0])}
              className="text-muted text-xs file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-acc/20 file:text-acc2 hover:file:bg-acc/30 cursor-pointer"
            />
          </div>
          <div className="flex items-center justify-end gap-3 mt-2">
            <button type="button" onClick={() => setShowImportModal(false)} className="px-4 py-2 rounded-xl bg-line/50 text-white">
              Отмена
            </button>
            <button
              type="submit"
              disabled={importLoading}
              className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg disabled:opacity-50"
            >
              {importLoading ? 'Загрузка...' : 'Загрузить базу'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Detailed Import Summary & Duplicate Breakdown Modal */}
      <Modal
        isOpen={!!importReport}
        onClose={() => setImportReport(null)}
        title="Результаты загрузки и проверки дублей"
      >
        {importReport && (
          <div className="flex flex-col gap-4 text-xs">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="p-3 rounded-xl bg-ok/10 border border-ok/30 flex flex-col items-center justify-center text-center">
                <CheckCircle className="w-5 h-5 text-ok mb-1" />
                <span className="text-dim text-[11px]">Новых добавлено</span>
                <b className="text-lg text-white font-bold">+{importReport.added || 0}</b>
              </div>

              <div className="p-3 rounded-xl bg-acc/10 border border-acc/30 flex flex-col items-center justify-center text-center">
                <Info className="w-5 h-5 text-acc2 mb-1" />
                <span className="text-dim text-[11px]">Обновлено существующих</span>
                <b className="text-lg text-white font-bold">{importReport.updated || 0}</b>
              </div>

              <div className="p-3 rounded-xl bg-warn/10 border border-warn/30 flex flex-col items-center justify-center text-center">
                <AlertTriangle className="w-5 h-5 text-warn mb-1" />
                <span className="text-dim text-[11px]">Пропущено (дубли/ошибки)</span>
                <b className="text-lg text-white font-bold">{importReport.skipped || 0}</b>
              </div>

              <div className="p-3 rounded-xl bg-line/40 border border-line flex flex-col items-center justify-center text-center">
                <FileSpreadsheet className="w-5 h-5 text-muted mb-1" />
                <span className="text-dim text-[11px]">Обработано строк</span>
                <b className="text-lg text-white font-bold">{importReport.total_rows || 0}</b>
              </div>
            </div>

            {importReport.errors && importReport.errors.length > 0 && (
              <div className="flex flex-col gap-2 mt-2">
                <span className="font-bold text-white text-xs">Детализация замечаний ({importReport.errors.length}):</span>
                <div className="max-h-40 overflow-y-auto border border-line rounded-xl bg-[#081221] p-2 flex flex-col gap-1">
                  {importReport.errors.map((err, idx) => (
                    <div key={idx} className="flex items-center justify-between text-[11px] p-1.5 rounded hover:bg-white/5 border-b border-line/30">
                      <span className="text-muted font-mono">Строка #{err.row}</span>
                      <span className="text-white font-mono">{err.phone || '—'}</span>
                      <span className="text-warn font-medium">{err.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center justify-end mt-4 pt-3 border-t border-line">
              <button
                onClick={() => setImportReport(null)}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white text-xs shadow-lg"
              >
                Закрыть
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Contact Details Card Modal */}
      <Modal
        isOpen={!!cardContactData}
        onClose={() => setCardContactData(null)}
        title={`Карточка клиента: ${cardContactData?.contact?.name || 'Без имени'}`}
        wide={true}
      >
        {cardLoading ? (
          <div className="py-8 text-center text-muted animate-pulse">Загрузка карточки...</div>
        ) : (
          <div className="flex flex-col gap-4 text-xs">
            <div className="p-4 rounded-xl bg-[#081221] border border-line grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <span className="text-dim block">Телефон</span>
                <span className="font-mono font-bold text-white">{cardContactData?.contact?.phone}</span>
              </div>
              <div>
                <span className="text-dim block">Группа</span>
                <span className="font-semibold text-white">{cardContactData?.contact?.group_name || '—'}</span>
              </div>
              <div>
                <span className="text-dim block">Согласие 152-ФЗ</span>
                <span className={cardContactData?.contact?.consent ? 'text-ok font-semibold' : 'text-bad font-semibold'}>
                  {cardContactData?.contact?.consent ? 'Есть' : 'Нет'}
                </span>
              </div>
              <div>
                <button
                  onClick={() => handleComplaint(cardContactData?.contact?.phone)}
                  className="px-3 py-1.5 rounded-lg bg-bad/20 hover:bg-bad/30 text-bad font-semibold text-xs flex items-center gap-1"
                >
                  <ShieldAlert className="w-3.5 h-3.5" /> Жалоба / В ЧС
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <span className="font-bold text-white text-sm">История вызовов клиента ({cardContactData?.calls?.length || 0})</span>
              <div className="overflow-x-auto border border-line rounded-xl bg-[#081221]">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-line text-muted uppercase text-[10px]">
                      <th className="py-2 px-3">Дата</th>
                      <th className="py-2 px-3">Кампания</th>
                      <th className="py-2 px-3">Длительность</th>
                      <th className="py-2 px-3">Результат</th>
                    </tr>
                  </thead>
                  <tbody>
                    {!cardContactData?.calls || cardContactData.calls.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="py-4 text-center text-dim">История звонков пуста</td>
                      </tr>
                    ) : (
                      cardContactData.calls.map((cl) => (
                        <tr key={cl.id} className="border-b border-line/40">
                          <td className="py-2 px-3 text-muted">{cl.started_at?.slice(0, 16).replace('T', ' ')}</td>
                          <td className="py-2 px-3 text-white">#{cl.campaign_id} {cl.camp_name || ''}</td>
                          <td className="py-2 px-3 text-muted">{cl.duration_sec ? `${Math.round(cl.duration_sec)} с` : '—'}</td>
                          <td className="py-2 px-3"><span className="badge">{cl.result || cl.status}</span></td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
