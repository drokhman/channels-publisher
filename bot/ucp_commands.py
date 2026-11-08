"""Исполнитель команд канала УЦП (@nscspr) через бота @ucp_publisher_bot.

Команды — файлы commands-ucp/*.json. Поддерживаются:
  {"action":"delete","message_id":7}
  {"action":"edit","message_id":8,"text_html":"..."}            # текстовый пост
  {"action":"edit_caption","message_id":8,"text_html":"..."}    # подпись к фото
  {"action":"poll","question":"...","options":["А","Б"],"anonymous":true,"multiple":false}
  {"action":"pin","message_id":8} / {"action":"unpin","message_id":8}
  {"action":"send","text_html":"..."}
  {"action":"photo","image":"commands-ucp-media/x.jpg","text_html":"..."}  # фото + подпись
  {"action":"check"}                                            # права бота в канале
  {"action":"chat"}                                             # сведения о канале и привязанном чате
  {"action":"updates"}                                          # группы, куда недавно добавили бота
Поле chat_id необязательно (по умолчанию @nscspr), разрешены только каналы из ALLOWED.
Результат: commands-ucp-done/ или commands-ucp-failed/, команда удаляется.
"""
import json, os, pathlib, sys, time, urllib.request, urllib.error

TOKEN = os.environ.get("UCP_BOT_TOKEN", "")
ALLOWED = ["@nscspr", "-1004303710880"]  # канал и группа-библиотека «Библиотека платформы»
DEFAULT_CHAT = "@nscspr"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC, DONE, FAIL = ROOT / "commands-ucp", ROOT / "commands-ucp-done", ROOT / "commands-ucp-failed"


def tg(method, payload):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}


def tg_photo(chat, path, caption, thread=None):
    import uuid
    b = uuid.uuid4().hex
    data = path.read_bytes()
    parts = []
    fields = [("chat_id", chat), ("caption", caption), ("parse_mode", "HTML")] + ([("message_thread_id", str(thread))] if thread else [])
    for k, v in fields:
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{path.name}\"\r\nContent-Type: image/jpeg\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}


def tg_upload(method, chat, field, path):
    import uuid
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n".encode(),
             f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{path.name}\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
             + path.read_bytes() + b"\r\n", f"--{b}--\r\n".encode()]
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}


