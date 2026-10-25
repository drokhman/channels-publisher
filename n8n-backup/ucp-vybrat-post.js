// === Выбрать пост: какой пост из outbox публиковать сейчас ===
// Имя файла задаёт время выхода (по Москве):
//   2026-10-19-0900-slug.json  → 19 октября в 09:00
//   2026-10-19-slug.json       → 19 октября, время из Настроек (default_hour)
//   slug.json (без даты)       → сразу
const cfg = $('Настройки').first().json;
const DEFAULT_HOUR = cfg.default_hour;
const MIN_GAP_MINUTES = cfg.min_gap_minutes;
const s = $getWorkflowStaticData('global');
s.lastSentAt = s.lastSentAt || {};
s.chatByFile = s.chatByFile || {};
s.doneFiles = s.doneFiles || {};   // защита от повтора, если файл не удалился из outbox
const now = Date.now();

const manual = $execution.mode === 'manual';   // ручной запуск кнопкой Execute workflow
if (!manual && s.pauseUntil && now < s.pauseUntil) return [];   // пауза после ошибки Telegram (ручной запуск её пропускает)

function dueAt(name) {
  const m = name.match(/^(\d{4})-(\d{2})-(\d{2})(?:-(\d{2})(\d{2}))?(?=[-_.])/);
  if (!m) return 0;
  let hh = DEFAULT_HOUR, mm = 0;
  if (m[4] !== undefined && +m[4] < 24 && +m[5] < 60) { hh = +m[4]; mm = +m[5]; }
  return Date.UTC(+m[1], +m[2] - 1, +m[3], hh - 3, mm);   // Москва = UTC+3
}

const listed = $input.all().map(i => i.json).filter(f => f.type === 'file' && f.name.endsWith('.json'));
for (const k of Object.keys(s.doneFiles)) if (!listed.some(f => f.name === k)) delete s.doneFiles[k];
const files = listed
  .filter(f => s.doneFiles[f.name] !== f.sha)
  .map(f => ({ ...f, due: dueAt(f.name) }))
  .filter(f => f.due <= now)
  .sort((a, b) => a.due - b.due || a.name.localeCompare(b.name));

for (const f of files) {
  const chat = s.chatByFile[f.name];
  const last = chat ? s.lastSentAt[chat] : undefined;
  if (last && now - last < MIN_GAP_MINUTES * 60000) continue;   // этот канал ещё «отдыхает»
  return [{ json: { name: f.name, sha: f.sha, url: f.url, due: new Date(f.due).toISOString() } }];
}
return [];
