"""Время выхода постов УЦП на день: 5–6 постов, между соседними 60–90 минут, минуты случайные.

Запуск: python3 bot/ucp_times.py 2026-10-27 [5|6] [ЧЧММ — не раньше этого времени, для текущего дня]  → печатает ЧЧММ через пробел.
Время дня (ЧЧ:ММ) никогда не повторяется: все занятые слоты — в scheduled-ucp/used-times.txt.
Первый пост — 08:00–09:00 по Москве, последний — не позже 18:45.
"""
import pathlib, random, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
USED = ROOT / "scheduled-ucp" / "used-times.txt"
day = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else random.choice([5, 6])
used = set(USED.read_text().split()) if USED.exists() else set()
for _ in range(5000):
    lo = int(sys.argv[3][:2]) * 60 + int(sys.argv[3][2:]) if len(sys.argv) > 3 else 8 * 60
    t = random.randint(lo, lo + 60)
    ts = [t]
    for _ in range(n - 1):
        t += random.randint(60, 90); ts.append(t)
    hm = [f"{x // 60:02d}{x % 60:02d}" for x in ts]
    if ts[-1] <= 18 * 60 + 45 and not (set(hm) & used) and len(set(x % 60 for x in ts)) == n:
        break
else:
    sys.exit("не нашёл свободных слотов")
USED.write_text("\n".join(sorted(used | set(hm))) + "\n")
print(" ".join(hm))
