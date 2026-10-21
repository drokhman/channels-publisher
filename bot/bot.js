// === БОТ-ВОРОНКА: логика. Обычно менять не нужно ===
const S = $('Настройки').first().json;
const T = $('Тексты').first().json;
const st = $getWorkflowStaticData('global');
st.offset = st.offset || 0;
st.users = st.users || {};
st.gifts = st.gifts || {};
const helpers = this.helpers;
if (!helpers || !helpers.httpRequest) throw new Error('В этой версии n8n нет this.helpers.httpRequest в узле Code. Напишите ассистенту.');
const now = Date.now();
const log = [];

async function api(method, body, timeoutMs) {
  try {
    return await helpers.httpRequest({
      method: 'POST',
      url: `https://api.telegram.org/bot${S.token}/${method}`,
      body, json: true, timeout: timeoutMs || 20000
    });
  } catch (e) {
    const r = e.response && e.response.body ? e.response.body : null;
    return { ok: false, error_code: r && r.error_code, description: (r && r.description) || String(e.message || e) };
  }
}
const send = (chat_id, text, kb) => api('sendMessage', {
  chat_id, text, parse_mode: 'HTML', disable_web_page_preview: false,
  ...(kb ? { reply_markup: { inline_keyboard: kb } } : {})
});
const branches = Object.keys(S.channels);
const clean = s => String(s || '').replace(/[^A-Za-z0-9_\-]/g, '').slice(0, 64);
const branchOf = p => branches.find(b => p === b || p.startsWith(b + '_')) || null;
const chanUrl = b => 'https://t.me/' + S.channels[b].replace('@', '');
const isOwner = id => Number(id) === Number(S.owner_id);
function mskHour(ts) { return new Date(ts + 3 * 3600e3).getUTCHours(); }

async function welcome(u) {
  const B = T[u.branch];
  await send(u.id, B.welcome, [
    [{ text: T.subscribe_button, url: chanUrl(u.branch) }],
    [{ text: T.check_button, callback_data: 'check:' + u.branch }]
  ]);
}
async function giveGift(u) {
  const B = T[u.branch];
  const fid = st.gifts[u.branch];
  if (fid) {
    await api('sendDocument', { chat_id: u.id, document: fid, caption: B.gift_caption });
  } else {
    await send(u.id, T.gift_missing);
    await send(S.owner_id, `⚠️ Нет файла подарка для ветки ${u.branch}. Пришлите боту PDF с подписью gift_${u.branch}`);
  }
  u.subscribed = true; u.giftAt = now; u.step = 0;
  u.next = now + B.delays[0] * 3600e3;
}
async function sendStep(u) {
  const B = T[u.branch];
  const m = B.chain[u.step];
  if (!m) { u.next = null; return; }
  const kb = m.lead_button ? [[{ text: m.lead_button, callback_data: 'lead:' + u.branch }]] : null;
  const r = await send(u.id, m.text, kb);
  if (!r.ok && r.error_code === 403) { u.stopped = true; u.next = null; return; }
  u.step += 1;
  u.next = u.step < B.chain.length ? u.giftAt + B.delays[u.step] * 3600e3 : null;
  if (u.next !== null && u.next < now) u.next = now + 3600e3; // не слать два подряд
}
function stats() {
  const by = {};
  for (const u of Object.values(st.users)) {
    const k = (u.branch || 'без ветки') + ' / ' + (u.source || 'без метки');
    by[k] = by[k] || { start: 0, sub: 0, done: 0, lead: 0, stop: 0 };
    by[k].start++; if (u.subscribed) by[k].sub++;
    if (u.branch && u.step >= T[u.branch].chain.length) by[k].done++;
    if (u.lead) by[k].lead++; if (u.stopped) by[k].stop++;
  }
  const rows = Object.entries(by).sort((a, b) => b[1].start - a[1].start)
    .map(([k, v]) => `${k}: старт ${v.start}, подписка ${v.sub}, цепочка до конца ${v.done}, заявки ${v.lead}, отписались ${v.stop}`);
  return '📊 <b>Статистика бота</b>\n\n' + (rows.join('\n') || 'Пока никого.') +
    `\n\nВсего: ${Object.keys(st.users).length}`;
}

