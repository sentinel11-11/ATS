import React, { useState, useEffect } from 'react';
import { PhoneCall, Plus, Trash2, Edit2, ShieldAlert } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function NumberPool({ addToast }) {
  const [numbers, setNumbers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingNum, setEditingNum] = useState(null); // null = closed

  const loadNumbers = async () => {
    setLoading(true);
    const res = await api('/numbers');
    if (res && res.numbers) {
      setNumbers(res.numbers);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadNumbers();
  }, []);

  const handleOpenEdit = (num) => {
    const n = num || {
      id: 0,
      number: '79990000000',
      label: 'Исходящий №1',
      kind: 'mobile',
      provider: 'megafon_vats',
      daily_limit: 100,
      weight: 1,
      active: true,
      quarantined: false,
    };
    setEditingNum(n);
  };

  const handleSave = async () => {
    if (!editingNum || !editingNum.number) return addToast('Укажите номер телефона', 'warn');
    const res = await api('/numbers/save', { body: editingNum });
    if (res && res.ok) {
      addToast('Номер сохранён в пуле', 'ok');
      setEditingNum(null);
      loadNumbers();
    } else {
      addToast('Ошибка сохранения: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Удалить номер из пула?')) return;
    const res = await api('/numbers/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Номер удалён из пула', 'ok');
      loadNumbers();
    }
  };

  const handleQuarantine = async (id, quarantined) => {
    const res = await api('/numbers/quarantine', { body: { id, quarantined } });
    if (res && res.ok) {
      addToast(quarantined ? 'Номер отправлен в карантин' : 'Карантин снят', 'ok');
      loadNumbers();
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Пул номеров Caller ID (Ротация & Антиспам)</h1>
        <button
          onClick={() => handleOpenEdit(null)}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Plus className="w-4 h-4" /> Добавить номер в пул
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {loading ? (
          <div className="text-center py-8 text-muted col-span-3">Загрузка номеров...</div>
        ) : numbers.length === 0 ? (
          <div className="glass-panel p-8 rounded-2xl text-center text-dim col-span-3">
            Пул номеров пуст. Добавьте исходящие номера для ротации Caller ID.
          </div>
        ) : (
          numbers.map((n) => (
            <div key={n.id} className="glass-panel p-5 rounded-2xl flex flex-col justify-between gap-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-base font-bold text-white">{n.number}</span>
                <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                  n.quarantined ? 'bg-bad/20 text-bad' : n.active ? 'bg-ok/20 text-ok' : 'bg-line text-muted'
                }`}>
                  {n.quarantined ? 'Карантин' : n.active ? 'Активен' : 'Отключен'}
                </span>
              </div>
              <p className="text-xs text-muted">
                Метка: {n.label || '—'} · Провайдер: <b className="text-white uppercase">{n.provider}</b>
              </p>
              <div className="text-[11px] text-dim border-t border-line pt-2 flex items-center justify-between">
                <span>Лимит: {n.daily_limit || 100} / сут</span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => handleOpenEdit(n)}
                    className="p-1 rounded-lg text-muted hover:text-white"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => handleQuarantine(n.id, !n.quarantined)}
                    className={`p-1 rounded-lg ${n.quarantined ? 'text-ok' : 'text-warn'}`}
                    title={n.quarantined ? 'Снять карантин' : 'В карантин'}
                  >
                    <ShieldAlert className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => handleDelete(n.id)}
                    className="p-1 rounded-lg text-muted hover:text-bad"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Modal */}
      <Modal
        isOpen={!!editingNum}
        onClose={() => setEditingNum(null)}
        title={editingNum?.id ? `Номер #${editingNum.id}` : 'Новый номер Caller ID'}
      >
        {editingNum && (
          <div className="flex flex-col gap-4 text-xs">
            <div>
              <label className="font-semibold text-muted block mb-1">Номер телефона (E.164)</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm text-white font-mono outline-none focus:border-acc"
                value={editingNum.number || ''}
                onChange={(e) => setEditingNum({ ...editingNum, number: e.target.value })}
                placeholder="79260000000"
              />
            </div>

            <div>
              <label className="font-semibold text-muted block mb-1">Метка / Описание</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={editingNum.label || ''}
                onChange={(e) => setEditingNum({ ...editingNum, label: e.target.value })}
                placeholder="Основной номер МегаФон"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Провайдер</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingNum.provider || 'megafon_vats'}
                  onChange={(e) => setEditingNum({ ...editingNum, provider: e.target.value })}
                >
                  <option value="megafon_vats">МегаФон ВАТС</option>
                  <option value="uis">UIS</option>
                  <option value="ami">Asterisk AMI</option>
                  <option value="sim">SIM стенд</option>
                </select>
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Суточный лимит вызовов</label>
                <input
                  type="number"
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingNum.daily_limit || 100}
                  onChange={(e) => setEditingNum({ ...editingNum, daily_limit: parseInt(e.target.value) || 100 })}
                />
              </div>
            </div>

            <div className="flex items-center gap-4 pt-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editingNum.active ?? true}
                  onChange={(e) => setEditingNum({ ...editingNum, active: e.target.checked })}
                  className="accent-acc"
                />
                <span className="text-white font-medium">Номер активен</span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editingNum.quarantined ?? false}
                  onChange={(e) => setEditingNum({ ...editingNum, quarantined: e.target.checked })}
                  className="accent-bad"
                />
                <span className="text-bad font-medium">Карантин (Антиспам)</span>
              </label>
            </div>

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
              <button
                onClick={() => setEditingNum(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold text-xs"
              >
                Отмена
              </button>
              <button
                onClick={handleSave}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
              >
                Сохранить номер
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
