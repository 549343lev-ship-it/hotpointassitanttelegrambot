"""engine/cross_check.py — перевірки між позиціями (п.11, 12, 15, 16)."""
from __future__ import annotations

import re

SYSTEM_LIMITS: dict[str, dict[str, set[int]]] = {
    "push_systems": {"angles": {90}, "dia": {16, 20, 25, 32}},
    "sewage":       {"angles": {15, 30, 45, 67, 87, 90}, "dia": {32, 40, 50, 75, 110, 160}},
    "plastic_ppr":  {"angles": {45, 90}, "dia": {20, 25, 32, 40, 50, 63, 75, 90, 110}},
}
FALLBACK_SYSTEM: dict[str, str] = {"push_systems": "plastic_ppr"}   # чого нема в PUSH → PPR
_PUSH_WORDS = re.compile(r"\s*(натяжн\S*|push|пуш|raftec|рафтек)", re.I)
_ANGLE = re.compile(r"(\d{2})\s*°")


def _angle(п: dict) -> int | None:
    a = п.get("angle")
    if isinstance(a, (int, float)) and a:
        return int(a)
    m = _ANGLE.search(f"{п.get('normalized', '')} {п.get('original', '')}")
    return int(m.group(1)) if m else None


def fix_system_gaps(позиції: list[dict]) -> None:
    """До пошуку: фітинг, якого не існує в системі, переводимо у fallback-систему з ❗."""
    for п in позиції:
        cat = п.get("category")
        lim = SYSTEM_LIMITS.get(cat)
        if not lim or п.get("type") not in ("коліно", "трійник"):
            continue
        ang = _angle(п)
        if ang is None or ang in lim["angles"]:
            continue
        new_cat = FALLBACK_SYSTEM.get(cat)
        if not new_cat:
            п.setdefault("notes", []).append(f"❗ кут {ang}° не існує в цій системі")
            continue
        п.update(category=new_cat, _ctx_category=new_cat, _system="ppr")
        п.pop("_ctx_brand", None)                 # далі — пріоритет брендів PPR
        п["normalized"] = _PUSH_WORDS.sub("", п.get("normalized", "")).strip() + " PPR"
        п.pop("_qa", None)
        п.setdefault("notes", []).append(f"❗ в PUSH немає {ang}° — дано PPR")


def _wall(name: str) -> str | None:
    m = re.search(r"16\s*[xх]\s*(\d,\d)", name)
    return m.group(1) if m else None


def check_pairs(результати: list[dict]) -> None:
    """Після пошуку: парні параметри і переходи між системами. Пише примітки в reason/brand_warning."""
    found = [r for r in результати if r and r.get("знайдено")]

    def warn(r: dict, text: str) -> None:
        r["reason"] = f"{text}. {r.get('reason', '')}".strip()
        r["brand_warning"] = r.get("brand_warning") or text

    # 1) євроконус ↔ труба т/п: однакова стінка
    pipe_walls = {_wall(r["назва"]) for r in found
                  if r["назва"].startswith("Труба") and r.get("category") == "underfloor_heating"} - {None}
    for r in found:
        if "Євроконус" in r["назва"] and pipe_walls and _wall(r["назва"]) not in pipe_walls:
            warn(r, f"❗ стінка євроконуса ≠ труби т/п ({', '.join(sorted(pipe_walls))})")

    # 2) перехід PPR при наявності PUSH того ж діаметра
    push_dia = {int(d) for r in found if r.get("category") == "push_systems"
                for d in re.findall(r"ф\s*(\d{2})", r["назва"])}
    for r in found:
        if r.get("category") == "plastic_ppr" and "перехідна" in r["назва"]:
            dias = {int(d) for d in re.findall(r"(\d{2})", r["назва"].split("ф", 1)[-1])[:2]}
            if dias & push_dia:
                warn(r, "⚠️ ф20 у замовленні — PUSH: перехід PPR→PUSH через МРВ PPR + муфту PUSH РЗ")
