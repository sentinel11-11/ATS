import React, { useState, useEffect } from 'react';
import { Upload, Plus, Trash2, Users, FileSpreadsheet } from 'lucide-react';
import { api, getToken } from '../api';
import Modal from '../components/Modal';

export default function Contacts({ addToast }) {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showImportModal, setShowImportModal] = useState(false);
  const [showNewModal, setShowNewModal] = useState(false);
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
    const res = await api('/contacts');
    if (res && res.contacts) {
      setContacts(res.contacts);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadContacts();
  }, []);

  const handleSaveContact = async () => {
    if (!newContact.phone) return addToast('Укажите телефон', 'warn');
    const res = await api('/contacts/save', { body: newContact });
    if (res && res.ok) {
      addToast('Контакт сохранён', 'ok');
      setShowNewModal(false);
      setNewContact({ phone: '', name: '', group_name: '', consent: true });
      loadContacts();
    } else {
      addToast('Ошибка: ' + (res?.error || '?'), 'err');
    }
  };

  const handleImportFile = async (e) => {
    e.preventDefault();
    if (!selectedFile) return addToast('Выберите файл CSV или Excel', 'warn');
    setImportLoading(true);

    const formData = new FormData();
    formData.append('file', selectedFile);

    const token = getToken();
    try {
      const res = await fetch('/api/v2/contacts/import-file', {
        method: 'POST',
        headers: { 'X-Ats-Token': token },
        body: formData,
      });
      const data = await res.json();
      setImportLoading(false);

      if (data && data.ok) {
        addToast(`Импортировано: +${data.added || 0} контактов`, 'ok');
        setShowImportModal(false);
        setSelectedFile(null);
        loadContacts();
      } else {
        addToast('Ошибка импорта: ' + (data?.error || '?'), 'err');
      }
    } catch (err) {
      setImportLoading(false);
      addToast('Ошибка при загрузке файла', 'err');
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">База контактов и клиенты</h1>
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

      <div className="glass-panel p-6 rounded-2xl overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-line text-muted uppercase text-[10px]">
              <th className="py-2.5 px-3">Имя</th>
              <th className="py-2.5 px-3">Телефон</th>
              <th className="py-2.5 px-3">Группа</th>
              <th className="py-2.5 px-3">Согласие (152-ФЗ)</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-muted">
                  Загрузка контактов...
                </td>
              </tr>
            ) : contacts.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-dim">
                  База контактов пуста. Импортируйте CSV/XLSX файл или добавьте контакт.
                </td>
              </tr>
            ) : (
              contacts.slice(0, 100).map((c) => (
                <tr key={c.id} className="border-b border-line/50 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-semibold text-white">{c.name || 'Без имени'}</td>
                  <td className="py-3 px-3 font-mono text-muted">{c.phone}</td>
                  <td className="py-3 px-3 text-muted">{c.group_name || '—'}</td>
                  <td className="py-3 px-3">
                    <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                      c.consent ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad'
                    }`}>
                      {c.consent ? 'Есть' : 'Нет'}
                    </span>
                  </td>
                </tr>
              ))
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
    </div>
  );
}
