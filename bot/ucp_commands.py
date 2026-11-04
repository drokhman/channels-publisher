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
Поле chat_id необязательно (по умолчанию @nscspr), разрешены только каналы из ALLOWED.
Результат: commands-ucp-done/ или commands-ucp-failed/, команда удаляется.
"""
import json, os, pathlib, sys, time, urllib.request, urllib.error

TOKEN = os.environ.get("UCP_BOT_TOKEN", "")
ALLOWED = ["@nscspr"]
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


def tg_photo(chat, path, caption):
    import uuid
    b = uuid.uuid4().hex
    data = path.read_bytes()
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption), ("parse_mode", "HTML")):
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
        return tg_photo(chat, ROOT / c["image"], c.get("text_html", ""))
    if a == "check":
        me = tg("getMe", {})
        if not me.get("ok"):
            return me
        m = tg("getChatMember", {"chat_id": chat, "user_id": me["result"]["id"]})
        if m.get("ok"):
            m["bot"] = me["result"].get("username")
        return m
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
