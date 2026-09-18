import React, { useState, useEffect } from 'react';
import { Save, Shield, PhoneCall, Cpu, RefreshCw, Key, CheckCircle, Database } from 'lucide-react';
import { api } from '../api';

export default function Settings({ addToast }) {
  const [loading, setLoading] = useState(true);
  const [providerConfig, setProviderConfig] = useState({});
  const [basicSettings, setBasicSettings] = useState({});
  const [newPassword, setNewPassword] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const loadSettings = async () => {
    setLoading(true);
    const [rawRes, basicRes] = await Promise.all([
      api('/settings/raw'),
      api('/settings')
    ]);

    if (rawRes && rawRes.provider_config) {
      setProviderConfig(rawRes.provider_config);
    }
    if (basicRes && basicRes.settings) {
      setBasicSettings(basicRes.settings);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleSave = async () => {
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
      loadSettings();
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
  const llm = providerConfig.llm || {};

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200 pb-12">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Системные настройки и Провайдеры</h1>
        <button
          onClick={handleSave}
          disabled={actionLoading}
          className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all disabled:opacity-50"
        >
          <Save className="w-4 h-4" /> Сохранить все настройки
        </button>
      </div>

      {/* Basic Telephony & Limits */}
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

      {/* Megafon VATS Settings & Action Tools */}
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

      {/* UIS & Asterisk AMI */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* UIS */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-3">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Database className="w-5 h-5 text-cy" /> Провайдер UIS / Comagic
          </h2>
          <div className="flex flex-col gap-3 text-xs">
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
          </div>
        </div>

        {/* Asterisk AMI */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-3">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Cpu className="w-5 h-5 text-warn" /> Asterisk AMI Manager
          </h2>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <label className="text-muted block mb-1 font-semibold">Хост Asterisk</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={ami.host || '127.0.0.1'}
                onChange={(e) => updateProvider('ami', 'host', e.target.value)}
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
              <label className="text-muted block mb-1 font-semibold">Пользователь AMI</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={ami.user || ''}
                onChange={(e) => updateProvider('ami', 'user', e.target.value)}
              />
            </div>
            <div>
              <label className="text-muted block mb-1 font-semibold">Пароль AMI</label>
              <input
                type="password"
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
                value={ami.secret || ''}
                onChange={(e) => updateProvider('ami', 'secret', e.target.value)}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Security & Password Change */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <Key className="w-5 h-5 text-bad" /> Безопасность (Смена пароля администратора)
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
    </div>
  );
}
