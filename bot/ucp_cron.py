"""Пишет строки cron в .github/workflows/ucp-schedule.yml по очереди scheduled-ucp/.

Запуск: python3 bot/ucp_cron.py. Для каждого поста — свой запуск в его минуту (UTC = Москва − 3 ч),
Основной запуск в минуту поста делает внешний вызов workflow_dispatch; cron здесь — второй путь.
Страховка через 25 минут убрана 2026-11-18 (экономия минут GitHub Actions).
"""
import pathlib, re, datetime as dt
ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows/ucp-schedule.yml"
NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})-")
now = dt.datetime.now(dt.timezone.utc)
lines = []
for p in sorted((ROOT / "scheduled-ucp").glob("*.json")):
    m = NAME.match(p.name)
    if not m:
        continue
    y, mo, d, h, mi = map(int, m.groups())
    t = dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone(dt.timedelta(hours=3))).astimezone(dt.timezone.utc)
    for k, tt in (("пост", t),):
        if tt > now - dt.timedelta(hours=1):
            lines.append(f"    - cron: '{tt.minute} {tt.hour} {tt.day} {tt.month} *'   # {p.name} · {k}")
if not lines:
    lines = ["    - cron: '0 0 1 1 *'   # очередь пуста"]
body = WF.read_text(encoding="utf-8")
start, end = body.index("  schedule:\n") + len("  schedule:\n"), body.index("  workflow_dispatch:")
WF.write_text(body[:start] + "\n".join(lines) + "\n" + body[end:], encoding="utf-8")
print(len(lines), "запусков")
