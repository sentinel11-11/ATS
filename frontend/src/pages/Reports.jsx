import React, { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import { api, getToken } from '../api';

export default function Reports({ addToast, refreshKey }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadReport = async () => {
    setLoading(true);
    const res = await api('/reports');
    if (res && !res.error) {
      setReport(res);
    } else if (res?.error) {
      addToast?.(`Не удалось загрузить отчёт: ${res.error}`, 'err');
    }
    setLoading(false);
  };

  useEffect(() => {
    loadReport();
  }, [refreshKey]);

  // API v2 returns the current period in `today`, not `metrics`.
  // Keep a compatibility fallback for older deployed builds.
  const metrics = report?.metrics || {
    total_attempts: report?.today?.n || 0,
    successful_calls: report?.today?.ok || 0,
    conversion_rate: `${report?.today?.ok_rate || 0}%`,
  };

  const handleExportCSV = () => {
    const token = getToken();
    window.open(`/api/v2/export/calls.csv?token=${encodeURIComponent(token)}`, '_blank');
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Отчёты и конверсия эффективности</h1>
          {report?.period?.label && (
            <p className="text-xs text-muted mt-1">Период: {report.period.label}</p>
          )}
        </div>
        <button
          onClick={handleExportCSV}
          className="px-4 py-2 rounded-xl bg-line/60 hover:bg-line font-semibold text-xs text-white flex items-center gap-1.5 transition-all"
        >
          <Download className="w-4 h-4" /> Экспорт звонков в CSV
        </button>
      </div>

      {loading ? (
        <div className="text-center py-12 text-muted">Загрузка аналитики...</div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="glass-panel p-5 rounded-2xl flex flex-col gap-2">
              <span className="text-xs text-muted">Всего попыток звонков</span>
              <b className="text-2xl text-white font-bold">{metrics.total_attempts || 0}</b>
            </div>
            <div className="glass-panel p-5 rounded-2xl flex flex-col gap-2">
              <span className="text-xs text-muted">Успешных соединений</span>
              <b className="text-2xl text-ok font-bold">{metrics.successful_calls || 0}</b>
            </div>
            <div className="glass-panel p-5 rounded-2xl flex flex-col gap-2">
              <span className="text-xs text-muted">Конверсия</span>
              <b className="text-2xl text-acc2 font-bold">{metrics.conversion_rate || '0%'}</b>
            </div>
          </div>

          {Array.isArray(report?.trend) && report.trend.length > 0 && (
            <div className="glass-panel p-6 rounded-2xl">
              <h2 className="text-base font-bold text-white mb-4">Динамика по дням</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-line text-muted uppercase text-[10px]">
                      <th className="py-2 px-3">Период</th>
                      <th className="py-2 px-3">Всего</th>
                      <th className="py-2 px-3">Успешных</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.trend.map((row) => (
                      <tr key={row.date} className="border-b border-line/40">
                        <td className="py-2.5 px-3 text-muted">{row.label || row.date}</td>
                        <td className="py-2.5 px-3 text-white">{row.n || 0}</td>
                        <td className="py-2.5 px-3 text-ok">{row.ok || 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
