import React, { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2, Code } from 'lucide-react';
import { api } from '../api';
import Modal from '../components/Modal';
import ScenarioDecisionTree from '../components/ScenarioDecisionTree';

export default function Templates({ addToast }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingTemplate, setEditingTemplate] = useState(null);

  const loadTemplates = async () => {
    setLoading(true);
    const res = await api('/templates');
    if (res && res.templates) {
      setTemplates(Array.isArray(res.templates) ? res.templates : []);
    }
    setLoading(false);
  };

  useEffect(() => {
    loadTemplates();
  }, []);

  const handleOpenEdit = (t) => {
    const tmpl = t || {
      id: 0,
      name: 'Новый шаблон',
      text: 'Здравствуйте! Это автоматический информационный звонок.',
      scenario: {},
    };
    setEditingTemplate(tmpl);
  };

  const handleSaveTemplate = async () => {
    if (!editingTemplate || !editingTemplate.name) return addToast('Укажите название шаблона', 'warn');

    const res = await api('/templates/save', { body: editingTemplate });
    if (res && res.ok) {
      addToast('Шаблон сохранен', 'ok');
      setEditingTemplate(null);
      loadTemplates();
    } else {
      addToast('Ошибка сохранения шаблона', 'err');
    }
  };

  const handleDeleteTemplate = async (id) => {
    if (!window.confirm('Удалить шаблон?')) return;
    const res = await api('/templates/delete', { body: { id } });
    if (res && res.ok) {
      addToast('Шаблон удален', 'ok');
      loadTemplates();
    } else {
      addToast('Ошибка удаления', 'err');
    }
  };

  const templatesList = Array.isArray(templates) ? templates : [];

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
                <td colSpan={4} className="py-8 text-center text-muted">Загрузка шаблонов...</td>
              </tr>
            ) : templatesList.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-dim">Шаблонов нет</td>
              </tr>
            ) : (
              templatesList.map((t) => (
                <tr key={t.id} className="border-b border-line/40 hover:bg-white/5 transition-colors">
                  <td className="py-3 px-3 font-semibold text-white">{t.name}</td>
                  <td className="py-3 px-3 text-muted max-w-md truncate">{t.text || 'ИИ Сценарий (Дерево решений)'}</td>
                  <td className="py-3 px-3">
                    <span className="badge">Активен</span>
                  </td>
                  <td className="py-3 px-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => handleOpenEdit(t)}
                        className="px-2.5 py-1 rounded-lg bg-line/60 hover:bg-line text-white text-xs font-semibold"
                      >
                        Редактировать
                      </button>
                      <button
                        onClick={() => handleDeleteTemplate(t.id)}
                        className="p-1 rounded-lg text-muted hover:text-bad"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Modal
        isOpen={!!editingTemplate}
        onClose={() => setEditingTemplate(null)}
        title={editingTemplate?.id ? `Редактирование шаблона #${editingTemplate.id}` : 'Новый шаблон сценария'}
        wide={true}
      >
        {editingTemplate && (
          <div className="flex flex-col gap-4 text-xs">
            <div>
              <label className="font-semibold text-muted block mb-1">Название шаблона</label>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-white outline-none focus:border-acc font-semibold"
                value={editingTemplate.name}
                onChange={(e) => setEditingTemplate({ ...editingTemplate, name: e.target.value })}
                placeholder="Информационное сообщение"
              />
            </div>

            <div>
              <label className="font-semibold text-muted block mb-1">Основной текст / Реплика (Информатор)</label>
              <textarea
                className="w-full bg-[#0a1628] border border-line2 rounded-xl p-3 text-white outline-none focus:border-acc min-h-[80px]"
                value={editingTemplate.text || ''}
                onChange={(e) => setEditingTemplate({ ...editingTemplate, text: e.target.value })}
                placeholder="Здравствуйте! У нас для вас специальное предложение..."
              />
            </div>

            <div className="flex flex-col gap-2 pt-2 border-t border-line">
              <span className="font-bold text-white text-xs">Иерархическое дерево решений бота (Decision Tree):</span>
              <ScenarioDecisionTree
                scenario={editingTemplate.scenario}
                onChange={(scObj) => setEditingTemplate({ ...editingTemplate, scenario: scObj })}
              />
            </div>

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-line">
              <button
                type="button"
                onClick={() => setEditingTemplate(null)}
                className="px-4 py-2 rounded-xl bg-line/50 hover:bg-line text-white"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleSaveTemplate}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] font-bold text-white shadow-lg"
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
