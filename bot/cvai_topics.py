#!/usr/bin/env python3
"""Рубрикатор «Человек VS ИИ»: раскладывает вышедшие посты канала по темам группы.

Источник: sent/*cvai*.json (пишет n8n v3.1). Текст поста берётся из истории git
(outbox/<file> до удаления). Рубрика: поле "rubric" в посте, иначе первый
совпавший хештег по порядку тем в config/cvai-topics.json, иначе default_topic.
Результат: topics-cvai/<id>.json. Ничего не делает, пока enabled=false.
"""
import glob, json, os, re, subprocess, sys, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "config", "cvai-topics.json")
DONE_DIR = os.path.join(ROOT, "topics-cvai")
TOKEN = os.environ.get("CVAI_BOT_TOKEN", "")


def api(method, payload):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def post_text(fname):
    """Достаёт исходный JSON поста из истории git."""
    path = f"outbox/{fname}"
    if os.path.exists(os.path.join(ROOT, path)):
        return open(os.path.join(ROOT, path), encoding="utf-8").read()
    h = subprocess.run(["git", "log", "--diff-filter=D", "--format=%H", "-1", "--", path],
                       cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not h:
        return ""
    return subprocess.run(["git", "show", f"{h}^:{path}"], cwd=ROOT,
                          capture_output=True, text=True).stdout


def pick_topic(cfg, raw):
    try:
        d = json.loads(raw)
    except Exception:
        d = {}
    if d.get("rubric"):
        return d["rubric"]
    text = " ".join(str(d.get(k, "")) for k in ("text_html", "caption_html", "text", "caption")).lower()
    tags = set(t.lower() for t in re.findall(r"#\w+", text))
    for t in cfg["topics"]:
        if any(x.lower() in tags for x in t["tags"]):
            return t["key"]
    title = re.sub(r"<[^>]+>", "", str(d.get("text_html") or d.get("caption_html") or ""))[:80]
    if re.search(r"(^|\W)раунд\s*\d", title.lower()):
        return "rounds"
    return cfg["default_topic"]


def main():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    if not cfg.get("enabled") or not cfg.get("group_chat_id"):
        print("Рубрикатор выключен или не задана группа — пропуск")
        return 0
    if not TOKEN:
        print("Нет секрета CVAI_BOT_TOKEN"); return 1
    chat = cfg["group_chat_id"]

    # 1. Создать недостающие темы
    changed = False
    for t in cfg["topics"]:
        if t.get("thread_id"):
            continue
        r = api("createForumTopic", {"chat_id": chat, "name": t["name"], "icon_color": t["icon_color"]})
        if not r.get("ok"):
            print("Не удалось создать тему", t["name"], r); return 1
        t["thread_id"] = r["result"]["message_thread_id"]; changed = True
        print("Создана тема", t["name"], t["thread_id"])
    if changed:
        json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    by_key = {t["key"]: t for t in cfg["topics"]}

    # 2. Разложить вышедшие посты
    os.makedirs(DONE_DIR, exist_ok=True)
    sent = sorted(f for f in glob.glob(os.path.join(ROOT, "sent", "*cvai*.json")))
    errors = 0
    for f in sent:
        rec = json.load(open(f, encoding="utf-8"))
        if rec.get("chat_id") != cfg["source_channel"]:
            continue
        pid = rec["id"]
        done = os.path.join(DONE_DIR, f"{pid}.json")
        if os.path.exists(done):
            continue
        if not cfg.get("backfill") and rec.get("published_at", "") < cfg.get("start_from", ""):
            continue
        ids = [i for i in (rec.get("photo_message_id"), rec.get("text_message_id")) if i]
        if not ids:
            continue
        key = cfg.get("manual", {}).get(pid) or pick_topic(cfg, post_text(rec.get("file") or f"{pid}.json"))
        topic = by_key.get(key) or by_key[cfg["default_topic"]]
        method = "forwardMessages" if cfg.get("mode") == "forward" else "copyMessages"
        r = api(method, {"chat_id": chat, "message_thread_id": topic["thread_id"],
                         "from_chat_id": cfg["source_channel"], "message_ids": ids,
                         "disable_notification": True})
        out = {"id": pid, "topic": topic["key"], "topic_name": topic["name"],
               "thread_id": topic["thread_id"], "source_link": rec.get("link"), "ok": r.get("ok")}
        if r.get("ok"):
            out["group_message_ids"] = [m["message_id"] for m in r["result"]]
            json.dump(out, open(done, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("OK", pid, "→", topic["name"])
        else:
            errors += 1
            print("ОШИБКА", pid, r)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
