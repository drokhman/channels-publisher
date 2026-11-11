"""Публикация постов канала УЦП (@nscspr) по расписанию.

Очередь — scheduled-ucp/ГГГГ-ММ-ДД-ЧЧММ-slug.json (время по Москве). Файл = один пост:
  {"steps":[{"action":"photo","image":"commands-ucp-media/x.jpg","text_html":"..."},
            {"action":"send","text_html":"..."},
            {"action":"poll","question":"...","options":["А","Б"]}]}
Шаги — те же действия, что у bot/ucp_commands.py. За один запуск уходит не больше одного поста
(самый ранний из тех, чьё время наступило) — так два поста не выходят одновременно, даже если GitHub
задержал запуск. Результат — scheduled-ucp-done/ или scheduled-ucp-failed/, файл из очереди удаляется.
Время запуска задают строки cron в .github/workflows/ucp-schedule.yml — их пишет bot/ucp_cron.py.
"""
import json, pathlib, re, sys, time, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ucp_commands as uc

ROOT = uc.ROOT
Q, DONE, FAIL = ROOT / "scheduled-ucp", ROOT / "scheduled-ucp-done", ROOT / "scheduled-ucp-failed"
MSK = dt.timezone(dt.timedelta(hours=3))
NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})-")


def due(p):
    m = NAME.match(p.name)
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    return dt.datetime(y, mo, d, h, mi, tzinfo=MSK)


def main():
    if not uc.TOKEN:
        print("Нет секрета UCP_BOT_TOKEN"); sys.exit(1)
    now = dt.datetime.now(MSK)
    ready = sorted((due(p), p) for p in Q.glob("*.json") if due(p) and due(p) <= now)
    if not ready:
        print("Нечего публиковать", now.isoformat()); return
    when, p = ready[0]
    post = json.loads(p.read_text(encoding="utf-8"))
    results, ok = [], True
    for st in post.get("steps", []):
        r = uc.run(st)
        results.append({"action": st.get("action"), "ok": r.get("ok"),
                        "message_id": (r.get("result") or {}).get("message_id") if isinstance(r.get("result"), dict) else None,
                        "description": r.get("description")})
        if not r.get("ok"):
            ok = False; break
        time.sleep(2)
    dest = DONE if ok else FAIL
    dest.mkdir(exist_ok=True)
    rec = {**post, "due": when.isoformat(), "sent_at": dt.datetime.now(MSK).isoformat(), "late_min": round((now - when).total_seconds() / 60), "results": results}
    for st in rec.get("steps", []):
        st.pop("image_base64", None)
    # MAX: фото без подписи + отдельный send — в MAX текст кладём к картинке (max_text_html), иначе там только фото
    sts = rec.get("steps", [])
    for i, st in enumerate(sts[:-1]):
        nx = sts[i + 1]
        if st.get("action") == "photo" and not (st.get("text_html") or "").strip() and not st.get("max_text_html") \
                and nx.get("action") == "send" and nx.get("text_html"):
            st["max_text_html"] = nx["text_html"]
            nx["max_skip"] = True
    (dest / p.name).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    p.unlink()
    print(p.name, "OK" if ok else "FAIL", results)


if __name__ == "__main__":
    main()
