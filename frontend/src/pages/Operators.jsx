import React, { useState, useEffect } from 'react';
import { Users, PhoneIncoming, Clock } from 'lucide-react';
import { api } from '../api';

export default function Operators({ addToast }) {
  const [operators, setOperators] = useState([]);
  const [queue, setQueue] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadAcdData = async () => {
    setLoading(true);
    const [opRes, qRes] = await Promise.all([
      api('/acd/operators'),
      api('/acd/queue'),
    ]);

    if (opRes && opRes.operators) setOperators(Array.isArray(opRes.operators) ? opRes.operators : []);
    if (qRes && qRes.queue) setQueue(Array.isArray(qRes.queue) ? qRes.queue : []);
    setLoading(false);
  };

  useEffect(() => {
    loadAcdData();
    const interval = setInterval(loadAcdData, 3000);
    return () => clearInterval(interval);
  }, []);

  const operatorsList = Array.isArray(operators) ? operators : [];
  const queueList = Array.isArray(queue) ? queue : [];

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <h1 className="text-xl font-bold text-white">Операторы и ACD Очередь</h1>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Operators */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Users className="w-5 h-5 text-acc2" /> Операторы ({operatorsList.length})
          </h2>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-line text-muted uppercase text-[10px]">
                  <th className="py-2.5 px-3">Оператор</th>
                  <th className="py-2.5 px-3">Внутр. №</th>
                  <th className="py-2.5 px-3">Статус</th>
                </tr>
              </thead>
              <tbody>
                {loading && operatorsList.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="py-4 text-center text-muted">Загрузка...</td>
                  </tr>
                ) : operatorsList.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="py-4 text-center text-dim">Операторов нет в ACD</td>
                  </tr>
                ) : (
                  operatorsList.map((op) => (
                    <tr key={op.id || op.login} className="border-b border-line/40">
                      <td className="py-2.5 px-3 font-semibold text-white">@{op.login} ({op.name || 'Оператор'})</td>
                      <td className="py-2.5 px-3 font-mono text-muted">{op.ext || '—'}</td>
                      <td className="py-2.5 px-3">
                        <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                          op.status === 'free' ? 'bg-ok/20 text-ok' : op.status === 'busy' ? 'bg-acc/20 text-acc2' : 'bg-warn/20 text-warn'
                        }`}>
                          {op.status === 'free' ? 'Свободен' : op.status === 'busy' ? 'В разговоре' : 'Перерыв'}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* ACD Queue */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-4">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <PhoneIncoming className="w-5 h-5 text-cy" /> Очередь ожидающих звонков ({queueList.length})
          </h2>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-line text-muted uppercase text-[10px]">
                  <th className="py-2.5 px-3"># ID</th>
                  <th className="py-2.5 px-3">Телефон</th>
                  <th className="py-2.5 px-3">Ожидание</th>
                </tr>
              </thead>
              <tbody>
                {queueList.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="py-6 text-center text-dim">Очередь вызовов пуста.</td>
                  </tr>
                ) : (
                  queueList.map((item) => (
                    <tr key={item.id} className="border-b border-line/40">
                      <td className="py-2.5 px-3 font-mono text-dim">#{item.id}</td>
                      <td className="py-2.5 px-3 font-mono font-semibold text-white">{item.phone}</td>
                      <td className="py-2.5 px-3 text-muted">{item.wait_sec ? `${item.wait_sec} с` : '0 с'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
