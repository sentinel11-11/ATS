import React, { useState, useEffect } from 'react';
import { Plus, Shield, Power, Trash2 } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function NumberPool({ addToast }) {
  const [numbers, setNumbers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newNumber, setNewContactNumber] = useState({
    phone: '',
    label: '',
    provider: 'megafon_vats',
    daily_limit: 100,
  });

  const loadNumbers = async () => {
    setLoading(true);
    const res = await api('/numbers');
    if (res && res.numbers) {
      setNumbers(Array.isArray(res.numbers) ? res.numbers : []);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadNumbers();
  }, []);

  const handleToggleActive = async (id, currentActive) => {
    const res = await api('/numbers/save', {
      body: { id, active: !currentActive },
    });

    if (res && res.ok) {
      addToast(`Номер ${!currentActive ? 'включен' : 'выключен'}`, 'ok');
      loadNumbers();
    } else {
      addToast('Ошибка изменения статуса', 'err');
    }
  };

  const handleAddNumber = async () => {
    if (!newNumber.phone) return addToast('Укажите номер телефона', 'warn');

    const res = await api('/numbers/save', { body: newNumber });
    if (res && res.ok) {
      addToast('Номер добавлен в пул', 'ok');
      setShowAddModal(false);
      setNewContactNumber({ phone: '', label: '', provider: 'megafon_vats', daily_limit: 100 });
      loadNumbers();
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDeleteNumber = async (id) => {
    if (!window.confirm('Удалить номер из пула?')) return;
    const res = await api('/numbers/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Номер удален из пула', 'ok');
      loadNumbers();
    } else {
      addToast('Ошибка удаления', 'err');
    }
  };

  const numbersList = Array.isArray(numbers) ? numbers : [];

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Пул номеров Caller ID (Ротация & Антиспам)</h1>
          <p className="text-xs text-muted">Всего номеров в ротации: {numbersList.length}</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Plus className="w-4 h-4" /> Добавить номер в пул
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          <div className="p-8 text-center text-muted col-span-3 animate-pulse">Загрузка номеров пула...</div>
        ) : numbersList.length === 0 ? (
          <div className="glass-panel p-8 rounded-2xl text-center text-dim col-span-3">
            В пуле нет номеров. Добавьте первый номер для исходящих вызовов.
          </div>
        ) : (
          numbersList.map((n) => (
            <div key={n.id} className="glass-card p-5 rounded-2xl flex flex-col justify-between gap-4">
              <div className="flex items-start justify-between">
                <div>
                  <b className="text-lg font-mono text-white block">{n.phone}</b>
                  <span
                    className={`inline-block mt-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                      n.active ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad'
                    }`}
                  >
                    {n.active ? 'Активен' : 'Отключен'}
                  </span>
                </div>
                <button
                  onClick={() => handleDeleteNumber(n.id)}
                  className="p-1.5 rounded-lg text-muted hover:text-bad hover:bg-bad/10 transition-colors"
                  title="Удалить номер"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              <div className="text-xs text-muted space-y-1">
                <div>Метка: <b className="text-white">{n.label || '—'}</b> · Провайдер: <b className="text-white">{n.provider}</b></div>
                <div>Лимит: <b className="text-white">{n.daily_limit || 100}</b> / сут</div>
              </div>

              <div className="pt-3 border-t border-line flex items-center justify-between">
                <button
                  onClick={() => handleToggleActive(n.id, n.active)}
                  className={`w-full py-2 rounded-xl font-bold text-xs flex items-center justify-center gap-1.5 transition-all ${
                    n.active ? 'bg-bad/20 hover:bg-bad/30 text-bad' : 'bg-ok/20 hover:bg-ok/30 text-ok'
                  }`}
                >
                  <Power className="w-3.5 h-3.5" /> {n.active ? 'Выкл' : 'Вкл'}
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      <Modal isOpen={showAddModal} onClose={() => setShowAddModal(false)} title="Добавить номер в пул">
        <div className="flex flex-col gap-4 text-xs">
          <div>
            <label className="font-semibold text-muted block mb-1">Номер телефона (E.164)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm font-mono text-white outline-none focus:border-acc"
              value={newNumber.phone}
              onChange={(e) => setNewContactNumber({ ...newNumber, phone: e.target.value })}
              placeholder="79260000000"
            />
          </div>
          <div>
            <label className="font-semibold text-muted block mb-1">Метка / Название</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={newNumber.label}
              onChange={(e) => setNewContactNumber({ ...newNumber, label: e.target.value })}
              placeholder="Сим-1 Москва"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="font-semibold text-muted block mb-1">Провайдер</label>
              <select
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={newNumber.provider}
                onChange={(e) => setNewContactNumber({ ...newNumber, provider: e.target.value })}
              >
                <option value="megafon_vats">МегаФон ВАТС</option>
                <option value="uis">UIS / Comagic</option>
                <option value="ami">Asterisk AMI</option>
                <option value="sim">Симуляция</option>
              </select>
            </div>
            <div>
              <label className="font-semibold text-muted block mb-1">Дневной лимит вызовов</label>
              <input
                type="number"
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={newNumber.daily_limit}
                onChange={(e) => setNewContactNumber({ ...newNumber, daily_limit: parseInt(e.target.value) || 100 })}
              />
            </div>
          </div>
          <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
            <button onClick={() => setShowAddModal(false)} className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white">
              Отмена
            </button>
            <button onClick={handleAddNumber} className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg">
              Сохранить
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
