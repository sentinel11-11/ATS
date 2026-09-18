import React, { useState, useEffect } from 'react';
import { Clock, Phone, Radio, Shield } from 'lucide-react';
import { api } from '../api';
import Modal from './Modal';

export default function TimelineModal({ callId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!callId) return;
    setLoading(true);
    async function load() {
      const res = await api(`/calls/${callId}/timeline`);
      if (res && res.call) {
        setData(res);
      }
      setLoading(false);
    }
    load();
  }, [callId]);

  if (!callId) return null;

  const { call = {}, events = [], acd = [] } = data || {};

  return (
    <Modal isOpen={!!callId} onClose={onClose} title={`Таймлайн и Хронология ВАТС: Звонок #${callId}`} wide={true}>
      {loading ? (
        <div className="py-8 text-center text-muted animate-pulse">Загрузка хронологии ВАТС...</div>
      ) : !data ? (
        <div className="py-8 text-center text-bad">Не удалось загрузить данные звонка</div>
      ) : (
        <div className="flex flex-col gap-4 text-xs">
          {/* Call Summary */}
          <div className="p-4 rounded-xl bg-[#081221] border border-line grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <span className="text-dim block">Клиент</span>
              <span className="font-bold text-white">{call.contact_name || call.contact_phone}</span>
            </div>
            <div>
              <span className="text-dim block">Исходящий номер</span>
              <span className="font-mono text-white">{call.caller_id || '—'}</span>
            </div>
            <div>
              <span className="text-dim block">Результат</span>
              <span className="font-semibold text-acc2">{call.result || call.status}</span>
            </div>
            <div>
              <span className="text-dim block">Длительность</span>
              <span className="font-semibold text-white">{call.duration_sec ? `${Math.round(call.duration_sec)} с` : '—'}</span>
            </div>
          </div>

          {/* Timeline Events List */}
          <div className="flex flex-col gap-2">
            <span className="font-bold text-white text-sm mb-1">События ВАТС и Вектор вебхуков ({events.length})</span>
            {events.length === 0 ? (
              <div className="p-4 rounded-xl bg-[#081221] text-dim text-center">
                Событий ВАТС по этому звонку не зафиксировано
              </div>
            ) : (
              events.map((ev, idx) => (
                <div key={idx} className="p-3 rounded-xl bg-[#081221] border border-line flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="p-1.5 rounded-lg bg-acc/20 text-acc2">
                      <Radio className="w-4 h-4" />
                    </span>
                    <div>
                      <span className="font-bold text-white block">{ev.event_type || 'event'}</span>
                      <span className="text-[11px] text-dim">
                        Fingerprint: {ev.fingerprint?.slice(0, 16)}...
                      </span>
                    </div>
                  </div>
                  <span className="font-mono text-dim">{ev.received_at?.replace('T', ' ').slice(0, 19)}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
