#!/usr/bin/env python3
"""Публикатор MAX на российском сервере. Версия 1.1 (01.11.2026): разовые команды commands-max/.

Берёт из GitHub посты, которые уже вышли в Telegram, и публикует тот же пост в канал MAX:
- каналы n8n: sent/{id}.json, текст и картинка — из истории outbox/;
- канал НППЦП @nscspr: scheduled-ucp-done/{id}.json (текст и путь к картинке в commands-ucp-media/),
  id в sent-max/ с приставкой ucp-. Опросы в MAX не переносятся.
Telegram этот скрипт не трогает: его по-прежнему публикует n8n в Нидерландах.

- разовые команды: commands-max/{имя}.json — отправить с кнопками и закрепить, исправить, удалить.

Результат: sent-max/{id}.json при успехе, failed-max/{id}-{время}.json при ошибке.
Защита от дублей: локальная отметка в STATE_DIR ставится сразу после ответа MAX,
ещё до записи в GitHub.

Запуск:  max_publisher.py            — один проход (его раз в 2 минуты вызывает systemd)
         max_publisher.py --dry-run  — показать, что ушло бы в MAX, ничего не отправляя
         max_publisher.py --check    — проверить доступ к GitHub и MAX, вывести каналы бота
Только стандартная библиотека Python, без pip.
"""
import base64, datetime as dt, fcntl, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request, uuid

HOME = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.environ.get('MAXPUB_CONFIG', os.path.join(HOME, 'config.json')), encoding='utf-8'))
STATE = os.environ.get('MAXPUB_STATE', CFG.get('state_dir', '/var/lib/max-publisher'))
GH_API = CFG.get('github_api', 'https://api.github.com')
MAX_API = CFG.get('max_api', 'https://platform-api2.max.ru')
TG_API = CFG.get('telegram_api', 'https://api.telegram.org')
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
MAX_TOKEN = os.environ.get('MAX_TOKEN', '')
TG_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
DRY = '--dry-run' in sys.argv

ctx = ssl.create_default_context()
if CFG.get('extra_ca') and os.path.exists(CFG['extra_ca']):
    ctx.load_verify_locations(CFG['extra_ca'])          # сертификат Минцифры для MAX


