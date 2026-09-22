import React, { useEffect, useState } from 'react';
import { ClipboardList, ChevronDown, ChevronUp, Filter, RefreshCw } from 'lucide-react';
import { api } from '../api';

const PAGE_SIZE = 100;
const EVENT_LABELS = {
  auth: 'Авторизация',
  settings: 'Настройки',
  campaign: 'Кампании',
  contact: 'Контакты',
  blacklist: 'Чёрный список',
  operator: 'Операторы / ACD',
  integration: 'Интеграции',
  export: 'Экспорт',
};

function formatDate(value) {
  return value ? String(value).slice(0, 16).replace('T', ' ') : '—';
}

export default function AdminLogs({ addToast, refreshKey }) {
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState(null);
  const [filters, setFilters] = useState({
    q: '',
    event: '',
    status: '',
    from: '',
    to: '',
  });

  const loadLogs = async (requestedPage = page) => {
    setLoading(true);
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(requestedPage * PAGE_SIZE),
    });
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const res = await api(`/audit/logs?${params.toString()}`);
    if (res && !res.error) {
      setLogs(Array.isArray(res.logs) ? res.logs : []);
      setTotal(Number(res.total) || 0);
      setPage(Math.floor((Number(res.offset) || 0) / PAGE_SIZE));
    } else {
      addToast?.(`Не удалось загрузить логи: ${res?.error || '?'}`, 'err');
    }
    setLoading(false);
  };

  useEffect(() => {
    loadLogs(page);
  }, [refreshKey]);

  const updateFilter = (name, value) => {
    setFilters((previous) => ({ ...previous, [name]: value }));
  };

  const applyFilters = () => {
    setExpandedId(null);
    setPage(0);
    loadLogs(0);
  };

  const clearFilters = () => {
    const empty = { q: '', event: '', status: '', from: '', to: '' };
    setFilters(empty);
    setPage(0);
    // Use the cleared values immediately instead of waiting for React state.
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: '0' });
    api(`/audit/logs?${params.toString()}`).then((res) => {
      if (res && !res.error) {
        setLogs(Array.isArray(res.logs) ? res.logs : []);
        setTotal(Number(res.total) || 0);
      }
    });
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <ClipboardList className="w-5 h-5 text-acc2" /> Административные логи
          </h1>
          <p className="text-xs text-muted mt-1">
            Авторизация, изменения данных, действия операторов и ошибки интеграций. Секреты в журнал не попадают.
          </p>
        </div>
        <button
          type="button"
          onClick={() => loadLogs(page)}
          className="px-3 py-2 rounded-xl bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1.5"
        >
          <RefreshCw className="w-4 h-4" /> Обновить
        </button>
      </div>

      <div className="glass-panel p-4 rounded-2xl flex flex-col gap-3">
        <div className="flex items-center gap-2 text-xs text-white font-semibold">
          <Filter className="w-4 h-4 text-acc2" /> Фильтры
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          <input
            value={filters.q}
            onChange={(event) => updateFilter('q', event.target.value)}
            placeholder="Поиск по действию или пользователю"
            className="lg:col-span-2 bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-acc"
          />
          <select
            value={filters.event}
            onChange={(event) => updateFilter('event', event.target.value)}
            className="bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-acc"
          >
            <option value="">Все категории</option>
            {Object.entries(EVENT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select
            value={filters.status}
            onChange={(event) => updateFilter('status', event.target.value)}
            className="bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-acc"
          >
            <option value="">Все результаты</option>
            <option value="success">Успешные</option>
            <option value="failure">Ошибки</option>
          </select>
          <div className="flex gap-2">
            <input type="date" value={filters.from} onChange={(event) => updateFilter('from', event.target.value)} className="min-w-0 w-1/2 bg-[#0a1628] border border-line2 rounded-xl px-2 py-2 text-xs text-white outline-none focus:border-acc" />
            <input type="date" value={filters.to} onChange={(event) => updateFilter('to', event.target.value)} className="min-w-0 w-1/2 bg-[#0a1628] border border-line2 rounded-xl px-2 py-2 text-xs text-white outline-none focus:border-acc" />
          </div>
        </div>
        <div className="flex items-center justify-end gap-2">
          <button type="button" onClick={clearFilters} className="px-3 py-2 rounded-xl bg-line/50 hover:bg-line text-muted hover:text-white text-xs">Сбросить</button>
          <button type="button" onClick={applyFilters} className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white text-xs font-bold">Применить</button>
        </div>
      </div>

      <div className="glass-panel rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs min-w-[900px]">
            <thead>
              <tr className="border-b border-line text-muted uppercase text-[10px]">
                <th className="py-3 px-3">Время</th>
                <th className="py-3 px-3">Пользователь</th>
                <th className="py-3 px-3">Событие</th>
                <th className="py-3 px-3">Действие</th>
                <th className="py-3 px-3">Объект</th>
                <th className="py-3 px-3">Результат</th>
                <th className="py-3 px-3">Детали</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="py-10 text-center text-muted">Загрузка логов...</td></tr>
              ) : logs.length === 0 ? (
                <tr><td colSpan={7} className="py-10 text-center text-dim">Событий по заданным фильтрам нет</td></tr>
              ) : logs.map((log) => {
                const expanded = expandedId === log.id;
                return (
                  <React.Fragment key={log.id}>
                    <tr className="border-b border-line/40 hover:bg-white/5">
                      <td className="py-2.5 px-3 text-muted whitespace-nowrap">{formatDate(log.created)}</td>
                      <td className="py-2.5 px-3 text-white">
                        {log.actor_login || 'Аноним'}
                        {log.actor_role && <span className="text-dim ml-1">({log.actor_role})</span>}
                      </td>
                      <td className="py-2.5 px-3 text-acc2">{EVENT_LABELS[log.event_type] || log.event_type}</td>
                      <td className="py-2.5 px-3 font-mono text-white">{log.action}</td>
                      <td className="py-2.5 px-3 text-muted">{log.entity_type}{log.entity_id ? ` #${log.entity_id}` : ''}</td>
                      <td className="py-2.5 px-3">
                        <span className={log.status === 'success' ? 'text-ok' : 'text-bad'}>
                          {log.status === 'success' ? 'Успешно' : (log.error_code || 'Ошибка')}
                        </span>
                      </td>
                      <td className="py-2.5 px-3">
                        <button type="button" onClick={() => setExpandedId(expanded ? null : log.id)} className="text-acc2 hover:text-white flex items-center gap-1">
                          {expanded ? 'Скрыть' : 'Открыть'}
                          {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                        </button>
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="border-b border-line/40 bg-[#070e1a]/60">
                        <td colSpan={7} className="p-3">
                          <pre className="text-[11px] leading-relaxed text-muted whitespace-pre-wrap break-all">{JSON.stringify(log.details || {}, null, 2)}</pre>
                          {log.path && <div className="text-[10px] text-dim mt-2">API: {log.path}</div>}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between gap-3 p-3 border-t border-line text-xs">
          <span className="text-muted">Всего: {total}</span>
          <div className="flex items-center gap-2">
            <button type="button" disabled={page <= 0} onClick={() => { const next = page - 1; setPage(next); loadLogs(next); }} className="px-3 py-1.5 rounded-lg bg-line/50 text-white disabled:opacity-40">Назад</button>
            <span className="text-dim">{Math.min(page + 1, totalPages)} / {totalPages}</span>
            <button type="button" disabled={page + 1 >= totalPages} onClick={() => { const next = page + 1; setPage(next); loadLogs(next); }} className="px-3 py-1.5 rounded-lg bg-line/50 text-white disabled:opacity-40">Вперёд</button>
          </div>
        </div>
      </div>
    </div>
  );
}
