import React, { useState, useEffect } from 'react';
import { Save, Shield, PhoneCall, Cpu, RefreshCw, Key, CheckCircle, Database, Users, Plus, Edit2, Trash2, Server, Bot, Link, Code, Radio } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';

export default function Settings({ addToast, refreshKey }) {
  const [loading, setLoading] = useState(true);
  const [providerConfig, setProviderConfig] = useState({});
  const [basicSettings, setBasicSettings] = useState({});
  const [users, setUsers] = useState([]);
  const [editingUser, setEditingUser] = useState(null);
  const [newPassword, setNewPassword] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [rawJsonText, setRawJsonText] = useState('');
  const [showRawEditor, setShowRawEditor] = useState(false);
  const [amoUsers, setAmoUsers] = useState([]);
  const [newOpKey, setNewOpKey] = useState('');
  const [newAmoId, setNewAmoId] = useState('');

  const isMaskedValue = (val) => {
    if (!val) return false;
    const s = String(val).trim();
    return s === '********' || s === '••••••••' || /^[\*\•]+$/.test(s);
  };

  const loadAll = async () => {
    setLoading(true);
    const [rawRes, basicRes, usersRes, amoUsersRes] = await Promise.all([
      api('/settings/raw'),
      api('/settings'),
      api('/users'),
      api('/amocrm/users')
    ]);

    if (rawRes && rawRes.provider_config) {
      setProviderConfig(rawRes.provider_config);
      setRawJsonText(JSON.stringify(rawRes.provider_config, null, 2));
    }
    if (basicRes && basicRes.settings) {
      setBasicSettings(basicRes.settings);
    }
    if (usersRes && usersRes.users) {
      setUsers(usersRes.users);
    }
    if (amoUsersRes && Array.isArray(amoUsersRes.users)) {
      setAmoUsers(amoUsersRes.users);
    } else {
      setAmoUsers([]);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadAll();
  }, [refreshKey]);

  const handleSaveSettings = async () => {
    setActionLoading(true);

    let currentProviderCfg = { ...providerConfig };

    // If user edited raw JSON directly, parse it
    if (showRawEditor && rawJsonText) {
      try {
        currentProviderCfg = JSON.parse(rawJsonText);
        setProviderConfig(currentProviderCfg);
      } catch (err) {
        setActionLoading(false);
        return addToast('Ошибка в формате JSON сырой конфигурации: ' + err.message, 'err');
      }
    }

    const bodyBasic = { ...basicSettings };
    if (newPassword) {
      bodyBasic.new_password = newPassword;
    }

    const [rawRes, basicRes] = await Promise.all([
      api('/settings/raw', { body: { provider_config: currentProviderCfg } }),
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
    // Автоматически сохраняем текущие введенные настройки перед проверкой/синхронизацией
    await Promise.all([
      api('/settings/raw', { body: { provider_config: providerConfig } }),
      api('/settings/save', { body: basicSettings }),
    ]);

    const res = await api(endpoint, { method: 'POST', body: megafon });
    setActionLoading(false);
    if (res && (res.ok || res.connected || res.status === 'ok')) {
      addToast(`Успешно: ${name}`, 'ok');
    } else {
      const detail = res?.detail || res?.error || res?.report?.failed_stage || 'Не удалось выполнить';
      addToast(`Ошибка (${name}): ${detail}`, 'err');
    }
  };

  const runMulticomAction = async (endpoint, name) => {
    setActionLoading(true);
    let currentProviderCfg = { ...providerConfig };

    if (showRawEditor && rawJsonText) {
      try {
        currentProviderCfg = JSON.parse(rawJsonText);
        setProviderConfig(currentProviderCfg);
      } catch (err) {
        setActionLoading(false);
        return addToast('Ошибка в формате JSON: ' + err.message, 'err');
      }
    }

    await Promise.all([
      api('/settings/raw', { body: { provider_config: currentProviderCfg } }),
      api('/settings/save', { body: basicSettings }),
    ]);

    const res = await api(endpoint, { method: 'POST', body: currentProviderCfg.multicom || {} });
    setActionLoading(false);
    if (res && (res.ok || res.connected || res.status === 'ok')) {
      addToast(`Успешно: ${name}`, 'ok');
    } else {
      const detail = res?.detail || res?.error || 'Не удалось выполнить';
      addToast(`Ошибка (${name}): ${detail}`, 'err');
    }
  };

  const runAmoCrmAction = async (endpoint, name) => {
    setActionLoading(true);
    let currentProviderCfg = { ...providerConfig };

    if (showRawEditor && rawJsonText) {
      try {
        currentProviderCfg = JSON.parse(rawJsonText);
        setProviderConfig(currentProviderCfg);
      } catch (err) {
        setActionLoading(false);
        return addToast('Ошибка в формате JSON: ' + err.message, 'err');
      }
    }

    await Promise.all([
      api('/settings/raw', { body: { provider_config: currentProviderCfg } }),
      api('/settings/save', { body: basicSettings }),
    ]);

    const res = await api(endpoint, { method: 'POST', body: currentProviderCfg.amocrm || {} });
    setActionLoading(false);
    if (res && (res.ok || res.connected || res.status === 'ok')) {
      addToast(`Успешно: ${name}`, 'ok');
      // После users-sync обновляем справочник для редактора соответствий
      if (endpoint.includes('users-sync')) {
        const amoRes = await api('/amocrm/users');
        if (amoRes && Array.isArray(amoRes.users)) {
          setAmoUsers(amoRes.users);
        }
      }
    } else {
      const detail = res?.detail || res?.error || 'Не удалось выполнить';
      addToast(`Ошибка (${name}): ${detail}`, 'err');
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
    setProviderConfig((prev) => {
      const updated = {
        ...prev,
        [section]: {
          ...(prev[section] || {}),
          [field]: value,
        },
      };
      setRawJsonText(JSON.stringify(updated, null, 2));
      return updated;
    });
  };

  const updateBasic = (field, value) => {
    setBasicSettings((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  if (loading) return <div className="p-8 text-center text-muted animate-pulse">Загрузка системных настроек...</div>;

  const megafon = providerConfig.megafon_vats || {};
  const multicom = providerConfig.multicom || {};
  const amocrm = providerConfig.amocrm || {};
  const uis = providerConfig.uis || {};
  const ami = providerConfig.ami || {};
  const llm = providerConfig.llm || {};
  const crm = providerConfig.crm || {};
  const bitrix24 = providerConfig.bitrix24 || {};

  // --- Соответствие операторов ATS → пользователей amoCRM (operator_user_map) ---
  const atsOpKey = (u) => String(u.op_vats || u.ext || u.login || '').trim();
  const amoOpMap = (amocrm.operator_user_map && typeof amocrm.operator_user_map === 'object' && !Array.isArray(amocrm.operator_user_map))
    ? amocrm.operator_user_map
    : {};
  const amoOpMapEntries = Object.entries(amoOpMap);
  const availableOps = users.filter((u) => {
    const k = atsOpKey(u);
    return k && !(k in amoOpMap);
  });
  const handleAddOpMap = () => {
    if (!newOpKey || !newAmoId) {
      return addToast('Выберите оператора ATS и пользователя amoCRM', 'warn');
    }
    updateProvider('amocrm', 'operator_user_map', { ...amoOpMap, [newOpKey]: parseInt(newAmoId, 10) });
    setNewOpKey('');
    setNewAmoId('');
  };
  const handleDelOpMap = (k) => {
    const cur = { ...amoOpMap };
    delete cur[k];
    updateProvider('amocrm', 'operator_user_map', cur);
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200 pb-12">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Системные настройки и Провайдеры</h1>
          <p className="text-xs text-muted">Полная конфигурация телефонии, лимитов, операторов и интеграций</p>
        </div>
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
              <option value="multicom">Мультиком (MultiCom API / SIP)</option>
              <option value="uis">UIS / Comagic API</option>
              <option value="ami">Asterisk AMI Manager</option>
              <option value="sim">Симуляция (Стенд)</option>
            </select>
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Макс. одновременных каналов</label>
            <input
              type="number"
              min="1"
              max="60"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.max_channels || 3}
              onChange={(e) => updateBasic('max_channels', parseInt(e.target.value) || 1)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Макс. попыток автодозвона</label>
            <input
              type="number"
              min="1"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.retry_max || 2}
              onChange={(e) => updateBasic('retry_max', parseInt(e.target.value) || 1)}
            />
          </div>

          <div>
            <label className="text-muted block mb-1 font-semibold">Интервал повтора (мин)</label>
            <input
              type="number"
              min="1"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.retry_delay_min || 15}
              onChange={(e) => updateBasic('retry_delay_min', parseInt(e.target.value) || 1)}
            />
          </div>

          <div>
            <label className="text-muted block mb-1 font-semibold">Остывание номера после звонка (сек)</label>
            <input
              type="number"
              min="0"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.line_cooldown_sec ?? 5}
              onChange={(e) => updateBasic('line_cooldown_sec', parseInt(e.target.value) || 0)}
            />
          </div>

          <div>
            <label className="text-muted block mb-1 font-semibold">Watchdog «зависших» звонков (мин)</label>
            <input
              type="number"
              min="1"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.watchdog_timeout_min || 20}
              onChange={(e) => updateBasic('watchdog_timeout_min', parseInt(e.target.value) || 1)}
            />
          </div>

          <div>
            <label className="text-muted block mb-1 font-semibold">Таймаут ожидания оператора (сек)</label>
            <input
              type="number"
              min="5"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.acd_wait_timeout_sec || 60}
              onChange={(e) => updateBasic('acd_wait_timeout_sec', parseInt(e.target.value) || 5)}
            />
          </div>

          <div>
            <label className="text-muted block mb-1 font-semibold">Страховка VATS-разговора (мин)</label>
            <input
              type="number"
              min="0"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.vats_conversation_timeout_min || 30}
              onChange={(e) => updateBasic('vats_conversation_timeout_min', parseInt(e.target.value) || 0)}
            />
          </div>

          <div>
            <label className="text-muted block mb-1 font-semibold">Жалоб до автокарантина номера</label>
            <input
              type="number"
              min="1"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.auto_quarantine_on_complaints || 3}
              onChange={(e) => updateBasic('auto_quarantine_on_complaints', parseInt(e.target.value) || 1)}
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

          <div className="flex items-center gap-2 pt-6">
            <input
              type="checkbox"
              id="cConsentReq"
              checked={basicSettings.consent_required !== false}
              onChange={(e) => updateBasic('consent_required', e.target.checked)}
              className="accent-acc w-4 h-4 cursor-pointer"
            />
            <label htmlFor="cConsentReq" className="text-white font-medium cursor-pointer">
              Требовать согласие контакта на обзвон (152-ФЗ)
            </label>
          </div>

          <div className="flex items-center gap-2 pt-6">
            <input
              type="checkbox"
              id="cLogNonCamp"
              checked={basicSettings.log_non_campaign_calls !== false}
              onChange={(e) => updateBasic('log_non_campaign_calls', e.target.checked)}
              className="accent-acc w-4 h-4 cursor-pointer"
            />
            <label htmlFor="cLogNonCamp" className="text-white font-medium cursor-pointer">
              Фиксировать фоновые звонки ВАТС (без кампании #0)
            </label>
          </div>
        </div>
      </div>

      {/* SECTION 3: Megafon VATS Settings */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Shield className="w-5 h-5 text-ok" /> Интеграция МегаФон ВАТС (REST CRM API)
          </h2>
          <div className="flex items-center gap-2 flex-wrap">
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
            <button
              type="button"
              onClick={() => runMegafonAction('/megafon/groups-sync', 'Синхронизация отделов')}
              className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5 text-warn" /> Синк отделов
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
              onFocus={(e) => {
                if (isMaskedValue(e.target.value)) {
                  updateProvider('megafon_vats', 'api_key', '');
                }
              }}
              onChange={(e) => updateProvider('megafon_vats', 'api_key', e.target.value)}
              placeholder={isMaskedValue(megafon.api_key) ? 'Сохранён (нажмите, чтобы изменить)' : 'Ключ авторизации ВАТС'}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">CRM Token (для входящих вебхуков)</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.crm_token || ''}
              onFocus={(e) => {
                if (isMaskedValue(e.target.value)) {
                  updateProvider('megafon_vats', 'crm_token', '');
                }
              }}
              onChange={(e) => updateProvider('megafon_vats', 'crm_token', e.target.value)}
              placeholder={isMaskedValue(megafon.crm_token) ? 'Сохранён (нажмите, чтобы изменить)' : 'Токен CRM'}
            />
          </div>
        </div>
      </div>

      {/* SECTION 4: Multicom Aggregator Settings */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Radio className="w-5 h-5 text-cy" /> Интеграция с Агрегатором Мультиком (MultiCom API / SIP)
          </h2>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => runMulticomAction('/multicom/check', 'Проверка связи с Мультиком')}
              className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
            >
              <CheckCircle className="w-3.5 h-3.5 text-ok" /> Проверить связь
            </button>
            <button
              type="button"
              onClick={() => runMulticomAction('/multicom/pool-sync', 'Синхронизация номеров')}
              className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5 text-acc2" /> Синк номеров
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1 font-semibold">API URL</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={multicom.api_url || ''}
              onChange={(e) => updateProvider('multicom', 'api_url', e.target.value)}
              placeholder="https://api.multicom.ru/v1"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">API Key / Token</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={multicom.api_key || ''}
              onFocus={(e) => {
                if (isMaskedValue(e.target.value)) {
                  updateProvider('multicom', 'api_key', '');
                }
              }}
              onChange={(e) => updateProvider('multicom', 'api_key', e.target.value)}
              placeholder={isMaskedValue(multicom.api_key) ? 'Сохранён (нажмите, чтобы изменить)' : 'Ключ API Мультиком'}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">ID Аккаунта / Лицевой счет</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={multicom.account_id || ''}
              onChange={(e) => updateProvider('multicom', 'account_id', e.target.value)}
              placeholder="ID аккаунта Мультиком"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">SIP Сервер (sip_host)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={multicom.sip_host || ''}
              onChange={(e) => updateProvider('multicom', 'sip_host', e.target.value)}
              placeholder="sip.multicom.ru"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">SIP Логин</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={multicom.sip_user || ''}
              onChange={(e) => updateProvider('multicom', 'sip_user', e.target.value)}
              placeholder="Логин SIP аккаунта"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">SIP Пароль</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={multicom.sip_secret || ''}
              onFocus={(e) => {
                if (isMaskedValue(e.target.value)) {
                  updateProvider('multicom', 'sip_secret', '');
                }
              }}
              onChange={(e) => updateProvider('multicom', 'sip_secret', e.target.value)}
              placeholder={isMaskedValue(multicom.sip_secret) ? 'Сохранён (нажмите, чтобы изменить)' : 'Пароль SIP'}
            />
          </div>
        </div>
      </div>

      {/* SECTION 4: UIS / Comagic API */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <Server className="w-5 h-5 text-acc2" /> Настройки UIS / Comagic API
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1 font-semibold">API URL</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={uis.api_url || ''}
              onChange={(e) => updateProvider('uis', 'api_url', e.target.value)}
              placeholder="https://api.uiscom.ru/v1.0"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">API Key</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={uis.api_key || ''}
              onChange={(e) => updateProvider('uis', 'api_key', e.target.value)}
              placeholder="Ключ API UIS"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Number Pool API URL / Key</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={uis.number_pool_api || ''}
              onChange={(e) => updateProvider('uis', 'number_pool_api', e.target.value)}
              placeholder="URL сервиса пула"
            />
          </div>
        </div>
      </div>

      {/* SECTION 5: Asterisk AMI Manager */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <Cpu className="w-5 h-5 text-cy" /> Настройки Asterisk AMI Manager
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1 font-semibold">Хост (IP / Host)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={ami.host || '127.0.0.1'}
              onChange={(e) => updateProvider('ami', 'host', e.target.value)}
              placeholder="127.0.0.1"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">Порт AMI</label>
            <input
              type="number"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={ami.port || 5038}
              onChange={(e) => updateProvider('ami', 'port', parseInt(e.target.value) || 5038)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">AMI Пользователь</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={ami.user || ''}
              onChange={(e) => updateProvider('ami', 'user', e.target.value)}
              placeholder="admin"
            />
          </div>
          <div>
            <label className="text-muted block mb-1 font-semibold">AMI Пароль (Secret)</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={ami.secret || ''}
              onChange={(e) => updateProvider('ami', 'secret', e.target.value)}
              placeholder="••••••••"
            />
          </div>
        </div>
      </div>

      {/* SECTION 6: LLM & CRM Settings */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* LLM Agent */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Bot className="w-5 h-5 text-acc2" /> ИИ-Агент / LLM Сценарии
          </h2>
          <div className="flex flex-col gap-3 text-xs">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="llmEnabled"
                checked={!!llm.enabled}
                onChange={(e) => updateProvider('llm', 'enabled', e.target.checked)}
                className="accent-acc w-4 h-4 cursor-pointer"
              />
              <label htmlFor="llmEnabled" className="text-white font-medium cursor-pointer">
                Включить ИИ-агента для голосовых сценариев
              </label>
            </div>
            <div>
              <label className="text-muted block mb-1 font-semibold">Base URL API ИИ</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={llm.base_url || ''}
                onChange={(e) => updateProvider('llm', 'base_url', e.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </div>
            <div>
              <label className="text-muted block mb-1 font-semibold">Модель ИИ</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={llm.model || ''}
                onChange={(e) => updateProvider('llm', 'model', e.target.value)}
                placeholder="gpt-4o-mini"
              />
            </div>
            <div>
              <label className="text-muted block mb-1 font-semibold">Переменная API Key</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={llm.api_key_env || 'ATS_LLM_KEY'}
                onChange={(e) => updateProvider('llm', 'api_key_env', e.target.value)}
                placeholder="ATS_LLM_KEY"
              />
            </div>
          </div>
        </div>

        {/* CRM Integration */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <Link className="w-5 h-5 text-ok" /> Интеграция с CRM
            </h2>
            {crm.driver === 'amocrm' && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => runAmoCrmAction('/amocrm/check', 'Проверка связи с amoCRM')}
                  className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
                >
                  <CheckCircle className="w-3.5 h-3.5 text-ok" /> Проверить связь
                </button>
                <button
                  type="button"
                  onClick={() => runAmoCrmAction('/amocrm/users-sync', 'Синхронизация менеджеров')}
                  className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
                >
                  <Users className="w-3.5 h-3.5 text-acc" /> Синхронизировать пользователей
                </button>
              </div>
            )}
          </div>
          <div className="flex flex-col gap-3 text-xs">
            <div>
              <label className="text-muted block mb-1 font-semibold">Драйвер выгрузки</label>
              <select
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                value={crm.driver || 'csv'}
                onChange={(e) => updateProvider('crm', 'driver', e.target.value)}
              >
                <option value="csv">CSV — Файловая выгрузка</option>
                <option value="amocrm">amoCRM — REST API v4</option>
                <option value="bitrix24">Bitrix24 — Вебхук REST API</option>
              </select>
            </div>

            {crm.driver === 'amocrm' && (
              <>
                <div>
                  <label className="text-muted block mb-1 font-semibold">Субдомен amoCRM (из URL: company.amocrm.ru)</label>
                  <input
                    className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                    value={amocrm.subdomain || ''}
                    onChange={(e) => updateProvider('amocrm', 'subdomain', e.target.value)}
                    placeholder="mycompany"
                  />
                </div>
                <div>
                  <label className="text-muted block mb-1 font-semibold">Долгосрочный токен доступа (access_token)</label>
                  <input
                    type="password"
                    className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                    value={amocrm.access_token || ''}
                    onFocus={(e) => {
                      if (isMaskedValue(e.target.value)) {
                        updateProvider('amocrm', 'access_token', '');
                      }
                    }}
                    onChange={(e) => updateProvider('amocrm', 'access_token', e.target.value)}
                    placeholder={isMaskedValue(amocrm.access_token) ? 'Сохранён (нажмите, чтобы изменить)' : 'Bearer токен amoCRM'}
                  />
                </div>
                <div>
                  <label className="text-muted block mb-1 font-semibold">ID Ответственного пользователя в amoCRM</label>
                  <input
                    type="number"
                    className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                    value={amocrm.responsible_user_id || 1}
                    onChange={(e) => updateProvider('amocrm', 'responsible_user_id', parseInt(e.target.value) || 1)}
                    placeholder="1"
                  />
                </div>
                <div className="flex flex-col gap-2 pt-2 border-t border-line2/40">
                  <label className="text-muted font-semibold">Соответствие операторов ATS → пользователей amoCRM</label>
                  {amoUsers.length === 0 ? (
                    <p className="text-warn text-xs">Список пользователей amoCRM пуст — сначала нажмите «Синхронизировать пользователей».</p>
                  ) : (
                    <>
                      {amoOpMapEntries.length === 0 ? (
                        <p className="text-dim text-xs">Соответствий пока нет — добавьте ниже.</p>
                      ) : (
                        <div className="flex flex-col gap-1">
                          {amoOpMapEntries.map(([atsKey, amoId]) => {
                            const au = amoUsers.find((x) => String(x.id) === String(amoId));
                            return (
                              <div key={atsKey} className="flex items-center justify-between bg-[#0a1628] border border-line2 rounded-xl px-3 py-1.5">
                                <span className="text-white text-xs font-mono truncate">
                                  {atsKey} <span className="text-muted">→</span>{' '}
                                  {au ? `${au.name || 'Без имени'}${au.email ? ` (${au.email})` : ''} [${au.id}]` : `id ${amoId}`}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => handleDelOpMap(atsKey)}
                                  className="p-1 rounded-lg text-muted hover:text-bad shrink-0"
                                  title="Удалить соответствие"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      )}
                      <div className="flex gap-2 flex-wrap">
                        <select
                          className="flex-1 min-w-[140px] bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                          value={newOpKey}
                          onChange={(e) => setNewOpKey(e.target.value)}
                        >
                          <option value="">Оператор ATS…</option>
                          {availableOps.map((u) => {
                            const k = atsOpKey(u);
                            return (
                              <option key={u.id} value={k}>
                                {u.op_name || u.login} ({k})
                              </option>
                            );
                          })}
                        </select>
                        <select
                          className="flex-1 min-w-[140px] bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                          value={newAmoId}
                          onChange={(e) => setNewAmoId(e.target.value)}
                        >
                          <option value="">Пользователь amoCRM…</option>
                          {amoUsers.map((au) => (
                            <option key={au.id} value={au.id}>
                              {au.name || 'Без имени'}{au.email ? ` (${au.email})` : ''} [{au.id}]
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={handleAddOpMap}
                          className="px-4 py-2 rounded-xl bg-acc text-white font-semibold text-xs hover:brightness-110 whitespace-nowrap"
                        >
                          Добавить
                        </button>
                      </div>
                      <p className="text-dim text-[11px]">Звонки и задачи операторов будут регистрироваться в amoCRM на выбранных сотрудников. Сохранение — общей кнопкой «Сохранить все настройки».</p>
                    </>
                  )}
                </div>
                <div className="flex flex-col gap-2 pt-2 border-t border-line2/40">
                  <label className="flex items-center gap-2 text-white font-medium cursor-pointer">
                    <input
                      type="checkbox"
                      checked={amocrm.auto_create_contacts !== false}
                      onChange={(e) => updateProvider('amocrm', 'auto_create_contacts', e.target.checked)}
                      className="accent-acc w-4 h-4 rounded cursor-pointer"
                    />
                    Автоматически создавать Контакт в amoCRM для новых номеров
                  </label>
                  <label className="flex items-center gap-2 text-white font-medium cursor-pointer">
                    <input
                      type="checkbox"
                      checked={amocrm.auto_create_tasks !== false}
                      onChange={(e) => updateProvider('amocrm', 'auto_create_tasks', e.target.checked)}
                      className="accent-acc w-4 h-4 rounded cursor-pointer"
                    />
                    Автоматически создавать Задачу на перезвон при пропущенных звонках
                  </label>
                </div>
              </>
            )}

            {crm.driver === 'bitrix24' && (
              <div>
                <label className="text-muted block mb-1 font-semibold">Webhook URL Bitrix24</label>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                  value={bitrix24.webhook_url || ''}
                  onChange={(e) => updateProvider('bitrix24', 'webhook_url', e.target.value)}
                  placeholder="https://yourportal.bitrix24.ru/rest/1/webhookkey/"
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* SECTION 7: Raw JSON Configuration */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Code className="w-5 h-5 text-warn" /> Расширенная конфигурация провайдеров (JSON)
          </h2>
          <button
            type="button"
            onClick={() => setShowRawEditor(!showRawEditor)}
            className="px-3 py-1.5 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold"
          >
            {showRawEditor ? 'Скрыть JSON редактор' : 'Показать сырой JSON'}
          </button>
        </div>

        {showRawEditor && (
          <div className="flex flex-col gap-2 animate-in fade-in duration-150">
            <p className="text-xs text-muted">
              Прямое редактирование параметров провайдеров. Секретные ключи показаны как ********.
            </p>
            <textarea
              className="w-full h-56 bg-[#070e1a] border border-line2 rounded-xl p-3 text-xs font-mono text-acc2 outline-none focus:border-acc"
              value={rawJsonText}
              onChange={(e) => setRawJsonText(e.target.value)}
            />
          </div>
        )}
      </div>

      {/* SECTION 8: Security & Password Change */}
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
