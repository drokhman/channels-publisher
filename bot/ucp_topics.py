#!/usr/bin/env python3
"""Рубрикатор УЦП (@nscspr): раскладывает вышедшие посты канала по темам чата.

Источники: scheduled-ucp-done/*.json (посты по расписанию) и commands-ucp-done/*.json
(разовые send/photo/poll). Рубрика: поле "rubric" в посте → config manual → первое
правило rules (подстрока в имени файла) → default_topic. Опросы дополнительно
пересылаются в тему polls_copy_to. Результат: topics-ucp/<id>.json.
"""
import glob, json, os, sys, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "config", "ucp-topics.json")
DONE_DIR = os.path.join(ROOT, "topics-ucp")
TOKEN = os.environ.get("UCP_BOT_TOKEN", "")
POST_ACTIONS = {"photo", "send", "poll"}
MAX_PER_RUN = int(os.environ.get("UCP_TOPICS_MAX", "40"))


def api(method, payload, tries=3):
    r = {}
    for _ in range(tries):
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                r = json.load(resp)
        except urllib.error.HTTPError as e:
            r = json.load(e)
        ra = (r.get("parameters") or {}).get("retry_after")
        if r.get("ok") or not ra:
            return r
        time.sleep(ra + 1)
    return r


def collect():
    """[(id, rubric|None, [(message_id, action)])] по возрастанию message_id."""
    out = []
    for f in glob.glob(os.path.join(ROOT, "scheduled-ucp-done", "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        msgs = [(r["message_id"], r.get("action")) for r in d.get("results", [])
                if r.get("ok") and r.get("message_id") and r.get("action") in POST_ACTIONS]
        if msgs:
            out.append((os.path.basename(f)[:-5], d.get("rubric"), msgs))
    for f in glob.glob(os.path.join(ROOT, "commands-ucp-done", "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        if d.get("action") not in POST_ACTIONS:
            continue
        res = (d.get("result") or {}).get("result")
        if isinstance(res, dict) and res.get("message_id"):
            out.append((os.path.basename(f)[:-5], d.get("rubric"), [(res["message_id"], d["action"])]))
    return sorted(out, key=lambda x: x[2][0][0])


def pick(cfg, pid, rubric):
    if rubric:
        return rubric
    if pid in cfg.get("manual", {}):
        return cfg["manual"][pid]
    for sub, key in cfg.get("rules", []):
        if sub in pid:
            return key
    return cfg["default_topic"]


def main():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    if not cfg.get("enabled"):
        print("Рубрикатор выключен — пропуск"); return 0
    if not TOKEN:
        print("Нет секрета UCP_BOT_TOKEN"); return 1
    chat, src = cfg["group_chat_id"], cfg["source_channel"]
    changed = False
    for t in cfg["topics"]:
        if t.get("thread_id"):
            continue
        r = api("createForumTopic", {"chat_id": chat, "name": t["name"], "icon_color": t["icon_color"]})
        if not r.get("ok"):
            print("Не удалось создать тему", t["name"], r); return 1
        t["thread_id"] = r["result"]["message_thread_id"]; changed = True
        print("Создана тема", t["name"], t["thread_id"]); time.sleep(2)
    if changed:
        json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    by_key = {t["key"]: t for t in cfg["topics"]}
    os.makedirs(DONE_DIR, exist_ok=True)
    errors = done_n = 0
    posts = collect()
    if cfg.get("min_message_id", 0) <= 8 and "msg-8" in cfg.get("manual", {}):
        posts.insert(0, ("msg-8", None, [(8, "photo")]))
    for pid, rubric, msgs in posts:
        if done_n >= MAX_PER_RUN:
            print("Лимит за запуск, остальное — в следующий раз"); break
        done = os.path.join(DONE_DIR, f"{pid}.json")
        if pid in cfg.get("skip", []) or os.path.exists(done) or msgs[0][0] < cfg.get("min_message_id", 0):
            continue
        key = pick(cfg, pid, rubric)
        topic = by_key.get(key) or by_key[cfg["default_topic"]]
        ids = [m for m, _ in msgs]
        method = "forwardMessages" if cfg.get("mode") == "forward" else "copyMessages"
        r = api(method, {"chat_id": chat, "message_thread_id": topic["thread_id"], "from_chat_id": src,
                         "message_ids": ids, "disable_notification": True})
        rec = {"id": pid, "topic": topic["key"], "topic_name": topic["name"], "thread_id": topic["thread_id"],
               "source_link": f"https://t.me/{src.lstrip('@')}/{ids[0]}", "ok": r.get("ok")}
        if not r.get("ok"):
            errors += 1; print("ОШИБКА", pid, r); continue
        rec["group_message_ids"] = [m["message_id"] for m in r["result"]]
        polls = [m for m, a in msgs if a == "poll"]
        pk = cfg.get("polls_copy_to")
        if polls and pk and pk != topic["key"] and pk in by_key:
            time.sleep(2)
            r2 = api(method, {"chat_id": chat, "message_thread_id": by_key[pk]["thread_id"], "from_chat_id": src,
                              "message_ids": polls, "disable_notification": True})
            rec["polls_copy_ok"] = r2.get("ok")
        json.dump(rec, open(done, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("OK", pid, "→", topic["name"]); done_n += 1
        time.sleep(4)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
