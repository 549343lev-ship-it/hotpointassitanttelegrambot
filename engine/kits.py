"""engine/kits.py — шаблони комплектів: підказка для LLM + детермінований підбір ролей (п.6, 17)."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

KITS_FILE = os.getenv("KITS_FILE", os.path.join(os.path.dirname(__file__), "..", "knowledge", "kits.json"))
_BAD = re.compile(r"ЗРАЗОК|ОБРАЗЕЦ|ЧАСТИНИ|КУСКИ|пошкоджен|\(п/з\)|знято з вироб", re.I)


@lru_cache(maxsize=1)
def load_kits() -> dict:
    with open(KITS_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def kits_prompt_block() -> str:
    return "\n".join(f"- {kid}: {k['title']}; ролі: {', '.join(k['roles'])}" for kid, k in load_kits().items())


def _qty(p: dict) -> float:
    m = re.search(r"\d+(?:[.,]\d+)?", str(p.get("qty") or ""))
    return float(m.group().replace(",", ".")) if m else 0.0


def _coil(name: str) -> int:
    m = re.search(r"\{(\d+)", name)
    return int(m.group(1)) if m else 0


def resolve_kit_items(позиції: list[dict], catalog: list[dict]) -> None:
    """Позиціям з _kit/_kit_role ставить _forced = товар з прайсу за шаблоном."""
    kits = load_kits()
    for п in позиції:
        kit = kits.get(п.get("_kit") or "")
        role = п.get("_kit_role")
        if not kit or role not in kit["roles"]:
            continue
        n = str(п.get("_kit_n") or "")
        if not n:
            m = re.search(r"(\d+)\s*(?:к|конт)", п.get("original", "").lower())
            n = m.group(1) if m else ""
        wall = kit.get("defaults", {}).get("WALL", "2,0")
        m = re.search(r"16\s*[xх×]\s*(\d[.,]\d)", f"{п.get('original', '')} {п.get('normalized', '')}")
        if m:
            wall = m.group(1).replace(".", ",")
        pat = kit["roles"][role].replace("{WALL}", wall).replace("{N}", n or r"\d+")
        cab = ""
        if "{CAB}" in pat:
            cab = kit.get("cabinet_by_n", {}).get(n, "")
            if not cab:
                continue
            pat = pat.replace("{CAB}", cab)
        rx = re.compile(pat, re.I)
        hits = [it for it in catalog if rx.search(it.get("name_full") or it["name"]) and not _BAD.search(it.get("name_full") or it["name"])]
        if not hits:
            п.setdefault("notes", []).append(f"⚠️ комплект {п['_kit']}: роль «{role}» не знайдена в прайсі")
            continue
        if role == "труба_тп":                 # найменша бухта, що покриває кількість
            need = _qty(п)
            hits.sort(key=lambda h: _coil(h.get("name_full") or h["name"]))
            hits = [h for h in hits if _coil(h.get("name_full") or h["name"]) >= need] or hits[-1:]
        п["_forced"] = hits[0]
        if cab:
            п.setdefault("notes", []).append(f"⚠️ шафа №{cab} під {n} контурів — перевірити")
