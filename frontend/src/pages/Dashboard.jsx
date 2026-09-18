import React, { useState, useEffect } from 'react';
import { PlayCircle, PhoneCall, Headphones, CheckCircle2, Radio, Users } from 'lucide-react';
import { api } from '../api';

export default function Dashboard({ onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function load() {
      const res = await api('/dashboard');
      if (mounted && res && !res.error) {
        setData(res);
        setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 3000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  if (loading && !data) {
    return <div className="p-8 text-center text-muted animate-pulse">Загрузка сводки дашборда...</div>;
  }

  // Parse response keys from ENGINE.dashboard()
  const campaignsRunning = data?.campaigns_running || 0;
  const callsToday = data?.calls_today || 0;
  const okToday = data?.ok_today || 0;
  const activeChannels = data?.active_channels || [];
  const numbersState = data?.numbers || { active_count: 0, total_count: 0 };
  const operatorsList = data?.operators || [];
  const freeOperators = operatorsList.filter((o) => o.status === 'free').length;
  const acdQueued = data?.acd_queued || 0;

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      {/* Metric KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div
          onClick={() => onNavigate && onNavigate('campaigns')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Активные кампании</span>
          <b className="text-3xl text-white font-bold">{campaignsRunning}</b>
          <span className="text-[11px] text-dim block mt-1">Перейти к кампаниям →</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-acc/10 text-acc2">
            <PlayCircle className="w-5 h-5" />
          </div>
        </div>

        <div
          onClick={() => onNavigate && onNavigate('journal')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Звонков за сегодня</span>
          <b className="text-3xl text-white font-bold">{callsToday}</b>
          <span className="text-[11px] text-ok block mt-1">Успешных: {okToday}</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-ok/10 text-ok">
            <CheckCircle2 className="w-5 h-5" />
          </div>
        </div>

        <div
          onClick={() => onNavigate && onNavigate('numbers')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Пул номеров Caller ID</span>
          <b className="text-3xl text-white font-bold">{numbersState.active_count || 0}</b>
          <span className="text-[11px] text-dim block mt-1">Всего в пуле: {numbersState.total_count || 0}</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-cy/10 text-cy">
            <PhoneCall className="w-5 h-5" />
          </div>
        </div>

        <div
          onClick={() => onNavigate && onNavigate('acd')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Операторы онлайн</span>
          <b className="text-3xl text-white font-bold">{freeOperators} / {operatorsList.length}</b>
          <span className="text-[11px] text-dim block mt-1">В очереди ACD: {acdQueued}</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-warn/10 text-warn">
            <Headphones className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Active Calls Stream */}
      <div className="glass-panel p-6 rounded-2xl">
        <h2 className="text-base font-bold text-white flex items-center justify-between mb-4">
          <span className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-acc2" /> Текущие активные звонки
          </span>
          <span className="text-xs font-normal text-dim">Обновляется в реальном времени</span>
        </h2>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line text-muted uppercase text-[10px]">
                <th className="py-2.5 px-3"># ID</th>
                <th className="py-2.5 px-3">Клиент</th>
                <th className="py-2.5 px-3">С номера</th>
                <th className="py-2.5 px-3">Кампания</th>
                <th className="py-2.5 px-3">Статус</th>
                <th className="py-2.5 px-3">Время</th>
              </tr>
            </thead>
            <tbody>
              {activeChannels.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-dim">
                    Сейчас нет активных вызовов. Ожидание запуска автодозвона.
                  </td>
                </tr>
              ) : (
                activeChannels.map((c) => (
                  <tr key={c.id || c.call_id} className="border-b border-line/50 hover:bg-white/5 transition-colors">
                    <td className="py-3 px-3 font-mono text-dim">#{c.id || c.call_id}</td>
                    <td className="py-3 px-3 font-semibold text-white">
                      {c.contact_name || c.contact_phone || c.phone}
                      <span className="block text-[10px] text-dim font-normal">{c.contact_phone || c.phone}</span>
                    </td>
                    <td className="py-3 px-3 text-muted">{c.caller_id || '—'}</td>
                    <td className="py-3 px-3 text-muted">#{c.campaign_id || 0}</td>
                    <td className="py-3 px-3">
                      <span className="px-2.5 py-1 rounded-full bg-acc/20 text-acc2 font-semibold text-[11px] pulse-active inline-flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-acc"></span>
                        {c.status}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-dim">{c.started_at?.slice(11, 19) || '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
