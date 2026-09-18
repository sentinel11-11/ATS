import React, { useState, useEffect } from 'react';
import { BarChart3, Calendar, Download } from 'lucide-react';
import { api } from '../api';

export default function Reports({ addToast }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadReport = async () => {
    setLoading(true);
    const res = await api('/reports');
    if (res) {
      setReport(res);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadReport();
  }, []);

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Отчёты и конверсия эффективности</h1>
      </div>

      {loading ? (
        <div className="text-center py-12 text-muted">Загрузка аналитики...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="glass-panel p-5 rounded-2xl flex flex-col gap-2">
            <span className="text-xs text-muted">Всего попыток звонков</span>
            <b className="text-2xl text-white font-bold">{report?.metrics?.total_attempts || 0}</b>
          </div>
          <div className="glass-panel p-5 rounded-2xl flex flex-col gap-2">
            <span className="text-xs text-muted">Успешных соединений</span>
            <b className="text-2xl text-ok font-bold">{report?.metrics?.successful_calls || 0}</b>
          </div>
          <div className="glass-panel p-5 rounded-2xl flex flex-col gap-2">
            <span className="text-xs text-muted">Конверсия дожима</span>
            <b className="text-2xl text-acc2 font-bold">{report?.metrics?.conversion_rate || '0%'}</b>
          </div>
        </div>
      )}
    </div>
  );
}
