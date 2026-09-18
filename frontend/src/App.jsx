import React, { useState, useCallback } from 'react';
import { getToken, getUser, setSession } from './api';
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

export default function App() {
  const [user, setUser] = useState(() => getUser());
  const [token, setToken] = useState(() => getToken());
  const [activeTab, setActiveTab] = useState('dashboard');
  const [toasts, setToasts] = useState([]);
  const [opStatus, setOpStatus] = useState('free');
  const [activeCallsCount, setActiveCallsCount] = useState(0);

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

  // SSE Realtime Event Handler
  const handleSSEEvent = useCallback((evType, data) => {
    if (evType === 'call') {
      if (data.status === 'dialing' || data.status === 'ringing' || data.status === 'talk') {
        setActiveCallsCount((prev) => prev + 1);
      }
      if (data.status === 'done' || data.status === 'failed') {
        setActiveCallsCount((prev) => Math.max(0, prev - 1));
      }
    }
  }, []);

  useSSE(handleSSEEvent);

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
        return <Dashboard onNavigate={setActiveTab} />;
      case 'campaigns':
        return <Campaigns addToast={addToast} />;
      case 'journal':
        return <Journal addToast={addToast} />;
      case 'contacts':
        return <Contacts addToast={addToast} />;
      case 'numbers':
        return <NumberPool addToast={addToast} />;
      case 'acd':
        return <Operators addToast={addToast} />;
      case 'templates':
        return <Templates addToast={addToast} />;
      case 'blacklist':
        return <Blacklist addToast={addToast} />;
      case 'reports':
        return <Reports addToast={addToast} />;
      case 'settings':
        return <Settings addToast={addToast} />;
      default:
        return <Dashboard onNavigate={setActiveTab} />;
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
          setOpStatus={setOpStatus}
          onRefresh={() => addToast('Данные обновлены', 'ok')}
          activeCallsCount={activeCallsCount}
        />

        <main className="flex-1 p-6 max-w-[1500px] w-full mx-auto">
          {renderTabContent()}
        </main>
      </div>

      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}
