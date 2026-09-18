import React, { useState, useEffect } from 'react';
import { Upload, Plus, Trash2, Users } from 'lucide-react';
import { api } from '../api';

export default function Contacts({ addToast }) {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);

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

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">База контактов и клиенты</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => addToast('Загрузка CSV/XLSX файлов поддерживается', 'info')}
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
                  База контактов пуста. Загрузите CSV/XLSX файл.
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
    </div>
  );
}
