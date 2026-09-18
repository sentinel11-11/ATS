import React, { useState, useEffect } from 'react';
import { Play, PhoneCall, CheckCircle, Users, Radio, Shield, ListFilter, ArrowRight } from 'lucide-react';
import { api } from '../api';

export default function Dashboard({ onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    async function fetchDashboard() {
      const res = await api('/dashboard');
      if (isMounted && res && !res.error) {
        setData(res);
        setLoading(false);
      }
    }

    fetchDashboard();
    const interval = setInterval(fetchDashboard, 3000);
    return () => {
      isMounted = false;
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
  const activeChannels = Array.isArray(data?.active_channels) ? data.active_channels : [];
  const numbersState = data?.numbers || { active_count: 0, total_count: 0 };
  const operatorsList = Array.isArray(data?.operators) ? data.operators : [];
  const freeOperators = operatorsList.filter((o) => o.status === 'free').length;
  const acdQueued = data?.acd_queued || 0;

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      {/* Metric KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div
          onClick={() => onNavigate && onNavigate('campaigns')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer hover:border-acc/50 transition-all group"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Активные кампании</span>
          <b className="text-3xl text-white font-bold">{campaignsRunning}</b>
          <span className="text-[11px] text-dim block mt-1 group-hover:text-acc2 transition-colors">
            Перейти к кампаниям →
          </span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-acc/10 text-acc2">
            <Play className="w-5 h-5 fill-acc2" />
          </div>
        </div>

        <div
          onClick={() => onNavigate && onNavigate('journal')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer hover:border-ok/50 transition-all group"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Звонков за сегодня</span>
          <b className="text-3xl text-white font-bold">{callsToday}</b>
          <span className="text-[11px] text-ok font-semibold block mt-1">Успешных: {okToday}</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-ok/10 text-ok">
            <PhoneCall className="w-5 h-5" />
          </div>
        </div>

        <div
          onClick={() => onNavigate && onNavigate('numbers')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer hover:border-cy/50 transition-all group"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Пул номеров Caller ID</span>
          <b className="text-3xl text-white font-bold">{numbersState.active_count || 0}</b>
          <span className="text-[11px] text-dim block mt-1">Всего в пуле: {numbersState.total_count || 0}</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-cy/10 text-cy">
            <Shield className="w-5 h-5" />
          </div>
        </div>

        <div
          onClick={() => onNavigate && onNavigate('acd')}
          className="glass-card p-5 rounded-2xl relative overflow-hidden cursor-pointer hover:border-acc2/50 transition-all group"
        >
          <span className="text-xs font-semibold text-muted block mb-1">Операторы онлайн</span>
          <b className="text-3xl text-white font-bold">
            {freeOperators} / {operatorsList.length}
          </b>
          <span className="text-[11px] text-dim block mt-1">В очереди ACD: {acdQueued}</span>
          <div className="absolute top-4 right-4 p-2.5 rounded-xl bg-acc2/10 text-acc2">
            <Users className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Real-time Active Channels Feed */}
      <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between border-b border-line pb-3">
          <div className="flex items-center gap-2">
            <Radio className="w-5 h-5 text-acc2 animate-pulse" />
            <h2 className="text-base font-bold text-white">Текущие активные звонки</h2>
          </div>
          <span className="text-xs text-muted">Обновляется в реальном времени</span>
        </div>

        {activeChannels.length === 0 ? (
          <div className="py-8 text-center text-dim text-xs">
            Сейчас нет активных вызовов. Ожидание запуска автодозвона.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-line text-muted uppercase text-[10px]">
                  <th className="py-2 px-3"># ID</th>
                  <th className="py-2 px-3">Клиент</th>
                  <th className="py-2 px-3">С номера</th>
                  <th className="py-2 px-3">Кампания</th>
                  <th className="py-2 px-3">Статус</th>
                  <th className="py-2 px-3">Время</th>
                </tr>
              </thead>
              <tbody>
                {activeChannels.map((c) => (
                  <tr key={c.id || c.call_id} className="border-b border-line/40 hover:bg-white/5">
                    <td className="py-2.5 px-3 font-mono text-dim">#{c.id || c.call_id}</td>
                    <td className="py-2.5 px-3 font-semibold text-white">{c.phone || c.client_phone}</td>
                    <td className="py-2.5 px-3 font-mono text-muted">{c.caller_id || c.clid || '—'}</td>
                    <td className="py-2.5 px-3 text-muted">#{c.campaign_id || 0}</td>
                    <td className="py-2.5 px-3">
                      <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-acc/20 text-acc2">
                        {c.status || 'dialing'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono text-dim">{c.duration_sec ? `${c.duration_sec}с` : '0 с'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
