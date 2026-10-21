// Логика оценки результата сохранения настроек (два независимых запроса:
// /settings/raw — конфигурация провайдера, /settings/save — базовые настройки
// и пароль). Выделена в чистый модуль, чтобы поведение было покрыто тестом
// (tests/test_settings_save_logic.py запускает этот файл через node).
//
// P1-дефект: раньше успех показывался по условию rawRes.ok OR basicRes.ok —
// при сбое одного из двух запросов UI сообщал «все настройки сохранены».

const SECTIONS = [
  { key: 'raw', label: 'Конфигурация провайдера', res: 'rawRes' },
  { key: 'basic', label: 'Основные настройки', res: 'basicRes' },
];

function errOf(res) {
  if (!res) return 'нет ответа';
  if (res.error) return String(res.error);
  if (res.ok === false) return 'ошибка';
  return 'ошибка';
}

export function summarizeSaveResults(rawRes, basicRes) {
  const results = { rawRes, basicRes };
  const saved = [];
  const failed = [];
  for (const s of SECTIONS) {
    const res = results[s.res];
    if (res && res.ok === true) {
      saved.push(s.label);
    } else {
      failed.push({ section: s.label, error: errOf(res) });
    }
  }
  const ok = saved.length === SECTIONS.length;
  let message;
  if (ok) {
    message = 'Все системные настройки успешно сохранены';
  } else if (failed.length === SECTIONS.length) {
    message = 'Ошибка сохранения: ничего не сохранено';
  } else {
    const failedLabels = failed.map((f) => f.section).join(', ');
    message = `Сохранено частично: не сохранено — ${failedLabels}`;
  }
  return { ok, savedSections: saved, failedSections: failed, message };
}

// Сохранение-пролог перед действиями «проверить/синхронизировать»: действие
// имеет смысл выполнять только если вся конфигурация реально сохранилась.
export async function saveAllBeforeAction(api, providerConfig, basicSettings) {
  const [rawRes, basicRes] = await Promise.all([
    api('/settings/raw', { body: { provider_config: providerConfig } }),
    api('/settings/save', { body: { ...basicSettings } }),
  ]);
  const summary = summarizeSaveResults(rawRes, basicRes);
  return { summary, rawRes, basicRes };
}
