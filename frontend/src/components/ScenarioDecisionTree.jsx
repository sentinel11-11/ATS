import React, { useState, useEffect } from 'react';
import { Phone, HelpCircle, CheckCircle, AlertCircle, Plus, Trash2, Code, Cpu } from 'lucide-react';

export default function ScenarioDecisionTree({ scenario, onChange }) {
  const [activeTab, setActiveTab] = useState('tree'); // 'tree' | 'json'
  const [sc, setSc] = useState(() => {
    let parsed = {};
    if (typeof scenario === 'string') {
      try { parsed = JSON.parse(scenario || '{}'); } catch (e) { parsed = {}; }
    } else {
      parsed = scenario || {};
    }
    return {
      greeting: parsed.greeting || 'Здравствуйте!',
      intro: parsed.intro || '',
      questions: Array.isArray(parsed.questions) ? parsed.questions : [],
      qualified_text: parsed.qualified_text || 'Спасибо, передаю ваш звонок специалисту.',
      not_qualified_text: parsed.not_qualified_text || 'Спасибо, до свидания.',
      ...parsed,
    };
  });
  const [jsonText, setJsonText] = useState(() => JSON.stringify(sc, null, 2));

  const updateSc = (newSc) => {
    const safeSc = {
      ...newSc,
      questions: Array.isArray(newSc?.questions) ? newSc.questions : [],
    };
    setSc(safeSc);
    const text = JSON.stringify(safeSc, null, 2);
    setJsonText(text);
    if (onChange) onChange(safeSc);
  };

  const handleJsonChange = (val) => {
    setJsonText(val);
    try {
      const parsed = JSON.parse(val);
      const safeParsed = {
        ...parsed,
        questions: Array.isArray(parsed?.questions) ? parsed.questions : [],
      };
      setSc(safeParsed);
      if (onChange) onChange(safeParsed);
    } catch (e) {
      // syntax error while typing
    }
  };

  const questionsList = Array.isArray(sc?.questions) ? sc.questions : [];

  const addQuestion = () => {
    const qCount = questionsList.length + 1;
    const newQ = {
      id: `q${qCount}`,
      text: 'Интересует ли вас предложение? Нажмите 1 — да, 2 — нет.',
      choices: {
        '1': { next: 'end', qualified: true },
        '2': { next: 'end', qualified: false },
      },
    };
    updateSc({ ...sc, questions: [...questionsList, newQ] });
  };

  const removeQuestion = (qIdx) => {
    const nextQuestions = questionsList.filter((_, idx) => idx !== qIdx);
    updateSc({ ...sc, questions: nextQuestions });
  };

  const updateQuestionText = (qIdx, text) => {
    const nextQuestions = [...questionsList];
    if (nextQuestions[qIdx]) {
      nextQuestions[qIdx].text = text;
      updateSc({ ...sc, questions: nextQuestions });
    }
  };

  const addBranch = (qIdx) => {
    const nextQuestions = [...questionsList];
    const q = nextQuestions[qIdx];
    if (!q) return;
    const choices = { ...(q.choices || {}) };
    const nextKey = String(Object.keys(choices).length + 1);
    choices[nextKey] = { next: 'end', qualified: null };
    nextQuestions[qIdx].choices = choices;
    updateSc({ ...sc, questions: nextQuestions });
  };

  const removeBranch = (qIdx, key) => {
    const nextQuestions = [...questionsList];
    if (!nextQuestions[qIdx]) return;
    const choices = { ...(nextQuestions[qIdx].choices || {}) };
    delete choices[key];
    nextQuestions[qIdx].choices = choices;
    updateSc({ ...sc, questions: nextQuestions });
  };

  const updateBranchChoice = (qIdx, key, field, val) => {
    const nextQuestions = [...questionsList];
    if (!nextQuestions[qIdx]) return;
    const choices = { ...(nextQuestions[qIdx].choices || {}) };
    const choice = { ...(choices[key] || {}) };
    
    if (field === 'next') choice.next = val;
    if (field === 'qualified') {
      choice.qualified = val === 'true' ? true : val === 'false' ? false : null;
    }
    
    choices[key] = choice;
    nextQuestions[qIdx].choices = choices;
    updateSc({ ...sc, questions: nextQuestions });
  };

  return (
    <div className="flex flex-col gap-4 p-4 rounded-2xl bg-[#060d18]/80 border border-line mt-3">
      {/* Tab Header */}
      <div className="flex items-center gap-2 border-b border-line pb-3">
        <button
          type="button"
          onClick={() => setActiveTab('tree')}
          className={`px-3.5 py-1.5 rounded-xl font-semibold text-xs transition-all flex items-center gap-2 ${
            activeTab === 'tree'
              ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow-md shadow-acc/30'
              : 'bg-[#0e1c30] text-muted hover:text-white border border-line'
          }`}
        >
          <Cpu className="w-4 h-4" /> Дерево Решений (Visual Flow)
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('json')}
          className={`px-3.5 py-1.5 rounded-xl font-semibold text-xs transition-all flex items-center gap-2 ${
            activeTab === 'json'
              ? 'bg-gradient-to-r from-[#5b8cff] to-[#3d6bff] text-white shadow-md shadow-acc/30'
              : 'bg-[#0e1c30] text-muted hover:text-white border border-line'
          }`}
        >
          <Code className="w-4 h-4" /> JSON-код
        </button>
      </div>

      {activeTab === 'tree' ? (
        <div className="flex flex-col items-center gap-3 p-4 rounded-2xl bg-gradient-to-b from-[#102038]/40 to-[#060c16]/80 border border-line max-h-[580px] overflow-y-auto">
          {/* Node 1: Start Node */}
          <div className="w-full max-w-[720px] bg-gradient-to-b from-[#102038] to-[#091220] border border-line2 border-l-4 border-l-cy rounded-2xl p-4 shadow-lg transition-all hover:border-acc2">
            <div className="flex items-center justify-between font-bold text-sm mb-3">
              <div className="flex items-center gap-2 text-cy">
                <Phone className="w-4 h-4" />
                <span>1. Старт: Приветствие и Вступление</span>
                <span className="px-2 py-0.5 rounded-md bg-cy/20 text-cy text-[11px] font-semibold">
                  НАЧАЛО
                </span>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3">
              <div>
                <span className="text-xs font-medium text-muted block mb-1">Приветствие бота</span>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm outline-none focus:border-acc"
                  value={sc.greeting || ''}
                  placeholder="Здравствуйте!"
                  onChange={(e) => updateSc({ ...sc, greeting: e.target.value })}
                />
              </div>
              <div>
                <span className="text-xs font-medium text-muted block mb-1">Доп. вступительный текст (опционально)</span>
                <input
                  className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm outline-none focus:border-acc"
                  value={sc.intro || ''}
                  placeholder="Служба заботы о клиентах..."
                  onChange={(e) => updateSc({ ...sc, intro: e.target.value })}
                />
              </div>
            </div>
          </div>

          {/* Question Nodes */}
          {questionsList.map((q, qIdx) => {
            const qId = q.id || `q${qIdx + 1}`;
            const choices = q.choices || {};
            const choiceKeys = Object.keys(choices);

            return (
              <React.Fragment key={qIdx}>
                {/* Connector Arrow */}
                <div className="flex flex-col items-center text-acc2 my-[-2px]">
                  <div className="w-[2px] h-4 bg-gradient-to-b from-acc to-acc2"></div>
                  <div className="text-xs">▼</div>
                </div>

                <div className="w-full max-w-[720px] bg-gradient-to-b from-[#102038] to-[#091220] border border-line2 border-l-4 border-l-acc rounded-2xl p-4 shadow-lg transition-all hover:border-acc2">
                  <div className="flex items-center justify-between font-bold text-sm mb-3">
                    <div className="flex items-center gap-2 text-acc2">
                      <HelpCircle className="w-4 h-4" />
                      <span>Вопрос #{qIdx + 1} ({qId})</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeQuestion(qIdx)}
                      className="px-2.5 py-1 rounded-lg bg-bad/20 hover:bg-bad/30 text-bad text-xs font-semibold flex items-center gap-1 transition-all"
                    >
                      <Trash2 className="w-3.5 h-3.5" /> Удалить
                    </button>
                  </div>

                  <div className="mb-3">
                    <span className="text-xs font-medium text-muted block mb-1">Текст вопроса абоненту</span>
                    <input
                      className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-sm outline-none focus:border-acc"
                      value={q.text || ''}
                      placeholder="Интересует ли вас предложение?"
                      onChange={(e) => updateQuestionText(qIdx, e.target.value)}
                    />
                  </div>

                  <div className="flex items-center justify-between mt-3 mb-2">
                    <span className="text-xs font-semibold text-muted">🌿 Ветки ответов и условия перехода:</span>
                    <button
                      type="button"
                      onClick={() => addBranch(qIdx)}
                      className="px-2.5 py-1 rounded-lg bg-line/60 hover:bg-line text-white text-xs font-semibold flex items-center gap-1 transition-all"
                    >
                      <Plus className="w-3.5 h-3.5" /> Ветка ответа
                    </button>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2 border-t border-dashed border-line2">
                    {choiceKeys.length === 0 && (
                      <span className="text-xs text-dim col-span-2">Ветвей нет. Нажмите «Ветка ответа».</span>
                    )}
                    {choiceKeys.map((key) => {
                      const ch = choices[key] || {};
                      return (
                        <div key={key} className="p-3 rounded-xl bg-[#081221] border border-line flex flex-col gap-2">
                          <div className="flex items-center justify-between text-xs font-bold text-acc2">
                            <span className="px-2 py-0.5 rounded bg-acc/20 text-acc2">Кнопка/Ответ: «{key}»</span>
                            <button
                              type="button"
                              onClick={() => removeBranch(qIdx, key)}
                              className="text-muted hover:text-bad p-1"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          <div>
                            <span className="text-[11px] text-muted block mb-0.5">Куда переходить:</span>
                            <select
                              className="w-full bg-[#0a1628] border border-line rounded-lg px-2.5 py-1.5 text-xs outline-none text-white"
                              value={ch.next || 'end'}
                              onChange={(e) => updateBranchChoice(qIdx, key, 'next', e.target.value)}
                            >
                              <option value="end">🏁 Завершить звонок</option>
                              {questionsList.map((otherQ, otherIdx) => {
                                const oId = otherQ.id || `q${otherIdx + 1}`;
                                if (otherIdx === qIdx) return null;
                                return (
                                  <option key={oId} value={oId}>
                                    Переход к Вопросу #{otherIdx + 1} ({oId})
                                  </option>
                                );
                              })}
                            </select>
                          </div>
                          <div>
                            <span className="text-[11px] text-muted block mb-0.5">Результат квалификации:</span>
                            <select
                              className="w-full bg-[#0a1628] border border-line rounded-lg px-2.5 py-1.5 text-xs outline-none text-white"
                              value={ch.qualified === true ? 'true' : ch.qualified === false ? 'false' : 'null'}
                              onChange={(e) => updateBranchChoice(qIdx, key, 'qualified', e.target.value)}
                            >
                              <option value="null">⚪ Без изменений</option>
                              <option value="true">🟢 Успешно (Квалифицирован)</option>
                              <option value="false">🔴 Отказ (Не квалифицирован)</option>
                            </select>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </React.Fragment>
            );
          })}

          {/* Add Question Button */}
          <div className="flex flex-col items-center text-acc2 my-[-2px]">
            <div className="w-[2px] h-4 bg-gradient-to-b from-acc to-acc2"></div>
            <div className="text-xs">▼</div>
          </div>

          <button
            type="button"
            onClick={addQuestion}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#22c68c] to-[#159a6e] hover:brightness-110 text-white font-semibold text-xs flex items-center gap-1.5 shadow-md shadow-ok/20 transition-all"
          >
            <Plus className="w-4 h-4" /> Добавить шаг-вопрос в дерево
          </button>

          <div className="flex flex-col items-center text-acc2 my-[-2px]">
            <div className="w-[2px] h-4 bg-gradient-to-b from-acc to-acc2"></div>
            <div className="text-xs">▼</div>
          </div>

          {/* Final Nodes */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 w-full max-w-[720px]">
            <div className="bg-gradient-to-b from-[#102038] to-[#091220] border border-line2 border-l-4 border-l-ok rounded-2xl p-4 shadow-lg">
              <div className="flex items-center gap-2 font-bold text-sm text-ok mb-2">
                <CheckCircle className="w-4 h-4" /> Успех (Квалифицирован)
              </div>
              <span className="text-xs text-muted block mb-1">Реплика бота при успехе</span>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs outline-none focus:border-ok"
                value={sc.qualified_text || ''}
                placeholder="Спасибо, соединяю со специалистом"
                onChange={(e) => updateSc({ ...sc, qualified_text: e.target.value })}
              />
            </div>

            <div className="bg-gradient-to-b from-[#102038] to-[#091220] border border-line2 border-l-4 border-l-bad rounded-2xl p-4 shadow-lg">
              <div className="flex items-center gap-2 font-bold text-sm text-bad mb-2">
                <AlertCircle className="w-4 h-4" /> Отказ / Завершение
              </div>
              <span className="text-xs text-muted block mb-1">Реплика бота при отказе</span>
              <input
                className="w-full bg-[#0a1628] border border-line2 rounded-xl px-3 py-2 text-xs outline-none focus:border-bad"
                value={sc.not_qualified_text || ''}
                placeholder="Спасибо, до свидания"
                onChange={(e) => updateSc({ ...sc, not_qualified_text: e.target.value })}
              />
            </div>
          </div>
        </div>
      ) : (
        <textarea
          className="w-full bg-[#0a1628] border border-line2 rounded-xl p-3 text-xs font-mono text-white outline-none focus:border-acc min-h-[300px]"
          value={jsonText}
          onChange={(e) => handleJsonChange(e.target.value)}
        />
      )}
    </div>
  );
}
