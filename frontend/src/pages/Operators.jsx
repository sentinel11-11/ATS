import React, { useState, useEffect } from 'react';
import { Headphones, Users, CheckCircle, Clock } from 'lucide-react';
import { api } from '../api';

export default function Operators({ addToast }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadACD = async () => {
    setLoading(true);
    const res = await api('/acd');
    if (res) {
      setData(res);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadACD();
  }, []);

  const { operators = [], queue = [] } = data || {};

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Операторы и ACD Очередь</h1>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Operators */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Users className="w-5 h-5 text-acc2" /> Операторы
          </h2>
          <div className="flex flex-col gap-2">
            {operators.length === 0 ? (
              <span className="text-xs text-dim py-4">Операторов нет.</span>
            ) : (
              operators.map((op) => (
                <div key={op.id} className="p-3 rounded-xl bg-[#081221] border border-line flex items-center justify-between">
                  <div>
                    <span className="font-semibold text-xs text-white block">{op.login}</span>
                    <span className="text-[10px] text-dim">{op.role === 'admin' ? 'Администратор' : 'Оператор'}</span>
                  </div>
                  <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                    op.status === 'free' ? 'bg-ok/20 text-ok' : op.status === 'break' ? 'bg-warn/20 text-warn' : 'bg-line text-muted'
                  }`}>
                    {op.status === 'free' ? 'Свободен' : op.status === 'break' ? 'Перерыв' : 'Занят'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ACD Queue */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Headphones className="w-5 h-5 text-warn" /> Очередь ожидающих звонков ({queue.length})
          </h2>
          <div className="flex flex-col gap-2">
            {queue.length === 0 ? (
              <span className="text-xs text-dim py-4">Очередь вызовов пуста.</span>
            ) : (
              queue.map((item) => (
                <div key={item.id} className="p-3 rounded-xl bg-[#081221] border border-line flex items-center justify-between">
                  <div>
                    <span className="font-semibold text-xs text-white block">{item.contact_phone}</span>
                    <span className="text-[10px] text-dim">Кампания #{item.campaign_id}</span>
                  </div>
                  <button className="px-3 py-1 rounded-lg bg-ok/20 hover:bg-ok/30 text-ok font-semibold text-xs">
                    Принять
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
