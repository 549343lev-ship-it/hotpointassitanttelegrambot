"""
logger.py — Логування та статистика.

  log_usage()        — фіксує кожне замовлення (хто, скільки, % знайдено)
  get_usage_stats()  — форматований звіт для адміна
  log_not_found()    — накопичує незнайдені позиції
  get_catalog_gaps() — топ незнайдених ("діри каталогу")
"""

import os
import json
import time

DATA_DIR       = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
USAGE_LOG_FILE = os.path.join(DATA_DIR, "usage_log.json")   # лог кожного замовлення
NOT_FOUND_FILE = os.path.join(DATA_DIR, "not_found_log.json")  # лог незнайдених позицій


# ─── Статистика використання ─────────────────────────────────────────────────

def log_usage(chat_id: int, username: str, total: int, found: int, files: int):  # записує одне замовлення в лог (chat, дата, к-ть позицій, знайдено)
    try:
        log = []
        if os.path.exists(USAGE_LOG_FILE):
            with open(USAGE_LOG_FILE, encoding="utf-8") as f:
                log = json.load(f)
        log.append({
            "chat_id":  chat_id,
            "username": username,
            "date":     time.strftime("%Y-%m-%d %H:%M"),
            "total":    total,
            "found":    found,
            "files":    files,
        })
        log = log[-1000:]   # тримаємо не більше 1000 останніх записів
        with open(USAGE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ log_usage: {e}")


def get_usage_stats() -> str:   # повертає форматований текстовий звіт по користувачах для адміна
    if not os.path.exists(USAGE_LOG_FILE):
        return "📊 Статистика порожня."
    try:
        with open(USAGE_LOG_FILE, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return "⚠️ Помилка читання логу."
    if not log:
        return "📊 Статистика порожня."

    users = {}
    for rec in log:
        u = rec.get("username", "?")
        if u not in users:
            users[u] = {"orders": 0, "total": 0, "found": 0}
        users[u]["orders"] += 1
        users[u]["total"]  += rec.get("total", 0)
        users[u]["found"]  += rec.get("found", 0)

    lines = [f"📊 *Статистика* ({len(log)} замовлень)\n"]
    for u, s in sorted(users.items(), key=lambda x: -x[1]["orders"]):
        pct = int(s["found"] / s["total"] * 100) if s["total"] else 0
        lines.append(f"👤 {u}: {s['orders']} замовл., {s['total']} поз., {pct}% знайдено")
    lines.append("\n🕐 Останні 5:")
    for rec in log[-5:]:
        lines.append(f"• {rec['date']} — {rec['username']}: {rec['found']}/{rec['total']}")
    return "\n".join(lines)


# ─── Діри каталогу ───────────────────────────────────────────────────────────

def log_not_found(rows: list):  # дописує незнайдені позиції замовлення в окремий лог для аналізу
    if not rows:
        return
    import re
    try:
        log = []
        if os.path.exists(NOT_FOUND_FILE):
            with open(NOT_FOUND_FILE, encoding="utf-8") as f:
                log = json.load(f)
        for r in rows:
            log.append({
                "original":   r.get("original", "")[:80],
                "normalized": r.get("normalized", "")[:80],
                "date":       time.strftime("%Y-%m-%d"),
            })
        log = log[-2000:]   # тримаємо не більше 2000 останніх незнайдених
        with open(NOT_FOUND_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ log_not_found: {e}")


def get_catalog_gaps() -> str:  # будує текстовий топ-20 найчастіше незнайдених позицій — що треба додати в прайси
    import re
    if not os.path.exists(NOT_FOUND_FILE):
        return "🕳 Порожньо — все знаходилось."
    try:
        with open(NOT_FOUND_FILE, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return "⚠️ Помилка читання."
    if not log:
        return "🕳 Порожньо."

    groups = {}
    for rec in log:
        key = re.sub(r"\s+", " ",
                     (rec.get("normalized") or rec.get("original", "")).lower()).strip()
        if not key:
            continue
        if key not in groups:
            groups[key] = {
                "n":    0,
                "show": rec.get("normalized") or rec.get("original", ""),
                "orig": rec.get("original", ""),
                "last": rec.get("date", ""),
            }
        groups[key]["n"] += 1
        groups[key]["last"] = rec.get("date", groups[key]["last"])

    top   = sorted(groups.values(), key=lambda g: -g["n"])[:20]
    lines = [f"🕳 ДІРИ КАТАЛОГУ — топ незнайдених ({len(log)} записів):\n"]
    for i, g in enumerate(top, 1):
        lines.append(f"{i}. ×{g['n']}  {g['show'][:48]}")
        if g['orig'] and g['orig'].lower() != g['show'].lower():
            lines.append(f"      (писали: {g['orig'][:45]})")
    lines.append("\n➡️ Ці товари варто додати в прайси або створити правило.")
    return "\n".join(lines)
