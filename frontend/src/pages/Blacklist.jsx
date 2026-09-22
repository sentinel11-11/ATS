import React, { useState, useEffect } from 'react';
import { ShieldAlert, Plus, Trash2 } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function Blacklist({ addToast, refreshKey }) {
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newItem, setNewItem] = useState({
    phone: '',
    reason: 'Отказ клиента / Не звонить',
  });

  const loadBlacklist = async () => {
    setLoading(true);
    const res = await api('/blacklist');
    if (res && res.blacklist) {
      setList(Array.isArray(res.blacklist) ? res.blacklist : []);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadBlacklist();
  }, [refreshKey]);

  const handleAdd = async () => {
    if (!newItem.phone) return addToast('Укажите телефон', 'warn');

    const res = await api('/blacklist/add', { body: newItem });
    if (res && res.ok) {
      addToast('Номер внесен в чёрный список', 'ok');
      setShowAddModal(false);
      setNewItem({ phone: '', reason: 'Отказ клиента / Не звонить' });
      loadBlacklist();
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Удалить номер из чёрного списка?')) return;
    const res = await api('/blacklist/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Номер удален из ЧС', 'ok');
      loadBlacklist();
    } else {
      addToast('Ошибка удаления', 'err');
    }
  };

  const blacklistList = Array.isArray(list) ? list : [];

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Чёрный список («Не звонить»)</h1>
          <p className="text-xs text-muted">Заблокировано номеров: {blacklistList.length}</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="px-4 py-2 rounded-xl bg-bad/20 hover:bg-bad/30 border border-bad/30 text-bad font-semibold text-xs flex items-center gap-1.5 transition-all"
        >
          <Plus className="w-4 h-4" /> Добавить в ЧС
        </button>
      </div>

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
                <td colSpan={4} className="py-8 text-center text-muted">Загрузка чёрного списка...</td>
              </tr>
            ) : blacklistList.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-dim">Чёрный список пуст.</td>
              </tr>
            ) : (
              blacklistList.map((item) => (
                <tr key={item.id} className="border-b border-line/40 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-mono font-bold text-bad">{item.phone}</td>
                  <td className="py-3 px-3 text-muted">{item.reason || 'Заявка на исключение'}</td>
                  <td className="py-3 px-3 text-dim">{(item.created_at || item.created)?.slice(0, 16).replace('T', ' ')}</td>
                  <td className="py-3 px-3 text-right">
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="p-1 rounded-lg text-muted hover:text-bad"
                      title="Удалить из ЧС"
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

      <Modal isOpen={showAddModal} onClose={() => setShowAddModal(false)} title="Добавить номер в Чёрный список">
        <div className="flex flex-col gap-4 text-xs">
          <div>
            <label className="font-semibold text-muted block mb-1">Телефон (E.164)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm font-mono text-white outline-none focus:border-acc"
              value={newItem.phone}
              onChange={(e) => setNewItem({ ...newItem, phone: e.target.value })}
              placeholder="79260000000"
            />
          </div>
          <div>
            <label className="font-semibold text-muted block mb-1">Причина добавления</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={newItem.reason}
              onChange={(e) => setNewItem({ ...newItem, reason: e.target.value })}
              placeholder="Отказ клиента / Не звонить"
            />
          </div>
          <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
            <button onClick={() => setShowAddModal(false)} className="px-4 py-2 rounded-xl bg-line/50 text-white">
              Отмена
            </button>
            <button onClick={handleAdd} className="px-5 py-2 rounded-xl bg-bad text-white font-bold shadow-lg">
              Заблокировать
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
