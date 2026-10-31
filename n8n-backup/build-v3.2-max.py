import json, copy, sys
src, dst = sys.argv[1], sys.argv[2]
w = json.load(open(src))
w['name'] = 'Публикация из GitHub в Telegram и MAX v3.2'
N = {n['name']: n for n in w['nodes']}

# --- версии в записях архива
for nm in ('Итог', 'Ошибка'):
    N[nm]['parameters']['jsCode'] = N[nm]['parameters']['jsCode'].replace("workflow: 'v3.1'", "workflow: 'v3.2'").replace("'n8n-server v3.1'", "'n8n-server v3.2'")

# --- Настройки: блок MAX (по умолчанию выключен)
code = N['Настройки']['parameters']['jsCode']
code = code.replace("  committer:", """  max: {
    enabled: false,             // true — дублировать посты в MAX; false — работает как v3.1
    api: 'https://platform-api2.max.ru',
    channels: {                 // канал Telegram → chat_id канала в MAX (число). Пусто — в MAX не дублируется
      '@chelovek_vs_ai': '',
      '@ryadom_repetitor': ''
    },
    text_limit: 4000            // предел текста MAX
  },
  committer:""")
N['Настройки']['parameters']['jsCode'] = code

def gh_headers():
    return copy.deepcopy(N['Записать в GitHub']['parameters']['headerParameters'])

MAX_CRED = {"httpHeaderAuth": {"id": "", "name": "MAX bot"}}
X0, Y0 = 2760, 0

nodes = []
def add(name, typ, ver, pos, params, **extra):
    n = {"parameters": params, "name": name, "type": typ, "typeVersion": ver, "position": pos, "id": "max-" + str(len(nodes) + 1)}
    n.update(extra); nodes.append(n); return n

PREP = r"""// === MAX: подготовить — продублировать опубликованный в Telegram пост в MAX ===
// Работает только после успешной отправки в Telegram (узел «Итог» записал sent/).
// Ошибка MAX не трогает Telegram: она пишется в failed-max/ и приходит вам в личку.
// В файле поста можно указать: "max": false — не дублировать; "max_text_html" — свой текст для MAX.
const cfg = $('Настройки').first().json;
const m = cfg.max || {};
if (!m.enabled) return [];
let done;
try { done = $('Итог').first().json; } catch (e) { return []; }      // Итог не выполнялся — это была ошибка
if (!done || !String(done.path || '').startsWith('sent/')) return [];
const post = $('Скачать пост').first().json;
const tg = $('Подготовить').first().json;
if (post.max === false) return [];
const maxChat = String((m.channels || {})[tg.chat_id] || '').trim();
if (!maxChat) return [];

// Telegram HTML → MAX HTML: оставляем только теги, которые MAX понимает, остальные снимаем (текст внутри сохраняется)
const KEEP = { b: 'b', strong: 'b', i: 'i', em: 'i', u: 'u', ins: 'u', s: 's', strike: 's', del: 's',
               code: 'code', pre: 'pre', a: 'a', blockquote: 'blockquote', mark: 'mark' };
function toMax(html) {
  return String(html || '').replace(/<\s*(\/?)\s*([a-zA-Z][\w-]*)([^>]*)>/g, (all, close, tag, attrs) => {
    const t = KEEP[tag.toLowerCase()];
    if (!t) return '';
    if (close) return '</' + t + '>';
    if (t === 'a') {
      const h = attrs.match(/href\s*=\s*("([^"]*)"|'([^']*)')/i);
      return h ? '<a href="' + (h[2] !== undefined ? h[2] : h[3]) + '">' : '';
    }
    return '<' + t + '>';
  });
}
const text = toMax(post.max_text_html || post.text_html || post.text || '');
const plain = text.replace(/<[^>]+>/g, '');
const base = { id: tg.id, file: tg.file, channel: tg.channel, tg_chat_id: tg.chat_id, max_chat_id: maxChat,
               tg_link: (() => { try { return JSON.parse(Buffer.from(done.content, 'base64').toString()).link; } catch (e) { return null; } })() };
const LIMIT = m.text_limit || 4000;
if (text.length > LIMIT) {
  return [{ json: { ...base, upload: false, skip_reason: 'Текст для MAX длиннее ' + LIMIT + ' знаков (' + text.length + ' с тегами, ' + plain.length + ' без). Сократите или задайте max_text_html.' } }];
}
const body = { text, format: 'html' };
if (post.image_base64) {
  return [{ json: { ...base, upload: true, body },
            binary: { photo: { data: post.image_base64, mimeType: post.image_mime || 'image/jpeg', fileName: (tg.id || 'post') + '.jpg' } } }];
}
if (post.img) body.attachments = [{ type: 'image', payload: { url: String(post.img) } }];
return [{ json: { ...base, upload: false, body } }];
"""

