import React, { useState, useEffect } from 'react';
import { Plus, Shield, Power, Trash2, Edit2, ShieldAlert, PhoneCall, RefreshCw, Sliders } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function NumberPool({ addToast, refreshKey }) {
  const [numbers, setNumbers] = useState([]);
  const [loading, setLoading] = useState(true);

  // Modals
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingNumber, setEditingNumber] = useState(null);

  const [newNumber, setNewContactNumber] = useState({
    phone: '',
    label: '',
    provider: 'megafon_vats',
    kind: 'mobile',
    daily_limit: 100,
    weight: 1,
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
  }, [refreshKey]);

  const handleToggleActive = async (id, currentActive) => {
    const res = await api('/numbers/save', {
      body: { id, active: !currentActive },
    });

    if (res && res.ok) {
      addToast(`Номер ${!currentActive ? 'включен' : 'отключен'}`, 'ok');
      loadNumbers();
    } else {
      addToast('Ошибка изменения статуса', 'err');
    }
  };

  const handleToggleQuarantine = async (id, currentQuarantined) => {
    const res = await api('/numbers/quarantine', {
      body: { id, on: !currentQuarantined },
    });

    if (res && res.ok) {
      addToast(`Карантин ${!currentQuarantined ? 'установлен' : 'снят'}`, 'ok');
      loadNumbers();
    } else {
      addToast('Ошибка смены карантина', 'err');
    }
  };

  const handleResetCounters = async () => {
    const res = await api('/numbers/reset', { method: 'POST', body: {} });
    if (res && res.ok) {
      addToast('Суточные счётчики и статусы остывания сброшены', 'ok');
      loadNumbers();
    } else {
      addToast('Ошибка сброса счётчиков', 'err');
    }
  };

  const handleAddNumber = async () => {
    if (!newNumber.phone) return addToast('Укажите номер телефона', 'warn');

    const res = await api('/numbers/save', {
      body: {
        number: newNumber.phone,
        label: newNumber.label,
        provider: newNumber.provider,
        kind: newNumber.kind,
        daily_limit: newNumber.daily_limit,
        weight: newNumber.weight,
      },
    });

    if (res && res.ok) {
      addToast('Номер добавлен в пул', 'ok');
      setShowAddModal(false);
      setNewContactNumber({ phone: '', label: '', provider: 'megafon_vats', kind: 'mobile', daily_limit: 100, weight: 1 });
      loadNumbers();
    } else {
      addToast('Ошибка добавления: ' + (res?.error || '?'), 'err');
    }
  };

  const handleSaveEditedNumber = async () => {
    if (!editingNumber) return;

    const res = await api('/numbers/save', {
      body: {
        id: editingNumber.id,
        label: editingNumber.label,
        provider: editingNumber.provider,
        kind: editingNumber.kind,
        daily_limit: editingNumber.daily_limit,
        weight: editingNumber.weight,
        active: editingNumber.active,
        enabled_outgoing: editingNumber.enabled_outgoing,
        quarantined: editingNumber.quarantined,
      },
    });

    if (res && res.ok) {
      addToast('Настройки номера сохранены', 'ok');
      setEditingNumber(null);
      loadNumbers();
    } else {
      addToast('Ошибка сохранения карточки номера', 'err');
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
    <div className="flex flex-col gap-6 animate-in fade-in duration-200 pb-12">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Пул номеров Caller ID (Ротация & Антиспам)</h1>
          <p className="text-xs text-muted">Всего номеров в ротации: {numbersList.length}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleResetCounters}
            className="px-3.5 py-2 rounded-xl bg-line/60 hover:bg-line text-white font-semibold text-xs flex items-center gap-1.5 transition-all"
            title="Сбросить суточные лимиты и таймеры остывания"
          >
            <RefreshCw className="w-3.5 h-3.5 text-acc2" /> Сбросить счётчики
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
          >
            <Plus className="w-4 h-4" /> Добавить номер в пул
          </button>
        </div>
      </div>

      {/* Number Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {loading ? (
          <div className="p-8 text-center text-muted col-span-3 animate-pulse">Загрузка номеров пула...</div>
        ) : numbersList.length === 0 ? (
          <div className="glass-panel p-8 rounded-2xl text-center text-dim col-span-3">
            В пуле нет номеров. Добавьте первый номер для исходящих вызовов.
          </div>
        ) : (
          numbersList.map((n) => {
            const phoneStr = n.number || n.phone || 'Без номера';

            return (
              <div
                key={n.id}
                className={`glass-panel p-5 rounded-2xl flex flex-col justify-between gap-4 border transition-all ${
                  n.quarantined
                    ? 'border-bad/40 bg-bad/5'
                    : n.active
                    ? 'border-line hover:border-acc/40'
                    : 'border-line/40 opacity-75'
                }`}
              >
                <div className="flex items-start justify-between">
                  <div>
                    {/* Large Visible Phone Number */}
                    <b className="text-xl font-mono text-white block tracking-wide">{phoneStr}</b>
                    <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                          n.active ? 'bg-ok/20 text-ok border border-ok/30' : 'bg-bad/20 text-bad border border-bad/30'
                        }`}
                      >
                        {n.active ? 'Активен' : 'Отключен'}
                      </span>

                      {n.quarantined && (
                        <span className="px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-bad text-white flex items-center gap-1">
                          <ShieldAlert className="w-3 h-3" /> Карантин (ЧС)
                        </span>
                      )}

                      {n.cooling && (
                        <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-warn/20 text-warn border border-warn/30">
                          Остывает
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setEditingNumber({ ...n })}
                      className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-line/60 transition-colors"
                      title="Настройки карточки номера"
                    >
                      <Sliders className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDeleteNumber(n.id)}
                      className="p-1.5 rounded-lg text-muted hover:text-bad hover:bg-bad/10 transition-colors"
                      title="Удалить номер"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Card Full Details */}
                <div className="p-3 rounded-xl bg-[#081221] border border-line/60 grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <span className="text-dim text-[11px] block">Метка</span>
                    <span className="font-semibold text-white truncate block">{n.label || '—'}</span>
                  </div>

                  <div>
                    <span className="text-dim text-[11px] block">Провайдер</span>
                    <span className="font-semibold text-acc2 block">{n.provider}</span>
                  </div>

                  <div>
                    <span className="text-dim text-[11px] block">Суточный лимит</span>
                    <span className="font-semibold text-white">
                      {n.daily_count || 0} / {n.daily_limit || 100}
                    </span>
                  </div>

                  <div>
                    <span className="text-dim text-[11px] block">Приоритет (Вес)</span>
                    <span className="font-semibold text-white">{n.weight || 1}</span>
                  </div>

                  <div>
                    <span className="text-dim text-[11px] block">Набрано звонков</span>
                    <span className="font-semibold text-muted">{n.dialed_total || 0}</span>
                  </div>

                  <div>
                    <span className="text-dim text-[11px] block">Успешных ответов</span>
                    <span className="font-semibold text-ok">{n.answered_total || 0}</span>
                  </div>
                </div>

                {/* Quick Action Buttons */}
                <div className="pt-3 border-t border-line grid grid-cols-2 gap-2">
                  <button
                    onClick={() => handleToggleActive(n.id, n.active)}
                    className={`py-2 rounded-xl font-bold text-xs flex items-center justify-center gap-1 transition-all ${
                      n.active ? 'bg-warn/20 hover:bg-warn/30 text-warn' : 'bg-ok/20 hover:bg-ok/30 text-ok'
                    }`}
                  >
                    <Power className="w-3.5 h-3.5" /> {n.active ? 'Отключить' : 'Включить'}
                  </button>

                  <button
                    onClick={() => handleToggleQuarantine(n.id, n.quarantined)}
                    className={`py-2 rounded-xl font-bold text-xs flex items-center justify-center gap-1 transition-all ${
                      n.quarantined ? 'bg-ok/20 hover:bg-ok/30 text-ok' : 'bg-bad/20 hover:bg-bad/30 text-bad'
                    }`}
                  >
                    <ShieldAlert className="w-3.5 h-3.5" /> {n.quarantined ? 'Снять ЧС' : 'В Карантин'}
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Add Number Modal */}
      <Modal isOpen={showAddModal} onClose={() => setShowAddModal(false)} title="Добавить новый номер в пул">
        <div className="flex flex-col gap-4 text-xs">
          <div>
            <label className="font-semibold text-muted block mb-1">Номер телефона (E.164)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm font-mono text-white outline-none focus:border-acc font-bold"
              value={newNumber.phone}
              onChange={(e) => setNewContactNumber({ ...newNumber, phone: e.target.value })}
              placeholder="79260000000"
            />
          </div>

          <div>
            <label className="font-semibold text-muted block mb-1">Метка / Название линии</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={newNumber.label}
              onChange={(e) => setNewContactNumber({ ...newNumber, label: e.target.value })}
              placeholder="Сим-1 МегаФон Москва"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="font-semibold text-muted block mb-1">Провайдер телефонии</label>
              <select
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
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
              <label className="font-semibold text-muted block mb-1">Тип линии</label>
              <select
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={newNumber.kind}
                onChange={(e) => setNewContactNumber({ ...newNumber, kind: e.target.value })}
              >
                <option value="mobile">Мобильный</option>
                <option value="landline">Городской (8-800 / 495)</option>
                <option value="sip">SIP-Транк</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="font-semibold text-muted block mb-1">Дневной лимит вызовов</label>
              <input
                type="number"
                min="1"
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={newNumber.daily_limit}
                onChange={(e) => setNewContactNumber({ ...newNumber, daily_limit: parseInt(e.target.value) || 100 })}
              />
            </div>

            <div>
              <label className="font-semibold text-muted block mb-1">Вес в ротации (Приоритет 1-10)</label>
              <input
                type="number"
                min="1"
                max="10"
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={newNumber.weight}
                onChange={(e) => setNewContactNumber({ ...newNumber, weight: parseInt(e.target.value) || 1 })}
              />
            </div>
          </div>

          <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
            <button onClick={() => setShowAddModal(false)} className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white">
              Отмена
            </button>
            <button onClick={handleAddNumber} className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg">
              Сохранить номер
            </button>
          </div>
        </div>
      </Modal>

      {/* Edit Full Number Card Modal */}
      <Modal
        isOpen={!!editingNumber}
        onClose={() => setEditingNumber(null)}
        title={`Полная настройка карточки номера: ${editingNumber?.number || editingNumber?.phone}`}
      >
        {editingNumber && (
          <div className="flex flex-col gap-4 text-xs">
            <div>
              <label className="font-semibold text-muted block mb-1">Метка / Название линии</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                value={editingNumber.label || ''}
                onChange={(e) => setEditingNumber({ ...editingNumber, label: e.target.value })}
                placeholder="Сим-1 МегаФон Москва"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="font-semibold text-muted block mb-1">Провайдер телефонии</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingNumber.provider || 'megafon_vats'}
                  onChange={(e) => setEditingNumber({ ...editingNumber, provider: e.target.value })}
                >
                  <option value="megafon_vats">МегаФон ВАТС</option>
                  <option value="uis">UIS / Comagic</option>
                  <option value="ami">Asterisk AMI</option>
                  <option value="sim">Симуляция</option>
                </select>
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Тип линии</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingNumber.kind || 'mobile'}
                  onChange={(e) => setEditingNumber({ ...editingNumber, kind: e.target.value })}
                >
                  <option value="mobile">Мобильный</option>
                  <option value="landline">Городской (8-800 / 495)</option>
                  <option value="sip">SIP-Транк</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="font-semibold text-muted block mb-1">Дневной лимит вызовов</label>
                <input
                  type="number"
                  min="1"
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingNumber.daily_limit || 100}
                  onChange={(e) => setEditingNumber({ ...editingNumber, daily_limit: parseInt(e.target.value) || 100 })}
                />
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Вес в ротации (Приоритет 1-10)</label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingNumber.weight || 1}
                  onChange={(e) => setEditingNumber({ ...editingNumber, weight: parseInt(e.target.value) || 1 })}
                />
              </div>
            </div>

            <div className="flex flex-col gap-2 pt-2 border-t border-line">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!editingNumber.active}
                  onChange={(e) => setEditingNumber({ ...editingNumber, active: e.target.checked })}
                  className="accent-acc w-4 h-4"
                />
                <span className="text-white font-medium">Активен (участвует в ротации исходящих)</span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editingNumber.enabled_outgoing !== false}
                  onChange={(e) => setEditingNumber({ ...editingNumber, enabled_outgoing: e.target.checked })}
                  className="accent-acc w-4 h-4"
                />
                <span className="text-white font-medium">Исходящие вызовы разрешены</span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!editingNumber.quarantined}
                  onChange={(e) => setEditingNumber({ ...editingNumber, quarantined: e.target.checked })}
                  className="accent-bad w-4 h-4"
                />
                <span className="text-bad font-medium">В карантине (заблокирован из-за жалоб/антиспама)</span>
              </label>
            </div>

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
              <button
                type="button"
                onClick={() => setEditingNumber(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleSaveEditedNumber}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg shadow-acc/30"
              >
                Сохранить настройки
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
