// === Подготовить: проверить канал и собрать сообщение ===
// Два формата поста:
//   { chat_id, text_html, image_base64, image_mime }  — картинка внутри файла
//   { chat_id, text | text_html, img }                — картинка по ссылке: одно сообщение с крупным превью
const cfg = $('Настройки').first().json;
const ALLOWED = [...cfg.allowed, String(cfg.admin_chat_id || '')].filter(Boolean);
const MIN_GAP_MINUTES = cfg.min_gap_minutes;
const CAPTION_LIMIT = cfg.caption_limit;
const s = $getWorkflowStaticData('global');
s.lastSentAt = s.lastSentAt || {};
s.chatByFile = s.chatByFile || {};
const file = $('Выбрать пост').first().json;
const p = $input.first().json;
const chat = String(p.chat_id || '');
s.chatByFile[file.name] = chat;

const base = { file: file.name, sha: file.sha, id: p.id || file.name.replace(/\.json$/, ''), channel: p.channel || '', chat_id: chat, text_html: p.text_html || p.text || '', due: file.due, preview: false };

if (!ALLOWED.includes(chat)) {
  return [{ json: { ...base, action: 'reject', reason: 'Канал ' + chat + ' не в белом списке' } }];
}
const last = s.lastSentAt[chat];
const manual = $execution.mode !== 'production';   // ручной запуск кнопкой Execute workflow
if (!manual && last && Date.now() - last < MIN_GAP_MINUTES * 60000) return [];   // ждём следующего окна (ручной запуск не ждёт)
if (!base.text_html && !p.image_base64 && !p.img) {
  return [{ json: { ...base, action: 'reject', reason: 'В посте нет ни текста, ни картинки' } }];
}

const plain = base.text_html.replace(/<[^>]+>/g, '')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');

if (p.img && !p.image_base64) {
  const html = '<a href="' + String(p.img).replace(/"/g, '%22') + '">&#8203;</a>' + base.text_html;
  return [{ json: { ...base, text_html: html, preview: true, action: 'text', needText: true } }];
}
if (p.image_base64) {
  const short = plain.length <= CAPTION_LIMIT;
  return [{
    json: { ...base, action: 'photo', caption: short ? base.text_html : '', needText: !short && plain.length > 0 },
    binary: { photo: { data: p.image_base64, mimeType: p.image_mime || 'image/jpeg', fileName: base.id + '.jpg' } }
  }];
}
return [{ json: { ...base, action: 'text', needText: true } }];