def run(c):
    chat = str(c.get("chat_id") or DEFAULT_CHAT)
    if chat not in ALLOWED:
        return {"ok": False, "description": f"Канал {chat} не в белом списке"}
    a = c.get("action")
    mid = c.get("message_id")
    html = {"parse_mode": "HTML"}
    if a == "delete":
        return tg("deleteMessage", {"chat_id": chat, "message_id": mid})
    if a == "edit":
        return tg("editMessageText", {"chat_id": chat, "message_id": mid, "text": c["text_html"],
                                      "link_preview_options": {"is_disabled": not c.get("preview", False)}, **html})
    if a == "edit_caption":
        return tg("editMessageCaption", {"chat_id": chat, "message_id": mid, "caption": c["text_html"], **html})
    if a == "poll":
        return tg("sendPoll", {"chat_id": chat, "question": c["question"],
                               "options": [{"text": o} for o in c["options"]],
                               "is_anonymous": c.get("anonymous", True),
                               "allows_multiple_answers": c.get("multiple", False)})
    if a == "pin":
        return tg("pinChatMessage", {"chat_id": chat, "message_id": mid, "disable_notification": True})
    if a == "unpin":
        return tg("unpinChatMessage", {"chat_id": chat, "message_id": mid})
    if a == "send":
        return tg("sendMessage", {"chat_id": chat, "text": c["text_html"],
                                  "link_preview_options": {"is_disabled": not c.get("preview", False)}, **html})
    if a == "photo":
        # {"action":"photo","image":"commands-ucp-media/x.jpg","text_html":"..."} — подпись до 1024 видимых символов
        return tg_photo(chat, ROOT / c["image"], c.get("text_html", ""), c.get("thread_id"))
    if a == "check":
        me = tg("getMe", {})
        if not me.get("ok"):
            return me
        m = tg("getChatMember", {"chat_id": chat, "user_id": me["result"]["id"]})
        if m.get("ok"):
            m["bot"] = me["result"].get("username")
        return m
    if a == "invite":   # {"action":"invite","chat_id":"-100…","name":"…"} — постоянная ссылка-приглашение
        return tg("createChatInviteLink", {"chat_id": chat, "name": c.get("name", "Канал НППЦП")})
    if a == "video":   # {"action":"video","chat_id":"-100…","video":"commands-ucp-media/x.mp4","caption":"…"}
        import uuid
        b = uuid.uuid4().hex; path = ROOT / c["video"]
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                 for k, v in (("chat_id", chat), ("caption", c.get("caption", "")), ("parse_mode", "HTML"),
                              ("width", str(c.get("width", 800))), ("height", str(c.get("height", 800))), ("supports_streaming", "true"))
                              + ((("message_thread_id", str(c["thread_id"])),) if c.get("thread_id") else ())]
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{path.name}\"\r\nContent-Type: video/mp4\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendVideo", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.load(e)
    if a == "set_photo":   # {"action":"set_photo","chat_id":"-100…","image":"commands-ucp-media/x.jpg"}
        return tg_upload("setChatPhoto", chat, "photo", ROOT / c["image"])
    if a == "get_photo":   # скачать аватар чата в commands-ucp-media/avatar-<chat>.jpg
        g = tg("getChat", {"chat_id": chat})
        fid = ((g.get("result") or {}).get("photo") or {}).get("big_file_id")
        if not fid:
            return {"ok": False, "description": "У чата нет аватара", "raw": g}
        f = tg("getFile", {"file_id": fid})
        if not f.get("ok"):
            return f
        out = ROOT / "commands-ucp-media" / f"avatar-{chat.strip('@-')}.jpg"
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{f['result']['file_path']}", timeout=60) as r:
            out.write_bytes(r.read())
        return {"ok": True, "saved": str(out.relative_to(ROOT)), "size": out.stat().st_size}
    if a == "updates":   # найти группы, куда добавили бота (id для рубрикатора)
        u = tg("getUpdates", {"allowed_updates": ["my_chat_member", "message"], "limit": 100})
        if not u.get("ok"):
            return u
        chats = {}
        for x in u["result"]:
            m = x.get("my_chat_member") or x.get("message") or {}
            c = m.get("chat") or {}
            if c.get("type") in ("group", "supergroup"):
                chats[c["id"]] = {"id": c["id"], "title": c.get("title"), "is_forum": c.get("is_forum"),
                                  "status": (m.get("new_chat_member") or {}).get("status")}
        return {"ok": True, "chats": list(chats.values())}
    if a == "chat":
        c = tg("getChat", {"chat_id": chat})
        lid = c.get("result", {}).get("linked_chat_id")
        if lid:
            c["linked"] = tg("getChat", {"chat_id": lid})
        return c
    return {"ok": False, "description": f"Неизвестное действие: {a}"}


def main():
    if not TOKEN:
        print("Нет секрета UCP_BOT_TOKEN"); sys.exit(1)
    files = sorted(p for p in SRC.glob("*.json"))
    for p in files:
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            res = run(c)
        except Exception as e:
            c, res = {"raw": p.read_text(encoding="utf-8", errors="replace")}, {"ok": False, "description": repr(e)}
        dest = DONE if res.get("ok") else FAIL
        dest.mkdir(exist_ok=True)
        rec = {**c, "result": res, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (dest / p.name).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        p.unlink()
        print(p.name, "OK" if res.get("ok") else "FAIL " + str(res.get("description")))
        time.sleep(1)


if __name__ == "__main__":
    main()
