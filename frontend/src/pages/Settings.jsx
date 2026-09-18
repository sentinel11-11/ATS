import React, { useState, useEffect } from 'react';
import { Settings as SettingsIcon, Save, CheckCircle } from 'lucide-react';
import { api } from '../api';

export default function Settings({ addToast }) {
  const [settings, setSettings] = useState({});
  const [loading, setLoading] = useState(true);

  const loadSettings = async () => {
    setLoading(true);
    const res = await api('/settings');
    if (res && res.settings) {
      setSettings(res.settings);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleSave = async () => {
    const res = await api('/settings', { body: settings });
    if (res && res.ok) {
      addToast('Настройки сохранены', 'ok');
    } else {
      addToast('Ошибка сохранения настроек', 'err');
    }
  };

  const updateNested = (category, field, value) => {
    setSettings((prev) => ({
      ...prev,
      [category]: {
        ...(prev[category] || {}),
        [field]: value,
      },
    }));
  };

  if (loading) return <div className="p-8 text-center text-muted">Загрузка настроек...</div>;

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

      {/* Provider Megafon */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <h2 className="text-base font-bold text-white">Интеграция: МегаФон ВАТС</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="text-muted block mb-1">Base URL (Панель ВАТС REST API)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={settings.megafon_vats?.base_url || ''}
              onChange={(e) => updateNested('megafon_vats', 'base_url', e.target.value)}
              placeholder="https://vatsXXXXXX.megapbx.ru/crmapi/v1"
            />
          </div>
          <div>
            <label className="text-muted block mb-1">Оператор по умолчанию (default_user)</label>
            <input
              className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc"
              value={settings.megafon_vats?.default_user || ''}
              onChange={(e) => updateNested('megafon_vats', 'default_user', e.target.value)}
              placeholder="admin"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
