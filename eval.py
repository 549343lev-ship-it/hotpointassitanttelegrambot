"""
eval.py — Вимір точності пошуку на еталонному наборі.

Без цього скрипта рух до 95% сліпий: ви не знаєте ні поточної цифри,
ні чи покращила її зміна.

ПІДГОТОВКА golden_set.json (200-300 рядків з реальних замовлень):
    [
      {"query": "трійник ппр 25х20х25 асг", "correct": "Трійник редукційний PPR ф 25х20х25, PP-RCT, ASG"},
      {"query": "коліно канал 110 на 87",   "correct": "Коліно внут. канал. ф110 х 87,5°, сіре, HTR, ASG"}
    ]
  Поле "correct" — точна назва з каталогу (можна також "artikul").

ЗАПУСК:
    python eval.py                     # повний прогін
    python eval.py --parametric-only   # тільки параметричний шар (швидко, без API)
"""

import json
import os
import sys

GOLDEN = os.environ.get("GOLDEN_SET", "golden_set.json")


def load_golden() -> list[dict]:
    if not os.path.exists(GOLDEN):
        print(f"❌ Немає {GOLDEN}. Створи файл за зразком у докстрінгу.")
        sys.exit(1)
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)


def _match(expected: str, got: str) -> bool:
    return expected.strip().lower() == (got or "").strip().lower()


def run_parametric(rows: list[dict]) -> dict:
    """Прогін ТІЛЬКИ параметричного шару — без Voyage і без Claude, безкоштовно."""
    from engine.parametric import parametric_search, parse_parametric, query_strength

    top1 = top3 = empty = weak = 0
    misses: list[tuple[str, str, str]] = []

    for r in rows:
        q, exp = r["query"], r["correct"]
        pq = parse_parametric(q, query_mode=True)
        if query_strength(pq) < 2:
            weak += 1
            continue
        hits = parametric_search(q, top_n=3, qa=pq)
        if not hits:
            empty += 1
            misses.append((q, exp, "— порожньо —"))
            continue
        names = [h["name"] for h in hits]
        if _match(exp, names[0]):
            top1 += 1
        elif any(_match(exp, n) for n in names):
            top3 += 1
        else:
            misses.append((q, exp, names[0]))

    n = len(rows) or 1
    print(f"\n📐 ПАРАМЕТРИЧНИЙ ШАР  ({n} запитів)")
    print(f"   top-1:            {top1:4}  ({top1 / n * 100:5.1f}%)")
    print(f"   top-3:            {top1 + top3:4}  ({(top1 + top3) / n * 100:5.1f}%)")
    print(f"   мало атрибутів:   {weak:4}  ({weak / n * 100:5.1f}%)  → йде у Voyage")
    print(f"   0 кандидатів:     {empty:4}  ({empty / n * 100:5.1f}%)  ← ЛАГОДИТИ ПЕРШИМ")

    if misses:
        print(f"\n❌ ПОМИЛКИ ({len(misses)}) — кожна вказує на відсутній синонім:")
        for q, exp, got in misses[:25]:
            print(f"   запит:  {q}")
            print(f"   треба:  {exp[:80]}")
            print(f"   дало:   {got[:80]}\n")

    return {"top1": top1, "top3": top1 + top3, "empty": empty, "weak": weak, "n": n}


def run_full(rows: list[dict]) -> dict:
    """Повний прогін через find_items — з Voyage і Claude. Витрачає токени."""
    from engine.search import find_items

    позиції = [{"original": r["query"], "normalized": r["query"], "qty": 1} for r in rows]
    res = find_items(позиції)

    top1 = notfound = 0
    by_src: dict[str, int] = {}
    misses: list[tuple[str, str, str, str]] = []

    for r, got in zip(rows, res):
        src = got.get("джерело", "?")
        by_src[src] = by_src.get(src, 0) + 1
        if not got.get("знайдено"):
            notfound += 1
            misses.append((r["query"], r["correct"], "—", src))
        elif _match(r["correct"], got.get("назва", "")):
            top1 += 1
        else:
            misses.append((r["query"], r["correct"], got.get("назва", ""), src))

    n = len(rows) or 1
    print(f"\n🎯 ПОВНИЙ ПАЙПЛАЙН  ({n} запитів)")
    print(f"   ТОЧНІСТЬ:         {top1:4}  ({top1 / n * 100:5.1f}%)")
    print(f"   не знайдено:      {notfound:4}  ({notfound / n * 100:5.1f}%)")
    print("\n   розподіл по джерелах:")
    for k, v in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"     {k:24} {v:4}  ({v / n * 100:5.1f}%)")

    if misses:
        print(f"\n❌ ПОМИЛКИ ({len(misses)}):")
        for q, exp, got, src in misses[:25]:
            print(f"   [{src}] {q}")
            print(f"      треба: {exp[:78]}")
            print(f"      дало:  {got[:78]}\n")

    return {"top1": top1, "n": n}


if __name__ == "__main__":
    rows = load_golden()
    print(f"📋 Еталонний набір: {len(rows)} запитів")
    if "--parametric-only" in sys.argv:
        run_parametric(rows)
    else:
        run_parametric(rows)
        run_full(rows)
