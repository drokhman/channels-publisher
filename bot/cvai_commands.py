#!/usr/bin/env python3
"""Разовые команды Bot API для «Человек VS ИИ» и его библиотеки.
commands-cvai/<name>.json: {"method": "...", "payload": {...}}
Результат: commands-cvai-done/<name>.json (с ответом Telegram, без токена)."""
import glob, json, os, sys, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get("CVAI_BOT_TOKEN", "")
ALLOWED_CHATS = {"@chelovek_vs_ai", -1004326691613, "-1004326691613"}
ALLOWED_METHODS = {"createChatInviteLink", "editMessageReplyMarkup", "editMessageText",
                   "sendMessage", "pinChatMessage", "unpinChatMessage", "deleteMessage",
                   "editForumTopic", "getChat", "setChatPermissions"}

def api(method, payload):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
    except urllib.error.HTTPError as e: return json.load(e)

def main():
    if not TOKEN: print("Нет CVAI_BOT_TOKEN"); return 1
    bad = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "commands-cvai", "*.json"))):
        cmd = json.load(open(f, encoding="utf-8"))
        m, p = cmd.get("method"), cmd.get("payload", {})
        if m not in ALLOWED_METHODS or p.get("chat_id") not in ALLOWED_CHATS:
            r = {"ok": False, "description": "запрещено правилами"}
        else:
            r = api(m, p)
        cmd["result"] = r
        print(os.path.basename(f), m, "OK" if r.get("ok") else r)
        bad += 0 if r.get("ok") else 1
        json.dump(cmd, open(os.path.join(ROOT, "commands-cvai-done", os.path.basename(f)), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        os.remove(f)
    return 1 if bad else 0

if __name__ == "__main__": sys.exit(main())
