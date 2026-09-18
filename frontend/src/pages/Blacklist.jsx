import React, { useState, useEffect } from 'react';
import { ShieldAlert, Plus, Trash2 } from 'lucide-react';
import { api } from '../api';

export default function Blacklist({ addToast }) {
  const [list, setList] = useState([]);
  const [phone, setPhone] = useState('');
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(true);

  const loadList = async () => {
    setLoading(true);
    const res = await api('/blacklist');
    if (res && res.blacklist) {
      setList(res.blacklist);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadList();
  }, []);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!phone) return addToast('Укажите телефон', 'warn');
    const res = await api('/blacklist/add', { body: { phone, reason } });
    if (res && res.ok) {
      addToast('Номер добавлен в ЧС', 'ok');
      setPhone('');
      setReason('');
      loadList();
    } else {
      addToast('Ошибка добавления в ЧС', 'err');
    }
  };

  const handleDelete = async (id) => {
    const res = await api('/blacklist/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Запись удалена из ЧС', 'ok');
      loadList();
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Чёрный список («Не звонить»)</h1>
      </div>

      <form onSubmit={handleAdd} className="glass-panel p-4 rounded-2xl flex flex-col md:flex-row gap-3">
        <input
          type="text"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="Телефон (напр. 79991112233)"
          className="bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-acc flex-1"
        />
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Причина (отказ / просьба не звонить)"
          className="bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-acc flex-1"
        />
        <button
          type="submit"
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all shrink-0"
        >
          <Plus className="w-4 h-4" /> Добавить в ЧС
        </button>
      </form>

      <div className="glass-panel p-6 rounded-2xl overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-line text-muted uppercase text-[10px]">
              <th className="py-2.5 px-3">Телефон</th>
              <th className="py-2.5 px-3">Причина</th>
              <th className="py-2.5 px-3">Дата внесения</th>
              <th className="py-2.5 px-3 text-right">Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-muted">
                  Загрузка ЧС...
                </td>
              </tr>
            ) : list.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-dim">
                  Чёрный список пуст.
                </td>
              </tr>
            ) : (
              list.map((item) => (
                <tr key={item.id} className="border-b border-line/50 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-mono font-semibold text-white">{item.phone}</td>
                  <td className="py-3 px-3 text-muted">{item.reason || '—'}</td>
                  <td className="py-3 px-3 text-dim">{item.created_at?.slice(0, 10)}</td>
                  <td className="py-3 px-3 text-right">
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="p-1.5 rounded-lg text-muted hover:text-bad hover:bg-bad/10"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
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