FILE = r"""// === MAX: файл к загрузке — соединить адрес загрузки и картинку ===
const prep = $('MAX: подготовить').first();
const r = $input.first().json;
if (!r.url) throw new Error('MAX не выдал адрес загрузки: ' + JSON.stringify(r).slice(0, 300));
return [{ json: { upload_url: r.url, upload_token: r.token || null }, binary: prep.binary }];
"""

ATTACH = r"""// === MAX: вложение — токен загруженной картинки в тело сообщения ===
const prep = $('MAX: подготовить').first().json;
const r = $input.first().json || {};
const t = $('MAX: файл к загрузке').first().json;
let payload = null;
if (r.photos && Object.keys(r.photos).length) payload = { photos: r.photos };
else if (r.token) payload = { token: r.token };
else if (t.upload_token) payload = { token: t.upload_token };
if (!payload) throw new Error('MAX не вернул токен картинки: ' + JSON.stringify(r).slice(0, 300));
return [{ json: { ...prep, body: { ...prep.body, attachments: [{ type: 'image', payload }] } } }];
"""

def CHECK(attempt, final):
    return r"""// === MAX: проверить ответ (попытка %d) ===
// Повтор только при attachment.not.ready — тогда пост точно не опубликован. Любая другая ошибка — без повтора, чтобы не было дубля.
const r = $input.first().json;
const status = r.statusCode;
let b = r.body;
if (typeof b === 'string') { try { b = JSON.parse(b); } catch (e) {} }
b = b || {};
const prev = $('MAX: тело').first().json;
if (status >= 200 && status < 300 && b.message) {
  const msg = b.message;
  return [{ json: { ...prev, route: 'ok', attempts: %d, max_mid: (msg.body && msg.body.mid) || null, max_url: msg.url || null } }];
}
const code = b.code || '';
const err = 'HTTP ' + status + ': ' + (code ? code + ' — ' : '') + (b.message || JSON.stringify(b)).toString().slice(0, 300);
if (code === 'attachment.not.ready' && !%s) return [{ json: { ...prev, route: 'retry', attempts: %d, error: err } }];
return [{ json: { ...prev, route: 'fail', attempts: %d, error: err } }];
""" % (attempt, attempt, 'true' if final else 'false', attempt, attempt)

BODY = r"""// === MAX: тело — единая точка перед отправкой (с картинкой или без) ===
const j = $input.first().json;
if (j.skip_reason) return [{ json: { ...j, route: 'fail', attempts: 0, error: j.skip_reason } }];
return [{ json: j }];
"""

RESULT = r"""// === MAX: итог — запись в sent-max/ ===
const j = $input.first().json;
const record = { id: j.id, channel: j.channel, tg_chat_id: j.tg_chat_id, tg_link: j.tg_link,
  max_chat_id: j.max_chat_id, max_mid: j.max_mid, max_url: j.max_url, attempts: j.attempts,
  file: j.file, published_at: new Date().toISOString(), published_by: 'n8n-server', workflow: 'v3.2' };
return [{ json: { path: 'sent-max/' + j.id + '.json', message: 'sent-max ' + j.id + ' (n8n)',
  content: Buffer.from(JSON.stringify(record, null, 2)).toString('base64') } }];
"""

