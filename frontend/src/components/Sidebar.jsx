import React from 'react';
import {
  LayoutDashboard,
  PlayCircle,
  Inbox,
  Users,
  PhoneCall,
  Headphones,
  FileText,
  ShieldAlert,
  BarChart3,
  Settings,
  ClipboardList,
  LogOut,
  Phone
} from 'lucide-react';

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Обзор', icon: LayoutDashboard },
  { id: 'campaigns', label: 'Кампании', icon: PlayCircle },
  { id: 'journal', label: 'Журнал звонков', icon: Inbox },
  { id: 'contacts', label: 'Контакты и базы', icon: Users },
  { id: 'numbers', label: 'Пул номеров', icon: PhoneCall },
  { id: 'acd', label: 'Операторы / ACD', icon: Headphones },
  { id: 'templates', label: 'Шаблоны и сценарии', icon: FileText, adminOnly: true },
  { id: 'blacklist', label: 'Чёрный список', icon: ShieldAlert },
  { id: 'reports', label: 'Отчёты', icon: BarChart3, adminOnly: true },
  { id: 'settings', label: 'Настройки', icon: Settings, adminOnly: true },
  { id: 'admin-logs', label: 'Админские логи', icon: ClipboardList, adminOnly: true },
];

export default function Sidebar({ activeTab, setActiveTab, user, onLogout }) {
  const isAdmin = user?.role === 'admin';

  return (
    <aside className="w-64 h-screen sticky top-0 flex flex-col p-4 bg-[#070e1a]/85 backdrop-blur-md border-r border-line z-20 gap-2">
      {/* Brand Header */}
      <div className="flex items-center gap-3 px-2 py-3 mb-2">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#5b8cff] to-[#3d6bff] flex items-center justify-center text-white shadow-lg shadow-acc/30">
          <Phone className="w-5 h-5" />
        </div>
        <div>
          <span className="font-extrabold text-lg tracking-wide text-white block leading-tight">
            ATS <b className="text-acc2">v2</b>
          </span>
          <span className="text-[11px] text-muted font-medium">Диспетчер обзвона</span>
        </div>
      </div>

      {/* Nav List */}
      <nav className="flex-1 flex flex-col gap-1 overflow-y-auto pr-1">
        {NAV_ITEMS.map((item) => {
          if (item.adminOnly && !isAdmin) return null;
          const Icon = item.icon;
          const isActive = activeTab === item.id;

          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-xl font-medium text-xs transition-all duration-150 text-left ${
                isActive
                  ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow-md shadow-acc/25 font-semibold'
                  : 'text-[#a9bdd8] hover:bg-[#12243d]/60 hover:text-white'
              }`}
            >
              <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-acc2'}`} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* User Info & Logout */}
      <div className="pt-3 border-t border-line flex items-center justify-between px-2">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-acc/20 border border-acc/40 text-acc2 font-bold text-xs flex items-center justify-center uppercase">
            {user?.login?.[0] || 'U'}
          </div>
          <div className="text-xs">
            <span className="font-semibold text-white block">{user?.login || 'Пользователь'}</span>
            <span className="text-[10px] text-dim block">{isAdmin ? 'Администратор' : 'Оператор'}</span>
          </div>
        </div>
        <button
          onClick={onLogout}
          title="Выйти из системы"
          className="p-2 rounded-lg text-muted hover:text-bad hover:bg-bad/10 transition-colors"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </aside>
  );
}
