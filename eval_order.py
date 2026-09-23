"""eval_order.py — регресія на рівні ЗАМОВЛЕННЯ (п.«add, don't break»).

Запуск:  python eval_order.py [golden_orders.json]
Проганяє текст замовлення через увесь пайплайн без фото і рахує збіги назв (мультимножина).
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from engine.ocr import normalize_text
from engine.cross_check import check_pairs
from engine.search import find_items
from services.process_service import prepare_positions


def run_order(order: dict) -> tuple[int, int, list[str], list[str]]:
    text = "\n".join(f"{ln['query']} - {ln['qty']}" for ln in order["lines"] if not ln["query"].startswith("—"))
    поз = prepare_positions(normalize_text(text, order.get("caption", "")),
                            order.get("caption", ""), order.get("mode", "order"))
    for п in поз:
        п["_brand_map"] = {}
    res = find_items(поз)
    check_pairs(res)
    got = Counter(r["назва"] for r in res if r and r.get("знайдено"))
    exp = Counter(ln["correct"] for ln in order["lines"])
    hit = sum((got & exp).values())
    return hit, sum(exp.values()), sorted((exp - got).elements()), sorted((got - exp).elements())


def main(path: str = "golden_orders.json") -> None:
    with open(path, encoding="utf-8") as f:
        orders = json.load(f)["orders"]
    total_hit = total = 0
    for o in orders:
        hit, n, missed, extra = run_order(o)
        total_hit, total = total_hit + hit, total + n
        print(f"\n{o['id']}: {hit}/{n} = {hit / n:.0%}")
        for m in missed:
            print("  ✗ очікувалось:", m)
        for e in extra:
            print("  + зайве/інше: ", e)
    print(f"\nРАЗОМ: {total_hit}/{total} = {total_hit / max(total, 1):.0%}")


if __name__ == "__main__":
    main(*sys.argv[1:])