FAIL = r"""// === MAX: ошибка — пост целиком в failed-max/, сообщение вам. Telegram уже опубликован и не повторяется ===
const j = $input.first().json;
const original = $('Скачать пост').first().json;
const record = { ...original, failed_max: { at: new Date().toISOString(), error: String(j.error || 'неизвестная ошибка').slice(0, 500),
  attempts: j.attempts || 0, max_chat_id: j.max_chat_id, tg_link: j.tg_link, by: 'n8n-server v3.2',
  hint: 'В Telegram пост уже вышел. В MAX его нет: опубликуйте вручную или после исправления причины.' } };
const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 13);
return [{ json: { path: 'failed-max/' + j.id + '-' + stamp + '.json', message: 'failed-max ' + j.id + ' (n8n)',
  content: Buffer.from(JSON.stringify(record, null, 2)).toString('base64'),
  notify: 'MAX: пост не опубликован — ' + j.id + '\nПричина: ' + String(j.error || '').slice(0, 300) + '\nTelegram: ' + (j.tg_link || 'опубликован') + '\nФайл в failed-max/.' } }];
"""

api = "$('Настройки').first().json.max.api"
def send_node(name, pos):
    return add(name, "n8n-nodes-base.httpRequest", 4.2, pos, {
        "method": "POST",
        "url": "={{ " + api + " + '/messages?chat_id=' + encodeURIComponent($('MAX: тело').first().json.max_chat_id) }}",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($('MAX: тело').first().json.body) }}",
        "options": {"response": {"response": {"fullResponse": True, "neverError": True, "responseFormat": "json"}}, "timeout": 30000}
    }, credentials=copy.deepcopy(MAX_CRED), onError="continueRegularOutput")

def code(name, pos, js): return add(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})
def iff(name, pos, left, op):
    return add(name, "n8n-nodes-base.if", 2, pos, {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
        "conditions": [{"leftValue": left, "rightValue": "", "operator": op}], "combinator": "and"}, "options": {}})
BOOL_TRUE = {"type": "boolean", "operation": "true", "singleValue": True}
def eq(v): return {"type": "string", "operation": "equals"}, v

code('MAX: подготовить', [X0, Y0], PREP)
iff('MAX: картинка в файле?', [X0+220, Y0], "={{ $json.upload }}", BOOL_TRUE)
add('MAX: адрес загрузки', "n8n-nodes-base.httpRequest", 4.2, [X0+440, Y0-180], {
    "method": "POST", "url": "={{ " + api + " + '/uploads?type=image' }}",
    "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth", "options": {"timeout": 30000}},
    credentials=copy.deepcopy(MAX_CRED), onError="continueErrorOutput")
code('MAX: файл к загрузке', [X0+660, Y0-180], FILE)
add('MAX: загрузить файл', "n8n-nodes-base.httpRequest", 4.2, [X0+880, Y0-180], {
    "method": "POST", "url": "={{ $json.upload_url }}",
    "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
    "sendBody": True, "contentType": "multipart-form-data",
    "bodyParameters": {"parameters": [{"parameterType": "formBinaryData", "name": "data", "inputDataFieldName": "photo"}]},
    "options": {"timeout": 120000}}, credentials=copy.deepcopy(MAX_CRED), onError="continueErrorOutput")
code('MAX: вложение', [X0+1100, Y0-180], ATTACH)
add('MAX: пауза на обработку', "n8n-nodes-base.wait", 1.1, [X0+1320, Y0-180], {"amount": 3, "unit": "seconds"}, webhookId="max-wait-1")
code('MAX: тело', [X0+1540, Y0], BODY)
add('MAX: отправлять?', "n8n-nodes-base.if", 2, [X0+1760, Y0], {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
    "conditions": [{"leftValue": "={{ $json.route }}", "rightValue": "fail", "operator": {"type": "string", "operation": "notEquals"}}], "combinator": "and"}, "options": {}})
