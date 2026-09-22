import React, { useState, useEffect, useCallback } from 'react';
import { api, getToken, getUser, setSession } from './api';
import { useSSE } from './useSSE';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import ToastContainer from './components/ToastContainer';

import AuthGate from './pages/AuthGate';
import Dashboard from './pages/Dashboard';
import Campaigns from './pages/Campaigns';
import Journal from './pages/Journal';
import Contacts from './pages/Contacts';
import NumberPool from './pages/NumberPool';
import Operators from './pages/Operators';
import Templates from './pages/Templates';
import Blacklist from './pages/Blacklist';
import Reports from './pages/Reports';
import Settings from './pages/Settings';
import AdminLogs from './pages/AdminLogs';

export default function App() {
  const [user, setUser] = useState(() => getUser());
  const [token, setToken] = useState(() => getToken());
  const [activeTab, setActiveTab] = useState('dashboard');
  const [toasts, setToasts] = useState([]);
  const [opStatus, setOpStatus] = useState('free');
  const [activeCallIds, setActiveCallIds] = useState(() => new Set());
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null);
      setToken('');
    };
    window.addEventListener('ats_unauthorized', handleUnauthorized);
    return () => window.removeEventListener('ats_unauthorized', handleUnauthorized);
  }, []);

  // Repair sessions created by older builds that stored only the role.
  useEffect(() => {
    if (!token || !user || (user.login && user.id != null)) return undefined;
    let active = true;
    api('/auth/me').then((res) => {
      if (!active || !res?.ok) return;
      const restored = { id: res.user_id, login: res.login, role: res.role };
      setSession(token, restored);
      setUser(restored);
    });
    return () => { active = false; };
  }, [token, user]);

  const addToast = useCallback((msg, type = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, msg, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // SSE Realtime Event Handler. Track call IDs instead of incrementing for
  // every phase event, otherwise dialing → ringing → talk is counted 3 times.
  const handleSSEEvent = useCallback((evType, data) => {
    if (evType !== 'call') return;
    const callId = data?.id ?? data?.call_id;
    if (callId === undefined || callId === null) return;

    const terminalStatuses = new Set([
      'done', 'failed', 'busy', 'no_answer', 'machine', 'blocked',
      'canceled', 'cancelled', 'timeout', 'no_operator', 'exhausted', 'missed',
    ]);
    const key = String(callId);
    setActiveCallIds((previous) => {
      const next = new Set(previous);
      if (terminalStatuses.has(data?.status)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  useSSE(handleSSEEvent);

  // Seed the counter from the database so calls already in progress are not
  // invisible until their next SSE event arrives.
  useEffect(() => {
    if (!token) return undefined;
    let active = true;
    api('/dashboard').then((res) => {
      if (!active || !Array.isArray(res?.active_calls)) return;
      const ids = res.active_calls
        .map((call) => call?.id ?? call?.call_id)
        .filter((id) => id !== undefined && id !== null)
        .map(String);
      setActiveCallIds(new Set(ids));
    });
    return () => { active = false; };
  }, [token, refreshKey]);

  // Load the persisted operator status and write status changes to the API.
  useEffect(() => {
    let active = true;
    if (user?.role !== 'operator') return () => { active = false; };

    api('/operators').then((res) => {
      if (!active || !Array.isArray(res?.operators)) return;
      const mine = res.operators.find((op) => String(op.user_id) === String(user.id));
      if (mine?.status) setOpStatus(mine.status);
    });
    return () => { active = false; };
  }, [user]);

  const handleOperatorStatus = useCallback(async (status) => {
    const res = await api('/operators/status', { body: { status } });
    if (res?.ok) {
      setOpStatus(status);
      addToast(status === 'free' ? 'Статус: свободен' : 'Статус: перерыв', 'ok');
    } else {
      addToast(`Не удалось изменить статус: ${res?.error || '?'}`, 'err');
    }
  }, [addToast]);

  const handleRefresh = () => {
    setRefreshKey((previous) => previous + 1);
    addToast('Данные обновлены', 'ok');
  };

  const handleLogout = () => {
    setSession('', null);
    setUser(null);
    setToken('');
  };

  const handleLoginSuccess = (usr) => {
    setUser(usr);
    setToken(getToken());
  };

  if (!token || !user) {
    return <AuthGate onLoginSuccess={handleLoginSuccess} />;
  }

  const renderTabContent = () => {
    switch (activeTab) {
      case 'dashboard':
        return <Dashboard onNavigate={setActiveTab} refreshKey={refreshKey} />;
      case 'campaigns':
        return <Campaigns addToast={addToast} refreshKey={refreshKey} />;
      case 'journal':
        return <Journal addToast={addToast} refreshKey={refreshKey} />;
      case 'contacts':
        return <Contacts addToast={addToast} refreshKey={refreshKey} />;
      case 'numbers':
        return <NumberPool addToast={addToast} refreshKey={refreshKey} />;
      case 'acd':
        return <Operators addToast={addToast} refreshKey={refreshKey} />;
      case 'templates':
        return <Templates addToast={addToast} refreshKey={refreshKey} />;
      case 'blacklist':
        return <Blacklist addToast={addToast} refreshKey={refreshKey} />;
      case 'reports':
        return <Reports addToast={addToast} refreshKey={refreshKey} />;
      case 'settings':
        return <Settings addToast={addToast} refreshKey={refreshKey} />;
      case 'admin-logs':
        return <AdminLogs addToast={addToast} refreshKey={refreshKey} />;
      default:
        return <Dashboard onNavigate={setActiveTab} refreshKey={refreshKey} />;
    }
  };

  return (
    <div className="flex min-h-screen bg-[#070d17] text-[#eef4ff] font-sans">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        user={user}
        onLogout={handleLogout}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <Topbar
          user={user}
          opStatus={opStatus}
          onStatusChange={handleOperatorStatus}
          onRefresh={handleRefresh}
          activeCallsCount={activeCallIds.size}
        />

        <main className="flex-1 p-6 max-w-[1500px] w-full mx-auto">
          {renderTabContent()}
        </main>
      </div>

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
