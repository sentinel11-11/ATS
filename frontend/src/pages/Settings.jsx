import React, { useState, useEffect } from 'react';
import { Save, Shield, PhoneCall, Cpu, RefreshCw, Key, CheckCircle, Database, Users, Plus, Edit2, Trash2 } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function Settings({ addToast }) {
  const [loading, setLoading] = useState(true);
  const [providerConfig, setProviderConfig] = useState({});
  const [basicSettings, setBasicSettings] = useState({});
  const [users, setUsers] = useState([]);
  const [editingUser, setEditingUser] = useState(null); // User Modal
  const [newPassword, setNewPassword] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    const [rawRes, basicRes, usersRes] = await Promise.all([
      api('/settings/raw'),
      api('/settings'),
      api('/users')
    ]);

    if (rawRes && rawRes.provider_config) setProviderConfig(rawRes.provider_config);
    if (basicRes && basicRes.settings) setBasicSettings(basicRes.settings);
    if (usersRes && usersRes.users) setUsers(usersRes.users);
    setLoading(false);
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleSaveSettings = async () => {
    setActionLoading(true);
    const bodyBasic = { ...basicSettings };
    if (newPassword) {
      bodyBasic.new_password = newPassword;
    }

    const [rawRes, basicRes] = await Promise.all([
      api('/settings/raw', { body: { provider_config: providerConfig } }),
      api('/settings/save', { body: bodyBasic })
    ]);

    setActionLoading(false);
    if ((rawRes && rawRes.ok) || (basicRes && basicRes.ok)) {
      addToast('Все системные настройки успешно сохранены', 'ok');
      setNewPassword('');
      loadAll();
    } else {
      addToast('Ошибка сохранения настроек: ' + (rawRes?.error || basicRes?.error || '?'), 'err');
    }
  };

  const runMegafonAction = async (endpoint, name) => {
    setActionLoading(true);
    const res = await api(endpoint, { method: 'POST', body: {} });
    setActionLoading(false);
    if (res && (res.ok || res.connected || res.status === 'ok')) {
      addToast(`Успешно: ${name}`, 'ok');
    } else {
      addToast(`Ошибка (${name}): ` + (res?.error || res?.detail || 'Не удалось выполнить'), 'err');
    }
  };

  // User Management Handlers
  const handleOpenEditUser = (u) => {
    const usr = u || {
      id: 0,
      login: '',
      op_name: '',
      role: 'operator',
      ext: '',
      password: '',
      active: true,
    };
    setEditingUser(usr);
  };

  const handleSaveUser = async () => {
    if (!editingUser || !editingUser.login) return addToast('Укажите логин пользователя', 'warn');
    if (!editingUser.id && (!editingUser.password || editingUser.password.length < 6)) {
      return addToast('Пароль для нового пользователя — минимум 6 символов', 'warn');
    }

    const res = await api('/users/save', {
      body: {
        id: editingUser.id,
        login: editingUser.login,
        name: editingUser.op_name || editingUser.name,
        ext: editingUser.ext,
        role: editingUser.role,
        password: editingUser.password,
        active: editingUser.active,
      },
    });

    if (res && res.ok) {
      addToast('Пользователь сохранён', 'ok');
      setEditingUser(null);
      loadAll();
    } else {
      addToast('Ошибка сохранения пользователя: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDeleteUser = async (id) => {
    if (!window.confirm('Удалить пользователя из системы?')) return;
    const res = await api('/users/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Пользователь удалён', 'ok');
      loadAll();
    } else {
      addToast('Ошибка удаления', 'err');
    }
  };

  const updateProvider = (section, field, value) => {
    setProviderConfig((prev) => ({
      ...prev,
      [section]: {
        ...(prev[section] || {}),
        [field]: value,
      },
    }));
  };

  const updateBasic = (field, value) => {
    setBasicSettings((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  if (loading) return <div className="p-8 text-center text-muted animate-pulse">Загрузка подробных настроек...</div>;

  const megafon = providerConfig.megafon_vats || {};
  const uis = providerConfig.uis || {};
  const ami = providerConfig.ami || {};

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200 pb-12">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Системные настройки и Провайдеры</h1>
        <button
          onClick={handleSaveSettings}
          disabled={actionLoading}
          className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all disabled:opacity-50"
        >
          <Save className="w-4 h-4" /> Сохранить все настройки
        </button>
      </div>

      {/* SECTION 1: Users & Operators Management */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Users className="w-5 h-5 text-acc2" /> Пользователи и Операторы системы ({users.length})
          </h2>
          <button
            onClick={() => handleOpenEditUser(null)}
            className="px-3 py-1.5 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white flex items-center gap-1 hover:brightness-110"
          >
            <Plus className="w-3.5 h-3.5" /> Новый пользователь / оператор
          </button>
        </div>

        <div className="overflow-x-auto border border-line rounded-xl bg-[#081221]">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line text-muted uppercase text-[10px] bg-[#0d1a2e]">
                <th className="py-2.5 px-3">Логин</th>
                <th className="py-2.5 px-3">Имя оператора</th>
                <th className="py-2.5 px-3">Роль</th>
                <th className="py-2.5 px-3">Внутр. номер</th>
                <th className="py-2.5 px-3">Доступ</th>
                <th className="py-2.5 px-3 text-right">Действия</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-4 text-center text-dim">Пользователей нет</td>
                </tr>
              ) : (
                users.map((u) => (
                  <tr key={u.id} className="border-b border-line/40 hover:bg-white/5">
                    <td className="py-2.5 px-3 font-semibold text-white">@{u.login}</td>
                    <td className="py-2.5 px-3 text-muted">{u.op_name || u.name || '—'}</td>
                    <td className="py-2.5 px-3">
                      <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                        u.role === 'admin' ? 'bg-acc/20 text-acc2' : 'bg-line text-muted'
                      }`}>
                        {u.role === 'admin' ? 'Администратор' : 'Оператор'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono text-muted">{u.ext || '—'}</td>
                    <td className="py-2.5 px-3">
                      <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                        u.active !== 0 ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad'
                      }`}>
                        {u.active !== 0 ? 'Разрешён' : 'Отключён'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => handleOpenEditUser(u)}
                          className="p-1 rounded-lg text-muted hover:text-white"
                          title="Редактировать"
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleDeleteUser(u.id)}
                          className="p-1 rounded-lg text-muted hover:text-bad"
                          title="Удалить"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* SECTION 2: Basic Telephony Settings & Limits */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <PhoneCall className="w-5 h-5 text-acc2" /> Основной провайдер и лимиты
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1 font-semibold">Провайдер телефонии</label>
            <select
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
              value={basicSettings.provider || ''}
              onChange={(e) => updateBasic('provider', e.target.value)}
            >
              <option value="">Не выбран (Fail-closed)</option>
              <option value="megafon_vats">МегаФон ВАТС REST API</option>
              <option value="uis">UIS / Comagic API</option>
              <option value="ami">Asterisk AMI Manager</option>
              <option value="sim">Симуляция (Стенд)</option>
            </select>
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Макс. одновременных каналов</label>
            <input
              type="number"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.max_channels || 3}
              onChange={(e) => updateBasic('max_channels', parseInt(e.target.value) || 1)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Макс. попыток автодозвона</label>
            <input
              type="number"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.retry_max || 2}
              onChange={(e) => updateBasic('retry_max', parseInt(e.target.value) || 1)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Интервал повтора (мин)</label>
            <input
              type="number"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.retry_delay_min || 15}
              onChange={(e) => updateBasic('retry_delay_min', parseInt(e.target.value) || 5)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Рабочее время (Старт)</label>
            <input
              type="text"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.window_start || '08:00'}
              onChange={(e) => updateBasic('window_start', e.target.value)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Рабочее время (Окончание)</label>
            <input
              type="text"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.window_end || '20:00'}
              onChange={(e) => updateBasic('window_end', e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* SECTION 3: Megafon VATS Settings */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Shield className="w-5 h-5 text-ok" /> Интеграция МегаФон ВАТС
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => runMegafonAction('/megafon/check', 'Проверка связи с ВАТС')}
              className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
            >
              <CheckCircle className="w-3.5 h-3.5 text-ok" /> Проверить связь
            </button>
            <button
              type="button"
              onClick={() => runMegafonAction('/megafon/pool-sync', 'Синхронизация пула')}
              className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5 text-acc2" /> Синк пула
            </button>
            <button
              type="button"
              onClick={() => runMegafonAction('/megafon/users-sync', 'Синхронизация сотрудников')}
              className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5 text-cy" /> Синк сотрудников
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1 font-semibold">Base URL (REST API ВАТС)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.base_url || ''}
              onChange={(e) => updateProvider('megafon_vats', 'base_url', e.target.value)}
              placeholder="https://vats326349.megapbx.ru/crmapi/v1"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Оператор по умолчанию (default_user)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.default_user || ''}
              onChange={(e) => updateProvider('megafon_vats', 'default_user', e.target.value)}
              placeholder="admin"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">API Key (Авторизация в АТС)</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.api_key || ''}
              onChange={(e) => updateProvider('megafon_vats', 'api_key', e.target.value)}
              placeholder="Ключ авторизации ВАТС"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">CRM Token (для входящих вебхуков)</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.crm_token || ''}
              onChange={(e) => updateProvider('megafon_vats', 'crm_token', e.target.value)}
              placeholder="Токен CRM"
            />
          </div>
        </div>
      </div>

      {/* SECTION 4: Security & Password Change */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <Key className="w-5 h-5 text-bad" /> Безопасность (Смена пароля текущего администратора)
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1 font-semibold">Новый пароль администратора</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Укажите новый надежный пароль"
            />
          </div>
        </div>
      </div>

      {/* Edit User / Operator Modal */}
      <Modal
        isOpen={!!editingUser}
        onClose={() => setEditingUser(null)}
        title={editingUser?.id ? `Пользователь @${editingUser.login}` : 'Новый пользователь / оператор'}
      >
        {editingUser && (
          <div className="flex flex-col gap-4 text-xs">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Логин (для входа)</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingUser.login || ''}
                  onChange={(e) => setEditingUser({ ...editingUser, login: e.target.value.toLowerCase().trim() })}
                  placeholder="operator2"
                />
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Имя оператора</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={editingUser.op_name || editingUser.name || ''}
                  onChange={(e) => setEditingUser({ ...editingUser, op_name: e.target.value })}
                  placeholder="Иван Оператор"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="font-semibold text-muted block mb-1">Роль в системе</label>
                <select
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                  value={editingUser.role || 'operator'}
                  onChange={(e) => setEditingUser({ ...editingUser, role: e.target.value })}
                >
                  <option value="operator">Оператор (принимает звонки)</option>
                  <option value="admin">Администратор (полный доступ)</option>
                </select>
              </div>

              <div>
                <label className="font-semibold text-muted block mb-1">Внутренний номер</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-mono"
                  value={editingUser.ext || ''}
                  onChange={(e) => setEditingUser({ ...editingUser, ext: e.target.value })}
                  placeholder="101"
                />
              </div>
            </div>

            <div>
              <label className="font-semibold text-muted block mb-1">
                Пароль {editingUser.id ? '(оставить пустым — не менять)' : '(мин. 6 символов)'}
              </label>
              <input
                type="password"
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={editingUser.password || ''}
                onChange={(e) => setEditingUser({ ...editingUser, password: e.target.value })}
                placeholder="••••••••"
              />
            </div>

            <div className="flex items-center gap-2 pt-2">
              <input
                type="checkbox"
                id="uActiveCheck"
                checked={editingUser.active !== 0 && editingUser.active !== false}
                onChange={(e) => setEditingUser({ ...editingUser, active: e.target.checked })}
                className="accent-acc"
              />
              <label htmlFor="uActiveCheck" className="text-white font-medium cursor-pointer">
                Доступ разрешён (активен)
              </label>
            </div>

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
              <button
                type="button"
                onClick={() => setEditingUser(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold text-xs"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleSaveUser}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
              >
                Сохранить пользователя
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
