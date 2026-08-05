"""config/settings.py — Всі константи, env-змінні та налаштування проекту."""
import os

# ── Telegram / Anthropic ──────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")
ADMIN_ID       = 395121797

# ── Шляхи ─────────────────────────────────────────────────────────────────────
DATA_DIR           = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
PENDING_FIXES_FILE = os.path.join(DATA_DIR, "pending_fixes.json")
USAGE_LOG_FILE     = os.path.join(DATA_DIR, "usage_log.json")
CATALOG_PATH       = os.path.join(DATA_DIR, "catalog.json")
EMBEDDINGS_PATH    = os.path.join(DATA_DIR, "catalog_embeddings.npz")

# ── Таймери ───────────────────────────────────────────────────────────────────
BATCH_TIMEOUT      = 4   # секунди: буфер фото перед обробкою
HINT_TTL           = 120 # секунди: підказка виробника живе 2 хв

# ── Пагінація ─────────────────────────────────────────────────────────────────
PAGE_SIZE          = 5   # кількість записів кешу на сторінку

# ── Voyage AI ─────────────────────────────────────────────────────────────────
VOYAGE_API_KEY     = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL       = "voyage-3"
THRESHOLD_AUTO     = 0.82
THRESHOLD_CONFIRM  = 0.72

# ── Claude ────────────────────────────────────────────────────────────────────
CLAUDE_MODEL       = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS  = 1024
