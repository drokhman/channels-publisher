#!/usr/bin/env python3
"""Публикатор MAX на российском сервере.

Берёт из GitHub посты, которые уже вышли в Telegram (sent/), достаёт их текст
и картинку из истории outbox/ и публикует тот же пост в канал MAX.
Telegram этот скрипт не трогает: его по-прежнему публикует n8n в Нидерландах.

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
    if not (TG_TOKEN and CFG.get('admin_chat_id')) or DRY:
        return
    body = json.dumps({'chat_id': CFG['admin_chat_id'], 'text': text}).encode()
    s, _ = http('POST', TG_API + '/bot' + TG_TOKEN + '/sendMessage', {'Content-Type': 'application/json'}, body, timeout=15)
    if s != 200:
        log('уведомление в Telegram не ушло (HTTP %s); ошибка записана в failed-max/' % s)


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
def process(file, now):
    pid = file[:-5]
    s, b = gh('GET', '/contents/sent/' + urllib.parse.quote(file), raw=True)
    if s != 200:
        raise RuntimeError('sent/%s: HTTP %s' % (file, s))
    rec = json.loads(b)
    max_chat = str((CFG.get('channels') or {}).get(rec.get('chat_id'), '')).strip()
    if not max_chat:
        mark(pid, {'id': pid, 'status': 'not-mapped', 'pushed': True}); return
    pub = dt.datetime.fromisoformat(rec.get('published_at', '').replace('Z', '+00:00')) if rec.get('published_at') else now
    base = {'id': pid, 'channel': rec.get('channel'), 'tg_chat_id': rec.get('chat_id'), 'tg_link': rec.get('link'),
            'max_chat_id': max_chat, 'file': rec.get('file') or file}
    post = original_post(rec.get('file') or file)
    if post.get('max') is False:
        mark(pid, {'id': pid, 'status': 'max-false', 'pushed': True}); log(pid, '— "max": false, пропуск'); return
    text = to_max_html(post.get('max_text_html') or post.get('text_html') or post.get('text') or '')
    limit = CFG.get('text_limit', 4000)
    error = None
    if (now - pub).total_seconds() > CFG.get('max_lag_hours', 12) * 3600:
        error = 'Опоздание: в Telegram пост вышел %s, позже %s ч. В MAX не отправлен, чтобы не выпускать пачку старых постов.' % (rec.get('published_at'), CFG.get('max_lag_hours', 12))
    elif len(text) > limit:
        error = 'Текст для MAX длиннее %s знаков (%s). Сократите или задайте max_text_html.' % (limit, len(text))
    if DRY:
        log('[dry-run]', pid, '→ MAX', max_chat, '| картинка:', 'файл' if post.get('image_base64') else ('ссылка' if post.get('img') else 'нет'),
            '| знаков:', len(text), '| ошибка:' if error else '', error or '')
        return
    result = None
    if not error:
        body = {'text': text, 'format': 'html'}
        try:
            if post.get('image_base64'):
                payload = upload_image(post['image_base64'], post.get('image_mime'))
                body['attachments'] = [{'type': 'image', 'payload': payload}]
                time.sleep(CFG.get('after_upload_seconds', 3))
            elif post.get('img'):
                body['attachments'] = [{'type': 'image', 'payload': {'url': post['img']}}]
            result = send(max_chat, body)
            if not result['ok']:
                error = result['error']
        except Exception as e:
            error = str(e)[:500]
    stamp = now.strftime('%Y%m%dT%H%M')
    if not error:
        record = {**base, 'max_mid': result['max_mid'], 'max_url': result['max_url'], 'attempts': result['attempts'],
                  'published_at': now.isoformat(), 'published_by': 'max-publisher (Россия)'}
        m = {'id': pid, 'status': 'ok', 'pushed': False, 'gh_path': 'sent-max/' + pid + '.json',
             'gh_record': record, 'gh_message': 'sent-max ' + pid + ' (ru)'}
        log(pid, '→ MAX опубликован', result.get('max_url') or result.get('max_mid'))
    else:
        light = {k: v for k, v in post.items() if k != 'image_base64'}
        record = {**light, 'failed_max': {'at': now.isoformat(), 'error': error, 'max_chat_id': max_chat, 'tg_link': rec.get('link'),
                  'by': 'max-publisher (Россия)', 'hint': 'В Telegram пост уже вышел. В MAX его нет: опубликуйте вручную или удалите отметку на сервере и запустите заново.'}}
        m = {'id': pid, 'status': 'failed', 'pushed': False, 'gh_path': 'failed-max/%s-%s.json' % (pid, stamp),
             'gh_record': record, 'gh_message': 'failed-max ' + pid + ' (ru)'}
        log(pid, '→ ошибка MAX:', error)
        notify('MAX: пост не опубликован — %s\nПричина: %s\nTelegram: %s\nФайл в failed-max/.' % (pid, error[:300], rec.get('link') or 'опубликован'))
    mark(pid, m)                      # сначала локально — это защита от дубля
    if gh_put(m['gh_path'], m['gh_record'], m['gh_message']):
        m['pushed'] = True; mark(pid, m)
    else:
        log('GitHub не принял запись, повторю в следующий проход:', m['gh_path'])


def check():
    ok = True
    s, b = gh('GET', '')
    print('GitHub:', 'OK' if s == 200 else 'ошибка HTTP %s %s' % (s, b[:150])); ok &= s == 200
    s, r = max_req('GET', '/me')
    print('MAX /me:', ('OK, бот ' + str(r.get('name') or r.get('username'))) if s == 200 else 'ошибка HTTP %s %s' % (s, json.dumps(r, ensure_ascii=False)[:200])); ok &= s == 200
    if s == 200:
        s, r = max_req('GET', '/chats?count=100')
        for c in r.get('chats', []):
            print('  чат: chat_id=%s type=%s title=%s' % (c.get('chat_id'), c.get('type'), c.get('title')))
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
    sent = sorted(n for n in gh_list('sent') if n.endswith('.json') and id_time(n) and id_time(n) >= start)
    for n in sent:
        pid = n[:-5]
        if pid in done or load_mark(pid):
            continue
        try:
            process(n, now)
        except Exception as e:
            log(pid, '— пропуск в этот проход:', str(e)[:300])  # GitHub недоступен и т. п.: повторим через 2 минуты
        time.sleep(1)                                           # MAX: не больше 2 сообщений в секунду
    return 0


if __name__ == '__main__':
    sys.exit(main())
