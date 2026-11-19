#!/usr/bin/env python3
"""Дайджест изменений права: площадка → Библиотека платформы → анонс в @nscspr.

Берёт последний опубликованный выпуск https://npscs.ru/pub/digest/latest.json.
Если выпуска ещё нет в digest-ucp-done/<id>.json:
  1) в тему «Изменения права» (key law в config/ucp-topics.json) группы библиотеки —
     по сообщению на тему дайджеста, HTML, до 4096 знаков, со ссылками на источники;
  2) в канал @nscspr — только короткий анонс со ссылкой на первое сообщение в библиотеке
     и приглашением в библиотеку (владелец: «На основном канале только Анонс и ссылку на библиотеку»);
  3) результат — в digest-ucp-done/<id>.json.
Анонс идёт напрямую, а не через commands-ucp-done, поэтому рубрикатор его не пересылает.
Тему law создаёт рубрикатор (bot/ucp_topics.py) — до этого скрипт ждёт.
"""
import html, json, os, sys, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "ucp-topics.json")
DONE = os.path.join(ROOT, "digest-ucp-done")
FEED = os.environ.get("DIGEST_FEED", "https://npscs.ru/pub/digest/latest.json")
TOKEN = os.environ.get("UCP_BOT_TOKEN", "")
CHANNEL = "@nscspr"
INVITE = "https://t.me/+eeQ6vFzW6EliNTYy"
SITE = "https://npscs.ru/#lawdigest"
LIMIT = 4096
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


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


def day(s):
    try:
        y, m, d = (int(x) for x in s[:10].split("-"))
        return f"{d} {MONTHS[m - 1]} {y}"
    except Exception:
        return s or ""


def e(s):
    return html.escape(str(s or ""), quote=False)


def item_html(it, cname):
    head = f"<b>{e(cname)}</b> · {e(it.get('law'))}" if it.get("law") else f"<b>{e(cname)}</b>"
    lines = [head, e(it.get("what"))]
    meta = [x for x in (it.get("act"), it.get("status"), ("вступает: " + it["effective"]) if it.get("effective") else "") if x]
    if meta:
        lines.append("<i>" + e(" · ".join(meta)) + "</i>")
    if it.get("todo"):
        lines.append("Что сделать: " + e(it["todo"]))
    if it.get("url"):
        lines.append(f'<a href="{html.escape(it["url"])}">{e(it.get("source") or "Источник")}</a>')
    return "\n".join(lines)


def theme_messages(feed, theme):
    cn = {c["id"]: c["name"] for c in feed.get("countries", [])}
    title = f"<b>{e(theme['name'])}</b> — изменения {day(feed['from'])} – {day(feed['to'])}"
    tail = "\n\n<i>Собрано ИИ, проверено модератором площадки. Перед применением сверяйтесь с первоисточником.</i>"
    msgs, cur = [], title
    for it in theme["items"]:
        block = "\n\n" + item_html(it, cn.get(it.get("country"), it.get("country", "")))
        if len(cur) + len(block) + len(tail) > LIMIT:
            msgs.append(cur)
            cur = title + " (продолжение)"
        cur += block
    msgs.append(cur + tail)
    return msgs


def main():
    if not TOKEN:
        print("Нет секрета UCP_BOT_TOKEN"); return 1
    cfg = json.load(open(CFG, encoding="utf-8"))
    topic = next((t for t in cfg["topics"] if t["key"] == "law"), None)
    if not topic or not topic.get("thread_id"):
        print("Тема «Изменения права» ещё не создана рубрикатором — пропуск"); return 0
    req = urllib.request.Request(FEED, headers={"User-Agent": "ucp-digest/1.0 (channels-publisher)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            feed = json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            print("Опубликованных выпусков нет"); return 0
        print("Лента недоступна:", err.code); return 1
    rid = feed.get("id")
    os.makedirs(DONE, exist_ok=True)
    done = os.path.join(DONE, f"{rid}.json")
    if not rid or os.path.exists(done):
        print("Выпуск уже отправлен:", rid); return 0
    chat = cfg["group_chat_id"]
    rec = {"id": rid, "from": feed.get("from"), "to": feed.get("to"), "thread_id": topic["thread_id"], "group": [], "ok": False}
    count = sum(len(t["items"]) for t in feed.get("themes", []))
    intro = (f"<b>Изменения права: {day(feed['from'])} – {day(feed['to'])}</b>\n"
             f"{count} изменений по темам: " + ", ".join(e(t["name"]) for t in feed["themes"]) +
             f".\nНа площадке — с фильтром по странам: {SITE}")
    for text in [intro] + [m for t in feed["themes"] for m in theme_messages(feed, t)]:
        r = api("sendMessage", {"chat_id": chat, "message_thread_id": topic["thread_id"], "text": text,
                                "parse_mode": "HTML", "disable_web_page_preview": True, "disable_notification": True})
        if not r.get("ok"):
            rec["error"] = r; print("ОШИБКА в библиотеке", r)
            json.dump(rec, open(done + ".fail", "w", encoding="utf-8"), ensure_ascii=False, indent=2); return 1
        rec["group"].append(r["result"]["message_id"]); time.sleep(2)
    first = f"https://t.me/c/{str(chat).replace('-100', '', 1)}/{rec['group'][0]}"
    rec["library_link"] = first
    ann = (f"<b>Что изменилось в регулировании за месяц</b>\n"
           f"Маркировка, торговля, маркетплейсы, транспорт, склад, ВЭД, налоги, аутстаф — "
           f"{count} изменений с {day(feed['from'])} по {day(feed['to'])}, по темам и с источниками.\n\n"
           f'Читать в библиотеке: <a href="{first}">выпуск</a>\n'
           f'Ещё не в библиотеке — вступайте: <a href="{INVITE}">Библиотека платформы</a>')
    r = api("sendMessage", {"chat_id": CHANNEL, "text": ann, "parse_mode": "HTML", "disable_web_page_preview": True})
    rec["announce_ok"] = r.get("ok")
    if r.get("ok"):
        rec["announce_id"] = r["result"]["message_id"]
    else:
        rec["announce_error"] = r; print("ОШИБКА анонса", r)
    rec["ok"] = bool(r.get("ok"))
    json.dump(rec, open(done, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Отправлено:", rid, len(rec["group"]), "сообщений; анонс", rec["announce_ok"])
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