send_node('MAX: отправить', [X0+1980, Y0-100])
code('MAX: проверить', [X0+2200, Y0-100], CHECK(1, False))
add('MAX: результат', "n8n-nodes-base.switch", 3, [X0+2420, Y0-100], {"rules": {"values": [
    {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
      "conditions": [{"leftValue": "={{ $json.route }}", "rightValue": r, "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"},
     "renameOutput": True, "outputKey": k} for r, k in (("ok", "Опубликовано"), ("retry", "Повторить"), ("fail", "Ошибка"))]}, "options": {}})
add('MAX: пауза перед повтором', "n8n-nodes-base.wait", 1.1, [X0+2640, Y0-60], {"amount": 10, "unit": "seconds"}, webhookId="max-wait-2")
send_node('MAX: отправить повторно', [X0+2860, Y0-60])
code('MAX: проверить повтор', [X0+3080, Y0-60], CHECK(2, True))
iff('MAX: повтор удался?', [X0+3300, Y0-60], "={{ $json.route === 'ok' }}", BOOL_TRUE)
code('MAX: итог', [X0+3520, Y0-260], RESULT)
code('MAX: ошибка', [X0+3520, Y0+160], FAIL)
code('MAX: ошибка загрузки', [X0+1100, Y0+40], r"""// === MAX: ошибка загрузки картинки ===
const prep = $('MAX: подготовить').first().json;
const e = $input.first().json.error;
return [{ json: { ...prep, route: 'fail', attempts: 0, error: 'Загрузка картинки: ' + (e ? (e.message || JSON.stringify(e)) : 'неизвестно') } }];
""")
wr = copy.deepcopy(N['Записать в GitHub']); wr['name'] = 'MAX: записать в GitHub'; wr['position'] = [X0+3760, Y0-60]; wr['id'] = 'max-write'
wr['onError'] = 'continueRegularOutput'; nodes.append(wr)
ntf = copy.deepcopy(N['Сообщить мне']); ntf['name'] = 'MAX: сообщить мне'; ntf['position'] = [X0+3760, Y0+260]; ntf['id'] = 'max-notify'; nodes.append(ntf)

w['nodes'] += nodes
C = w['connections']
def link(a, b, out=0):
    C.setdefault(a, {"main": []})
    while len(C[a]["main"]) <= out: C[a]["main"].append([])
    C[a]["main"][out].append({"node": b, "type": "main", "index": 0})
link('Убрать из outbox', 'MAX: подготовить')
link('MAX: подготовить', 'MAX: картинка в файле?')
link('MAX: картинка в файле?', 'MAX: адрес загрузки', 0)
link('MAX: картинка в файле?', 'MAX: тело', 1)
link('MAX: адрес загрузки', 'MAX: файл к загрузке', 0); link('MAX: адрес загрузки', 'MAX: ошибка загрузки', 1)
link('MAX: файл к загрузке', 'MAX: загрузить файл')
link('MAX: загрузить файл', 'MAX: вложение', 0); link('MAX: загрузить файл', 'MAX: ошибка загрузки', 1)
link('MAX: вложение', 'MAX: пауза на обработку')
link('MAX: пауза на обработку', 'MAX: тело')
link('MAX: ошибка загрузки', 'MAX: ошибка')
link('MAX: тело', 'MAX: отправлять?')
link('MAX: отправлять?', 'MAX: отправить', 0); link('MAX: отправлять?', 'MAX: ошибка', 1)
link('MAX: отправить', 'MAX: проверить')
link('MAX: проверить', 'MAX: результат')
link('MAX: результат', 'MAX: итог', 0); link('MAX: результат', 'MAX: пауза перед повтором', 1); link('MAX: результат', 'MAX: ошибка', 2)
link('MAX: пауза перед повтором', 'MAX: отправить повторно')
link('MAX: отправить повторно', 'MAX: проверить повтор')
link('MAX: проверить повтор', 'MAX: повтор удался?')
link('MAX: повтор удался?', 'MAX: итог', 0); link('MAX: повтор удался?', 'MAX: ошибка', 1)
link('MAX: итог', 'MAX: записать в GitHub')
link('MAX: ошибка', 'MAX: записать в GitHub'); link('MAX: ошибка', 'MAX: сообщить мне')
json.dump(w, open(dst, 'w'), ensure_ascii=False, indent=2)
print(len(w['nodes']), 'nodes')