// 1. Новые сообщения и нажатия кнопок
const up = await api('getUpdates', { offset: st.offset, timeout: S.poll_seconds, allowed_updates: ['message', 'callback_query'] }, (S.poll_seconds + 15) * 1000);
if (up.ok) {
  for (const x of up.result) {
    st.offset = x.update_id + 1;
    try {
      if (x.message) {
        const m = x.message, from = m.from || {}, id = from.id;
        if (!id || m.chat.type !== 'private') continue;
        const text = (m.text || '').trim();
        // команды владельца
        if (isOwner(id) && m.document && /^gift_/.test(m.caption || '')) {
          const b = (m.caption || '').replace('gift_', '').trim();
          if (S.channels[b]) { st.gifts[b] = m.document.file_id; await send(id, `✅ Подарок для ветки ${b} сохранён.`); }
          else await send(id, `Не знаю ветку «${b}». Есть: ${branches.join(', ')}`);
          continue;
        }
        if (isOwner(id) && text === '/stats') { await send(id, stats()); continue; }
        if (isOwner(id) && text.startsWith('/demo')) {
          const b = text.split(' ')[1];
          if (!S.channels[b]) { await send(id, 'Напишите /demo cvai или /demo ryadom'); continue; }
          const B = T[b];
          await send(id, B.welcome);
          if (st.gifts[b]) await api('sendDocument', { chat_id: id, document: st.gifts[b], caption: B.gift_caption });
          for (const c of B.chain) await send(id, c.text, c.lead_button ? [[{ text: c.lead_button, callback_data: 'noop' }]] : null);
          continue;
        }
        if (text === '/stop') {
          const u = st.users[id]; if (u) { u.stopped = true; u.next = null; }
          await send(id, T.stop); continue;
        }
        if (text.startsWith('/start')) {
          const payload = clean(text.split(' ')[1]);
          let u = st.users[id];
          if (!u) {
            u = st.users[id] = { id, name: from.first_name || '', username: from.username || '', source: payload || '', branch: branchOf(payload), created: now, step: 0, subscribed: false, giftAt: null, next: null, stopped: false, lead: false };
          } else {
            u.stopped = false;
            if (!u.branch && branchOf(payload)) u.branch = branchOf(payload);
          }
          if (!u.branch) {
            await send(id, T.choose, [[
              { text: T.choose_buttons.cvai, callback_data: 'branch:cvai' },
              { text: T.choose_buttons.ryadom, callback_data: 'branch:ryadom' }
            ]]);
          } else if (u.subscribed && u.giftAt) {
            await giveGift(Object.assign({}, u)); // повторно прислать подарок, не сбивая цепочку
          } else {
            await welcome(u);
          }
          continue;
        }
      }
      if (x.callback_query) {
        const q = x.callback_query, id = q.from.id, data = q.data || '';
        let u = st.users[id];
        if (!u) u = st.users[id] = { id, name: q.from.first_name || '', username: q.from.username || '', source: '', branch: null, created: now, step: 0, subscribed: false, giftAt: null, next: null, stopped: false, lead: false };
        if (data.startsWith('branch:')) {
          const b = data.split(':')[1]; if (S.channels[b]) { u.branch = b; await welcome(u); }
          await api('answerCallbackQuery', { callback_query_id: q.id });
        } else if (data.startsWith('check:')) {
          const b = data.split(':')[1]; if (S.channels[b] && !u.branch) u.branch = b;
          const r = await api('getChatMember', { chat_id: S.channels[u.branch], user_id: id });
          const ok = r.ok && (['member', 'administrator', 'creator'].includes(r.result.status) || (r.result.status === 'restricted' && r.result.is_member));
          if (ok) {
            await api('answerCallbackQuery', { callback_query_id: q.id, text: 'Спасибо! Отправляю подарок.' });
            if (!u.subscribed) await giveGift(u); else await giveGift(Object.assign({}, u));
          } else {
            await api('answerCallbackQuery', { callback_query_id: q.id, text: T.not_subscribed, show_alert: true });
            if (!r.ok) log.push('getChatMember: ' + r.description);
          }
        } else if (data.startsWith('lead:')) {
          const b = data.split(':')[1];
          await api('answerCallbackQuery', { callback_query_id: q.id });
          if (!u.lead) {
            u.lead = true; u.leadAt = now;
            const who = (u.name || '') + (u.username ? ' @' + u.username : ` (id ${id}, без username)`);
            await send(S.owner_id, `🔥 <b>Новая заявка</b>\nКто: ${who}\nВетка: ${b}\nОткуда: ${u.source || 'без метки'}\nНаписать: <a href="tg://user?id=${id}">открыть чат</a>`);
          }
          await send(id, T[b].lead_thanks);
        } else {
          await api('answerCallbackQuery', { callback_query_id: q.id });
        }
      }
    } catch (e) { log.push('ошибка: ' + String(e.message || e)); }
  }
} else {
  log.push('getUpdates: ' + up.description);
}

// 2. Цепочка сообщений по расписанию
const h = mskHour(now);
let sent = 0;
if (h >= S.send_from_hour && h < S.send_to_hour) {
  for (const u of Object.values(st.users)) {
    if (sent >= S.max_per_run) break;
    if (u.stopped || !u.subscribed || !u.next || u.next > now || !u.branch) continue;
    await sendStep(u); sent++;
  }
}

return [{ json: { updates: up.ok ? up.result.length : 'ошибка', chain_sent: sent, users: Object.keys(st.users).length, log } }];
