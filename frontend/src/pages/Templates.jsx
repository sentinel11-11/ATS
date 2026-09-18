import React, { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2, FileText } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';
import ScenarioDecisionTree from '../components/ScenarioDecisionTree';

export default function Templates({ addToast }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingTpl, setEditingTpl] = useState(null); // null = modal closed

  const loadTemplates = async () => {
    setLoading(true);
    const res = await api('/templates');
    if (res && res.templates) {
      setTemplates(res.templates);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadTemplates();
  }, []);

  const handleOpenEdit = (tpl) => {
    const t = tpl || { id: 0, name: '', text: '', active: true, scenario: {} };
    setEditingTpl(t);
  };

  const handleSave = async () => {
    if (!editingTpl) return;
    const res = await api('/templates/save', {
      body: {
        id: editingTpl.id,
        name: editingTpl.name,
        text: editingTpl.text,
        active: editingTpl.active,
        scenario: editingTpl.scenario,
      },
    });

    if (res && res.ok) {
      addToast('Шаблон сохранён', 'ok');
      setEditingTpl(null);
      loadTemplates();
    } else {
      addToast('Ошибка: ' + (res?.error || '?'), 'err');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Удалить шаблон?')) return;
    const res = await api('/templates/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Шаблон удалён', 'ok');
      loadTemplates();
    }
  };

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-200">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Шаблоны и сценарии ИИ-ботов</h1>
        <button
          onClick={() => handleOpenEdit(null)}
          className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-semibold text-xs text-white shadow-lg shadow-acc/30 flex items-center gap-1.5 hover:brightness-110 transition-all"
        >
          <Plus className="w-4 h-4" /> Новый шаблон
        </button>
      </div>

      <div className="glass-panel p-6 rounded-2xl overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-line text-muted uppercase text-[10px]">
              <th className="py-2.5 px-3">Название</th>
              <th className="py-2.5 px-3">Текст / Сценарий</th>
              <th className="py-2.5 px-3">Статус</th>
              <th className="py-2.5 px-3 text-right">Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-muted">
                  Загрузка шаблонов...
                </td>
              </tr>
            ) : templates.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-dim">
                  Шаблонов пока нет. Создайте первый шаблон.
                </td>
              </tr>
            ) : (
              templates.map((t) => (
                <tr key={t.id} className="border-b border-line/50 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-semibold text-white">{t.name}</td>
                  <td className="py-3 px-3 text-muted max-w-xs truncate">{t.text || '—'}</td>
                  <td className="py-3 px-3">
                    <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                      t.active ? 'bg-ok/20 text-ok' : 'bg-line text-muted'
                    }`}>
                      {t.active ? 'Активен' : 'Отключен'}
                    </span>
                  </td>
                  <td className="py-3 px-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => handleOpenEdit(t)}
                        className="px-2.5 py-1 rounded-lg bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1"
                      >
                        <Edit2 className="w-3.5 h-3.5" /> Редактировать
                      </button>
                      <button
                        onClick={() => handleDelete(t.id)}
                        className="p-1 rounded-lg text-muted hover:text-bad hover:bg-bad/10"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Edit Template Modal */}
      <Modal
        isOpen={!!editingTpl}
        onClose={() => setEditingTpl(null)}
        title={editingTpl?.id ? `Шаблон #${editingTpl.id}` : 'Новый шаблон'}
        wide={true}
      >
        {editingTpl && (
          <div className="flex flex-col gap-4 text-xs">
            <div>
              <label className="font-semibold text-muted block mb-1">Название шаблона</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm text-white outline-none focus:border-acc"
                value={editingTpl.name || ''}
                onChange={(e) => setEditingTpl({ ...editingTpl, name: e.target.value })}
                placeholder="Квалификация опрос"
              />
            </div>

            <div>
              <label className="font-semibold text-muted block mb-1">
                Текст озвучки — подстановки: &#123;name&#125; &#123;phone&#125; &#123;group&#125; &#123;note&#125;
              </label>
              <textarea
                className="w-full bg-[#0a1628] border border-line2 rounded-xl p-3 text-sm text-white outline-none focus:border-acc min-h-[80px]"
                value={editingTpl.text || ''}
                onChange={(e) => setEditingTpl({ ...editingTpl, text: e.target.value })}
                placeholder="Здравствуйте, {name}! У нас для вас специальное предложение."
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="tplActive"
                checked={editingTpl.active ?? true}
                onChange={(e) => setEditingTpl({ ...editingTpl, active: e.target.checked })}
                className="accent-acc"
              />
              <label htmlFor="tplActive" className="text-white font-medium cursor-pointer">
                Шаблон активен
              </label>
            </div>

            {/* Scenario Decision Tree Editor */}
            <div>
              <label className="font-semibold text-muted block mb-1">
                Сценарий ИИ-агента / Голосового бота (Дерево диалога)
              </label>
              <ScenarioDecisionTree
                scenario={editingTpl.scenario}
                onChange={(newSc) => setEditingTpl({ ...editingTpl, scenario: newSc })}
              />
            </div>

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
              <button
                onClick={() => setEditingTpl(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white font-semibold text-xs"
              >
                Отмена
              </button>
              <button
                onClick={handleSave}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] hover:brightness-110 text-white font-bold text-xs shadow-lg shadow-acc/30"
              >
                Сохранить шаблон
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