def log(*a):
    print(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


def http(method, url, headers=None, body=None, timeout=30):
    """Возвращает (статус, байты). Сетевые сбои — статус 0."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # тайм-аут, DNS, TLS
        return 0, str(e).encode()


def js(b):
    try:
        return json.loads(b or b'{}')
    except Exception:
        return {'raw': (b or b'')[:300].decode('utf-8', 'replace')}


# ---------- GitHub ----------
def gh(method, path, body=None, raw=False):
    h = {'Authorization': 'Bearer ' + GH_TOKEN, 'X-GitHub-Api-Version': '2022-11-28',
         'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
         'User-Agent': 'max-publisher'}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    return http(method, GH_API + '/repos/' + CFG['repo'] + path, h, data, timeout=60)


def gh_list(folder):
    s, b = gh('GET', '/contents/' + folder)
    if s == 404:
        return []
    if s != 200:
        raise RuntimeError('GitHub %s: HTTP %s %s' % (folder, s, b[:200]))
    return [x['name'] for x in js(b) if x.get('type') == 'file']


def gh_put(path, obj, message):
    content = base64.b64encode(json.dumps(obj, ensure_ascii=False, indent=2).encode()).decode()
    body = {'message': message, 'content': content, 'committer': CFG['committer'], 'author': CFG['committer']}
    s, b = gh('PUT', '/contents/' + urllib.parse.quote(path), body)
    if s == 422 and b'sha' in b:  # файл уже есть — значит, запись уже сделана
        return True
    return s in (200, 201)


def original_post(file):
    """Текст поста из истории: n8n удаляет его из outbox/ после публикации."""
    q = '/commits?per_page=10&path=' + urllib.parse.quote('outbox/' + file)
    s, b = gh('GET', q)
    if s != 200:
        raise RuntimeError('история outbox/%s: HTTP %s' % (file, s))
    for c in js(b):
        s2, b2 = gh('GET', '/contents/' + urllib.parse.quote('outbox/' + file) + '?ref=' + c['sha'], raw=True)
        if s2 == 200:
            return json.loads(b2)
    raise RuntimeError('не найден текст поста outbox/' + file + ' в истории GitHub')


def repo_file_b64(path):
    s, b = gh('GET', '/contents/' + urllib.parse.quote(path), raw=True)
    if s != 200:
        raise RuntimeError('файл %s: HTTP %s' % (path, s))
    return base64.b64encode(b).decode()


# ---------- MAX ----------
KEEP = {'b': 'b', 'strong': 'b', 'i': 'i', 'em': 'i', 'u': 'u', 'ins': 'u', 's': 's', 'strike': 's', 'del': 's',
        'code': 'code', 'pre': 'pre', 'a': 'a', 'blockquote': 'blockquote', 'mark': 'mark'}


def to_max_html(html):
    """Теги Telegram, которых нет в MAX (спойлер, свои эмодзи, span), снимаются; текст остаётся."""
    def rep(m):
        close, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        t = KEEP.get(tag)
        if not t:
            return ''
        if close:
            return '</%s>' % t
        if t == 'a':
            h = re.search(r'href\s*=\s*("([^"]*)"|\'([^\']*)\')', attrs, re.I)
            return '<a href="%s">' % (h.group(2) if h.group(2) is not None else h.group(3)) if h else ''
        return '<%s>' % t
    return re.sub(r'<\s*(/?)\s*([a-zA-Z][\w-]*)([^>]*)>', rep, str(html or ''))


def max_req(method, path, body=None):
    h = {'Authorization': MAX_TOKEN, 'User-Agent': 'max-publisher'}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode(); h['Content-Type'] = 'application/json'
    s, b = http(method, MAX_API + path, h, data)
    return s, js(b)


def upload_image(b64, mime):
    s, r = max_req('POST', '/uploads?type=image')
    if s != 200 or not r.get('url'):
        raise RuntimeError('MAX не выдал адрес загрузки: HTTP %s %s' % (s, json.dumps(r, ensure_ascii=False)[:200]))
    boundary = uuid.uuid4().hex
    ext = 'png' if 'png' in (mime or '') else 'jpg'
    body = (('--%s\r\nContent-Disposition: form-data; name="data"; filename="post.%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (boundary, ext, mime or 'image/jpeg')).encode()
            + base64.b64decode(b64) + ('\r\n--%s--\r\n' % boundary).encode())
    s2, b2 = http('POST', r['url'], {'Authorization': MAX_TOKEN, 'Content-Type': 'multipart/form-data; boundary=' + boundary},
                  body, timeout=120)
    u = js(b2)
    if s2 not in (200, 201):
        raise RuntimeError('загрузка картинки: HTTP %s %s' % (s2, json.dumps(u, ensure_ascii=False)[:200]))
    if u.get('photos'):
        return {'photos': u['photos']}
    if u.get('token') or r.get('token'):
        return {'token': u.get('token') or r.get('token')}
    raise RuntimeError('MAX не вернул токен картинки: ' + json.dumps(u, ensure_ascii=False)[:200])


def send(chat_id, body):
    """Повтор один раз и только при attachment.not.ready: в этом случае поста точно нет."""
    path = '/messages?chat_id=' + urllib.parse.quote(str(chat_id))
    for attempt in (1, 2):
        s, r = max_req('POST', path, body)
        if 200 <= s < 300 and r.get('message'):
            m = r['message']
            return {'ok': True, 'attempts': attempt, 'max_mid': (m.get('body') or {}).get('mid'), 'max_url': m.get('url')}
        code = r.get('code', '')
        err = 'HTTP %s: %s%s' % (s, (code + ' — ') if code else '', str(r.get('message') or r)[:300])
        if code == 'attachment.not.ready' and attempt == 1:
            time.sleep(CFG.get('retry_seconds', 10)); continue
        return {'ok': False, 'attempts': attempt, 'error': err}


# ---------- уведомление ----------
def notify(text):
    """Сначала в личный диалог с ботом в MAX (из России доступен надёжно), затем в Telegram."""
    if DRY:
        return
    sent = False
    if CFG.get('admin_max_user_id'):
        s, r = max_req('POST', '/messages?user_id=' + urllib.parse.quote(str(CFG['admin_max_user_id'])), {'text': text})
        sent = 200 <= s < 300
        if not sent:
            log('уведомление в MAX не ушло: HTTP %s %s' % (s, json.dumps(r, ensure_ascii=False)[:150]))
    if TG_TOKEN and CFG.get('admin_chat_id'):
        body = json.dumps({'chat_id': CFG['admin_chat_id'], 'text': text}).encode()
        s, _ = http('POST', TG_API + '/bot' + TG_TOKEN + '/sendMessage', {'Content-Type': 'application/json'}, body, timeout=15)
        sent = sent or s == 200
        if s != 200:
            log('уведомление в Telegram не ушло (HTTP %s)' % s)
    if not sent:
        log('уведомление не доставлено; ошибка записана в failed-max/')


# ---------- локальное состояние ----------
def mark_path(pid):
    return os.path.join(STATE, 'done', pid + '.json')


def mark(pid, rec):
    if DRY:
        return
    os.makedirs(os.path.join(STATE, 'done'), exist_ok=True)
    tmp = mark_path(pid) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, mark_path(pid))


def load_mark(pid):
    try:
        return json.load(open(mark_path(pid), encoding='utf-8'))
    except FileNotFoundError:
        return None


def push_pending():
    """Записи, которые не удалось сохранить в GitHub в прошлый раз."""
    d = os.path.join(STATE, 'done')
    if not os.path.isdir(d):
        return
    for n in sorted(os.listdir(d)):
        if not n.endswith('.json'):
            continue
        m = json.load(open(os.path.join(d, n), encoding='utf-8'))
        if m.get('pushed') or not m.get('gh_path'):
            continue
        if gh_put(m['gh_path'], m['gh_record'], m['gh_message']):
            m['pushed'] = True; mark(m['id'], m); log('дозаписано в GitHub:', m['gh_path'])


def id_time(pid):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', pid)
    return dt.datetime(*map(int, m.groups())) if m else None


# ---------- основной проход ----------
def publish(pid, base, items, published_at, now, notify_link):
    """items: [{'text': html, 'image_b64'|'image_url': ..., 'mime': ...}] — отправляются по порядку."""
    limit = CFG.get('text_limit', 4000)
    error, results = None, []
    if (now - published_at).total_seconds() > CFG.get('max_lag_hours', 12) * 3600:
        error = 'Опоздание: в Telegram пост вышел %s, позже %s ч. В MAX не отправлен, чтобы не выпускать пачку старых постов.' % (published_at.isoformat(), CFG.get('max_lag_hours', 12))
    for it in items:
        if not error and len(it['text']) > limit:
            error = 'Текст для MAX длиннее %s знаков (%s). Сократите или задайте max_text_html.' % (limit, len(it['text']))
    if DRY:
        for it in items:
            log('[dry-run]', pid, '→ MAX', base['max_chat_id'], '| картинка:', 'файл' if it.get('image_b64') or it.get('image_path') else ('ссылка' if it.get('image_url') else 'нет'),
                '| знаков:', len(it['text']), '| ошибка:' if error else '', error or '')
        return
    if not error:
        for it in items:
            body = {'text': it['text'], 'format': 'html'}
            try:
                b64 = it.get('image_b64') or (repo_file_b64(it['image_path']) if it.get('image_path') else None)
                if b64:
                    body['attachments'] = [{'type': 'image', 'payload': upload_image(b64, it.get('mime'))}]
                    time.sleep(CFG.get('after_upload_seconds', 3))
                elif it.get('image_url'):
                    body['attachments'] = [{'type': 'image', 'payload': {'url': it['image_url']}}]
                r = send(base['max_chat_id'], body)
            except Exception as e:
                r = {'ok': False, 'error': str(e)[:500]}
            if not r['ok']:
                error = r['error'] + (' (до ошибки отправлено частей: %s)' % len(results) if results else '')
                break
            results.append(r); time.sleep(1)
    stamp = now.strftime('%Y%m%dT%H%M')
    if not error:
        record = {**base, 'max_mid': results[-1]['max_mid'], 'max_url': results[-1]['max_url'],
                  'parts': [{'max_mid': r['max_mid'], 'max_url': r['max_url'], 'attempts': r['attempts']} for r in results],
                  'published_at': now.isoformat(), 'published_by': 'max-publisher (Россия)'}
        m = {'id': pid, 'status': 'ok', 'pushed': False, 'gh_path': 'sent-max/' + pid + '.json',
             'gh_record': record, 'gh_message': 'sent-max ' + pid + ' (ru)'}
        log(pid, '→ MAX опубликован', results[-1].get('max_url') or results[-1].get('max_mid'))
    else:
        record = {**base, 'items': [{k: v for k, v in it.items() if k != 'image_b64'} for it in items],
                  'failed_max': {'at': now.isoformat(), 'error': error, 'by': 'max-publisher (Россия)',
                                 'hint': 'В Telegram пост уже вышел. В MAX его нет: опубликуйте вручную или удалите отметку на сервере и запустите заново.'}}
        m = {'id': pid, 'status': 'failed', 'pushed': False, 'gh_path': 'failed-max/%s-%s.json' % (pid, stamp),
             'gh_record': record, 'gh_message': 'failed-max ' + pid + ' (ru)'}
        log(pid, '→ ошибка MAX:', error)
        notify('MAX: пост не опубликован — %s\nПричина: %s\nTelegram: %s\nФайл в failed-max/.' % (pid, error[:300], notify_link or 'опубликован'))
    mark(pid, m)                      # сначала локально — это защита от дубля
    if gh_put(m['gh_path'], m['gh_record'], m['gh_message']):
        m['pushed'] = True; mark(pid, m)
    else:
        log('GitHub не принял запись, повторю в следующий проход:', m['gh_path'])


def parse_time(v, default):
    try:
        return dt.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except Exception:
        return default


def process_main(file, now):
    """Каналы n8n (@chelovek_vs_ai, @ryadom_repetitor): sent/{id}.json + текст из истории outbox/."""
    pid = file[:-5]
    s, b = gh('GET', '/contents/sent/' + urllib.parse.quote(file), raw=True)
    if s != 200:
        raise RuntimeError('sent/%s: HTTP %s' % (file, s))
    rec = json.loads(b)
    max_chat = str((CFG.get('channels') or {}).get(rec.get('chat_id'), '')).strip()
    if not max_chat:
        mark(pid, {'id': pid, 'status': 'not-mapped', 'pushed': True}); return
    base = {'id': pid, 'channel': rec.get('channel'), 'tg_chat_id': rec.get('chat_id'), 'tg_link': rec.get('link'),
            'max_chat_id': max_chat, 'file': rec.get('file') or file}
    post = original_post(rec.get('file') or file)
    if post.get('max') is False:
        mark(pid, {'id': pid, 'status': 'max-false', 'pushed': True}); log(pid, '— "max": false, пропуск'); return
    it = {'text': to_max_html(post.get('max_text_html') or post.get('text_html') or post.get('text') or '')}
    if post.get('image_base64'):
        it.update(image_b64=post['image_base64'], mime=post.get('image_mime'))
    elif post.get('img'):
        it['image_url'] = post['img']
    publish(pid, base, [it], parse_time(rec.get('published_at'), now), now, rec.get('link'))


def process_ucp(file, now):
    """Канал НППЦП (@nscspr): scheduled-ucp-done/{id}.json с текстом и путём к картинке.
    Опросы в MAX не переносятся (в Bot API MAX их нет) — пост из одних опросов пропускается."""
    pid = 'ucp-' + file[:-5]
    max_chat = str((CFG.get('channels') or {}).get('@nscspr', '')).strip()
    if not max_chat:
        return                                   # канал не подключён — ничего не отмечаем, подключить можно позже
    s, b = gh('GET', '/contents/scheduled-ucp-done/' + urllib.parse.quote(file), raw=True)
    if s != 200:
        raise RuntimeError('scheduled-ucp-done/%s: HTTP %s' % (file, s))
    d = json.loads(b)
    if d.get('max') is False:
        mark(pid, {'id': pid, 'status': 'max-false', 'pushed': True}); log(pid, '— "max": false, пропуск'); return
    res = d.get('results') or []
    if not res or not all(r.get('ok') for r in res):
        mark(pid, {'id': pid, 'status': 'tg-not-ok', 'pushed': True}); log(pid, '— в Telegram не вышел целиком, в MAX не дублируется'); return
    items = []
    for st in d.get('steps') or []:
        if st.get('action') == 'photo':
            items.append({'text': to_max_html(st.get('max_text_html') or st.get('text_html') or ''), 'image_path': st.get('image'),
                          'mime': 'image/png' if str(st.get('image', '')).endswith('.png') else 'image/jpeg'})
        elif st.get('action') in ('message', 'text'):
            items.append({'text': to_max_html(st.get('max_text_html') or st.get('text_html') or st.get('text') or '')})
    if not items:
        mark(pid, {'id': pid, 'status': 'no-max-content', 'pushed': True}); log(pid, '— только опрос, в MAX не дублируется'); return
    mid = (res[-1] or {}).get('message_id')
    link = 'https://t.me/nscspr/%s' % mid if mid else None
    base = {'id': pid, 'channel': 'НППЦП', 'tg_chat_id': '@nscspr', 'tg_link': link, 'max_chat_id': max_chat,
            'file': 'scheduled-ucp-done/' + file}
    publish(pid, base, items, parse_time(d.get('sent_at'), now), now, link)


# ---------- разовые команды для каналов MAX ----------
def process_command(file, now):
    """commands-max/{имя}.json — разовые действия в канале MAX: отправить (с кнопками и закреплением),
    исправить, закрепить, удалить. Пример: {"channel": "@nscspr", "steps": [
      {"action": "send", "key": "toc", "text_html": "...", "buttons": [[{"text": "...", "url": "..."}]], "pin": true},
      {"action": "edit", "key": "toc", "text_html": "...", "buttons": [...]},
      {"action": "pin", "key": "toc"}, {"action": "delete", "key": "toc"}]}
    key — имя сообщения: по нему потом можно исправить или закрепить его (связка key → mid в commands-max/keys.json).
    Результат: commands-max-done/{имя}.json или commands-max-failed/{имя}-{время}.json."""
    pid = 'cmd-' + file[:-5]
    s, b = gh('GET', '/contents/commands-max/' + urllib.parse.quote(file), raw=True)
    if s != 200:
        raise RuntimeError('commands-max/%s: HTTP %s' % (file, s))
    d = json.loads(b)
    chat = str((CFG.get('channels') or {}).get(d.get('channel'), '')).strip()
    keys = keys_load()
    results, error = [], None
    if not chat:
        error = 'канал %s не подключён в config.json' % d.get('channel')
    for st in ([] if error else d.get('steps') or []):
        a = st.get('action')
        mid = st.get('mid') or keys.get('%s:%s' % (d.get('channel'), st.get('key')))
        r = {'action': a, 'key': st.get('key')}
        if DRY:
            log('[dry-run]', pid, a, st.get('key'), mid); continue
        if a in ('send', 'edit'):
            body = {'text': to_max_html(st.get('text_html') or ''), 'format': 'html'}
            if len(body['text']) > CFG.get('text_limit', 4000):
                error = 'текст длиннее %s знаков' % CFG.get('text_limit', 4000); break
            att = []
            if st.get('image'):
                att.append({'type': 'image', 'payload': upload_image(repo_file_b64(st['image']),
                            'image/png' if st['image'].endswith('.png') else 'image/jpeg')})
                time.sleep(CFG.get('after_upload_seconds', 3))
            if st.get('buttons'):
                att.append({'type': 'inline_keyboard', 'payload': {'buttons': [
                    [{'type': 'link', 'text': x['text'], 'url': x['url']} for x in row] for row in st['buttons']]}})
            if att or a == 'send':
                body['attachments'] = att
            if a == 'send':
                rs = send(chat, body)
                if not rs['ok']:
                    error = rs['error']; break
                mid = rs['max_mid']; r.update(max_mid=mid, max_url=rs.get('max_url'))
                if st.get('key'):
                    keys['%s:%s' % (d.get('channel'), st['key'])] = mid
            else:
                if not mid:
                    error = 'edit: не найден mid для key=%s' % st.get('key'); break
                s2, rr = max_req('PUT', '/messages?message_id=' + urllib.parse.quote(mid), body)
                if s2 != 200 or not rr.get('success'):
                    error = 'edit: HTTP %s %s' % (s2, json.dumps(rr, ensure_ascii=False)[:200]); break
                r['max_mid'] = mid
        if a == 'pin' or (a == 'send' and st.get('pin')):
            s2, rr = max_req('PUT', '/chats/%s/pin' % chat, {'message_id': mid, 'notify': bool(st.get('notify', False))})
            if s2 != 200 or not rr.get('success'):
                error = 'pin: HTTP %s %s' % (s2, json.dumps(rr, ensure_ascii=False)[:200]); break
            r['pinned'] = True
        if a == 'delete':
            s2, rr = max_req('DELETE', '/messages?message_id=' + urllib.parse.quote(mid or ''))
            if s2 != 200 or not rr.get('success'):
                error = 'delete: HTTP %s %s' % (s2, json.dumps(rr, ensure_ascii=False)[:200]); break
            keys.pop('%s:%s' % (d.get('channel'), st.get('key')), None)
        results.append(r); log(pid, '→', a, st.get('key') or '', mid or ''); time.sleep(1)
    if DRY:
        return
    keys_save(keys)
    rec = {'id': pid, 'file': 'commands-max/' + file, 'channel': d.get('channel'), 'max_chat_id': chat,
           'results': results, 'at': now.isoformat(), 'by': 'max-publisher (Россия)'}
    if error:
        rec['error'] = error
        path = 'commands-max-failed/%s-%s.json' % (file[:-5], now.strftime('%Y%m%dT%H%M'))
        notify('MAX: команда %s не выполнена — %s' % (file, error[:300]))
    else:
        path = 'commands-max-done/' + file
    m = {'id': pid, 'status': 'failed' if error else 'ok', 'pushed': False, 'gh_path': path, 'gh_record': rec,
         'gh_message': 'commands-max ' + file[:-5] + ' (ru)'}
    mark(pid, m)
    if gh_put(path, rec, m['gh_message']):
        m['pushed'] = True; mark(pid, m)


def keys_load():
    try:
        return json.load(open(os.path.join(STATE, 'keys.json'), encoding='utf-8'))
    except FileNotFoundError:
        s, b = gh('GET', '/contents/commands-max-done/keys.json', raw=True)
        return json.loads(b) if s == 200 else {}


def keys_save(keys):
    with open(os.path.join(STATE, 'keys.json'), 'w', encoding='utf-8') as f:
        json.dump(keys, f, ensure_ascii=False, indent=2)
    path = '/contents/commands-max-done/keys.json'
    s, b = gh('GET', path)
    body = {'message': 'commands-max keys (ru)', 'committer': CFG['committer'], 'author': CFG['committer'],
            'content': base64.b64encode(json.dumps(keys, ensure_ascii=False, indent=2).encode()).decode()}
    if s == 200:
        body['sha'] = js(b).get('sha')
    gh('PUT', path, body)


def check():
    ok = True
    s, b = gh('GET', '')
    print('GitHub:', 'OK' if s == 200 else 'ошибка HTTP %s %s' % (s, b[:150])); ok &= s == 200
    s, r = max_req('GET', '/me')
    print('MAX /me:', ('OK, бот ' + str(r.get('name') or r.get('username'))) if s == 200 else 'ошибка HTTP %s %s' % (s, json.dumps(r, ensure_ascii=False)[:200])); ok &= s == 200
    if s == 200:
        # GET /chats отключён в июне 2026: chat_id теперь только из событий бота (bot_added и др.)
        s, r = max_req('GET', '/updates?limit=100&timeout=0')
        seen = {}
        for u in r.get('updates', []):
            cid = u.get('chat_id') or ((u.get('message') or {}).get('recipient') or {}).get('chat_id')
            if cid and cid not in seen:
                seen[cid] = (u.get('update_type'), (u.get('chat') or {}).get('title') or u.get('title') or '')
        for cid, (t, title) in seen.items():
            print('  событие: chat_id=%s %s %s' % (cid, t, title))
        if not seen:
            print('  событий нет: чтобы узнать chat_id, заново добавьте бота в канал и повторите --check')
        for cid in (CFG.get('channels') or {}).values():
            if str(cid).strip():
                s2, c = max_req('GET', '/chats/' + str(cid).strip())
                print('  канал из config.json %s:' % cid, ('OK, ' + str(c.get('title'))) if s2 == 200 else 'ошибка HTTP %s %s' % (s2, json.dumps(c, ensure_ascii=False)[:150]))
    if '--notify-test' in sys.argv:
        notify('Проверка уведомлений публикатора MAX: если вы это читаете, уведомления доходят.')
        print('тестовое уведомление отправлено (см. MAX и Telegram)')
    if TG_TOKEN:
        s, _ = http('GET', TG_API + '/bot' + TG_TOKEN + '/getMe', timeout=15)
        print('Telegram (только для уведомлений):', 'OK' if s == 200 else 'недоступен, HTTP %s' % s)
    return 0 if ok else 1


def main():
    if '--check' in sys.argv:
        return check()
    os.makedirs(STATE, exist_ok=True)
    lock = open(os.path.join(STATE, 'lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('предыдущий проход ещё идёт'); return 0
    if not CFG.get('enabled', False) and not DRY:
        return 0
    start = dt.datetime.fromisoformat(CFG['start_from'])          # время по Москве, как в именах файлов
    now = dt.datetime.now(dt.timezone.utc)
    if not DRY:
        push_pending()
    done = set(n[:-5] for n in gh_list('sent-max'))
    done |= set(re.sub(r'-\d{8}T\d{4}$', '', n[:-5]) for n in gh_list('failed-max'))
    queue = [(n[:-5], n, process_main) for n in gh_list('sent')
             if n.endswith('.json') and id_time(n) and id_time(n) >= start]
    if str((CFG.get('channels') or {}).get('@nscspr', '')).strip():
        queue += [('ucp-' + n[:-5], n, process_ucp) for n in gh_list('scheduled-ucp-done')
                  if n.endswith('.json') and id_time(n) and id_time(n) >= dt.datetime.fromisoformat(CFG.get('start_from_ucp') or CFG['start_from'])]
    queue += [('cmd-' + n[:-5], n, process_command) for n in gh_list('commands-max') if n.endswith('.json')]
    done |= set('cmd-' + n[:-5] for n in gh_list('commands-max-done') if n != 'keys.json')
    for pid, n, fn in sorted(queue, key=lambda q: q[1]):
        if pid in done or load_mark(pid):
            continue
        try:
            fn(n, now)
        except Exception as e:
            log(pid, '— пропуск в этот проход:', str(e)[:300])  # GitHub недоступен и т. п.: повторим через 2 минуты
        time.sleep(1)                                           # MAX: не больше 2 сообщений в секунду
    return 0


if __name__ == '__main__':
    sys.exit(main())
