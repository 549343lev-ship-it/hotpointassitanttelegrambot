"""
rules.py — Динамічні правила конкретного магазину.

Різниця від knowledge.py:
  knowledge.py  — незмінна база (хто ти, як мислиш, скорочення, логіка)
  rules.py      — живий досвід цього магазину (росте з кожним виправленням)

Правила додаються командою "правило <текст>" через Telegram.
Зберігаються у rules.txt (гілка botdata на GitHub).

Gemini отримує: knowledge.get_knowledge() + rules.get_rules() — разом.
Якщо є конфлікт — правила мають вищий пріоритет (вони свіжіші і конкретніші).
"""

import os

DATA_DIR           = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
RULES_FILE         = os.path.join(DATA_DIR, "rules.txt")
PENDING_RULES_FILE = os.path.join(DATA_DIR, "pending_rules.json")  # правила що чекають підтвердження адміна


# ─── Публічний API ───────────────────────────────────────────────────────────

def get_rules() -> str:                          # читає rules.txt і повертає весь текст для промпту Gemini
    if not os.path.exists(RULES_FILE):
        return ""
    with open(RULES_FILE, encoding="utf-8") as f:
        return f.read().strip()


def add_rule(rule: str):                         # дописує одне правило в кінець rules.txt
    with open(RULES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {rule}\n")


def delete_rule(n: int) -> tuple[bool, str]:     # видаляє правило за номером рядка (1-based); повертає (успіх, текст рядка)
    lines = get_rules().splitlines()
    if 1 <= n <= len(lines):
        removed = lines.pop(n - 1)
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True, removed
    return False, f"Рядка {n} немає (всього {len(lines)})"


def get_rules_count() -> int:                    # повертає кількість рядків у rules.txt
    return len(get_rules().splitlines())


# ─── Правила що чекають підтвердження адміна ─────────────────────────────────

def load_pending_rules() -> list:                # завантажує список правил що очікують підтвердження адміна
    import json
    if os.path.exists(PENDING_RULES_FILE):
        try:
            with open(PENDING_RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_pending_rules(rules: list):             # зберігає оновлений список правил на підтвердження
    import json
    with open(PENDING_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def add_pending_rule(rule: str, user_id: int, username: str) -> int:  # додає правило від користувача в чергу; повертає розмір черги
    import time
    rules = load_pending_rules()
    rules.append({
        "rule":     rule,
        "user_id":  user_id,
        "username": username,
        "date":     time.strftime("%Y-%m-%d %H:%M"),
    })
    save_pending_rules(rules)
    return len(rules)
