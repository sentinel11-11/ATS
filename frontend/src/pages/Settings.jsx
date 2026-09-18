import React, { useState, useEffect } from 'react';
import { Save, Shield, PhoneCall } from 'lucide-react';
import { api } from '../api';

export default function Settings({ addToast }) {
  const [loading, setLoading] = useState(true);
  const [providerConfig, setProviderConfig] = useState({});
  const [basicSettings, setBasicSettings] = useState({});

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
    const [rawRes, basicRes] = await Promise.all([
      api('/settings/raw', { body: { provider_config: providerConfig } }),
      api('/settings/save', { body: basicSettings })
    ]);

    if ((rawRes && rawRes.ok) || (basicRes && basicRes.ok)) {
      addToast('Все настройки успешно сохранены', 'ok');
      loadSettings();
    } else {
      addToast('Ошибка сохранения настроек', 'err');
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

  if (loading) return <div className="p-8 text-center text-muted animate-pulse">Загрузка настроек...</div>;

  const megafon = providerConfig.megafon_vats || {};

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Системные настройки и Провайдеры</h1>
        <button
          onClick={handleSave}
          className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Save className="w-4 h-4" /> Сохранить настройки
        </button>
      </div>

      {/* Basic Telephony Settings */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <PhoneCall className="w-5 h-5 text-acc2" /> Основной провайдер и лимиты
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1">Провайдер телефонии</label>
            <select
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.provider || ''}
              onChange={(e) => updateBasic('provider', e.target.value)}
            >
              <option value="">Не выбран (Fail-closed)</option>
              <option value="megafon_vats">МегаФон ВАТС REST API</option>
              <option value="uis">UIS / Comagic</option>
              <option value="ami">Asterisk AMI</option>
              <option value="sim">Симуляция (Стенд)</option>
            </select>
          </div>
          <div>
            <label className="text-muted block mb-1">Макс. одновременных каналов</label>
            <input
              type="number"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.max_channels || 3}
              onChange={(e) => updateBasic('max_channels', parseInt(e.target.value) || 1)}
            />
          </div>
          <div>
            <label className="text-muted block mb-1">Макс. попыток автодозвона</label>
            <input
              type="number"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={basicSettings.retry_max || 2}
              onChange={(e) => updateBasic('retry_max', parseInt(e.target.value) || 1)}
            />
          </div>
        </div>
      </div>

      {/* Megafon VATS */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white flex items-center gap-2">
          <Shield className="w-5 h-5 text-ok" /> Интеграция: МегаФон ВАТС
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1">Base URL (Панель ВАТС REST API)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.base_url || ''}
              onChange={(e) => updateProvider('megafon_vats', 'base_url', e.target.value)}
              placeholder="https://vats326349.megapbx.ru/crmapi/v1"
            />
          </div>
          <div>
            <label className="text-muted block mb-1">Оператор по умолчанию (default_user)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.default_user || ''}
              onChange={(e) => updateProvider('megafon_vats', 'default_user', e.target.value)}
              placeholder="admin"
            />
          </div>
          <div>
            <label className="text-muted block mb-1">API Key (Авторизация в АТС)</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.api_key || ''}
              onChange={(e) => updateProvider('megafon_vats', 'api_key', e.target.value)}
              placeholder="••••••••"
            />
          </div>
          <div>
            <label className="text-muted block mb-1">CRM Token (для входящих вебхуков)</label>
            <input
              type="password"
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={megafon.crm_token || ''}
              onChange={(e) => updateProvider('megafon_vats', 'crm_token', e.target.value)}
              placeholder="••••••••"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
