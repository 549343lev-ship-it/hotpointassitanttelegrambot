"""diag.py — діагностика бота. Запускати в Render Shell: python diag.py

Показує, що саме зламано: диск, embeddings, кеш, доступ до моделей, і як читається фото.
Нічого не змінює.
"""
from __future__ import annotations

import base64
import os
import sys

OK, BAD = "✅", "❌"


def line(flag: str, text: str) -> None:
    print(f"{flag} {text}", flush=True)


def check_disk() -> None:
    print("\n=== ДИСК І ДАНІ ===")
    from config.settings import DATA_DIR, CATALOG_PATH, EMBEDDINGS_PATH
    line(OK if DATA_DIR == "/var/data" else BAD,
         f"DATA_DIR = {DATA_DIR}" + ("" if DATA_DIR == "/var/data" else "  ← диск не змонтований!"))
    for path in (CATALOG_PATH, EMBEDDINGS_PATH,
                 os.path.join(DATA_DIR, "normalization_cache.json"),
                 os.path.join(DATA_DIR, "clients")):
        exists = os.path.exists(path)
        size = ""
        if exists and os.path.isfile(path):
            size = f" ({os.path.getsize(path) / 1_048_576:.1f} МБ)"
        line(OK if exists else BAD, f"{path}{size}")


def check_catalog() -> None:
    print("\n=== КАТАЛОГ І ПОШУК ===")
    try:
        from catalog.catalog import CATALOG
        line(OK if CATALOG else BAD, f"товарів у каталозі: {len(CATALOG)}")
    except Exception as e:
        line(BAD, f"каталог: {type(e).__name__}: {e}")
    try:
        from engine.voyage_search import EMBEDDINGS_FILE
        if os.path.exists(EMBEDDINGS_FILE):
            import numpy as np
            data = np.load(EMBEDDINGS_FILE, allow_pickle=True)
            key = "embeddings" if "embeddings" in data else data.files[0]
            line(OK, f"embeddings: {data[key].shape}")
        else:
            line(BAD, f"embeddings немає ({EMBEDDINGS_FILE}) → Voyage-пошук вимкнений, "
                      f"запусти: python build_embeddings_server.py")
    except Exception as e:
        line(BAD, f"embeddings: {type(e).__name__}: {e}")
    try:
        from clients.cache import get_cache_stats
        line(OK, f"кеш: {get_cache_stats()}")
    except Exception as e:
        line(BAD, f"кеш: {type(e).__name__}: {e}")


def check_gemini(image_path: str | None) -> None:
    print("\n=== GEMINI ===")
    from engine import ocr
    line(OK, f"OCR_MODEL = {ocr.OCR_MODEL}")
    for model in dict.fromkeys([ocr.OCR_MODEL, "gemini-2.5-pro", "gemini-2.5-flash"]):
        try:
            old, ocr.OCR_MODEL = ocr.OCR_MODEL, model
            from google.genai import types as gt
            resp = ocr._gemini_call([gt.Part.from_text(text="Відповідай одним словом: працює")])
            line(OK, f"{model}: {resp.text.strip()[:40]}")
        except Exception as e:
            line(BAD, f"{model}: {type(e).__name__}: {str(e)[:200]}")
        finally:
            ocr.OCR_MODEL = old

    if not image_path:
        print("   (щоб перевірити читання фото: python diag.py фото1.jpg фото2.jpg)")
        return
    imgs = []
    for p in image_path if isinstance(image_path, list) else [image_path]:
        with open(p, "rb") as f:
            imgs.append(base64.b64encode(f.read()).decode())
    rows = ocr.normalize_photos(imgs, "")
    line(OK if len(rows) > 1 else BAD, f"normalize_photos → {len(rows)} позицій")
    for r in rows[:5]:
        print("   ", r.get("original", "")[:90], "|", r.get("qty", ""))


def check_anthropic() -> None:
    print("\n=== ANTHROPIC ===")
    for var in ("ORDER_CTX_MODEL", "REVIEW_MODEL", "PICKER_MODEL"):
        print(f"    {var} = {os.environ.get(var, '(не задано)')}")
    try:
        from engine.search import claude
        import engine.order_context as oc
        resp = claude.messages.create(model=oc.MODEL, max_tokens=16,
                                      messages=[{"role": "user", "content": "Відповідай одним словом: працює"}])
        line(OK, f"{oc.MODEL}: {resp.content[0].text.strip()[:40]}")
    except Exception as e:
        line(BAD, f"{type(e).__name__}: {str(e)[:200]}")


def check_modules() -> None:
    print("\n=== НОВІ МОДУЛІ ===")
    for mod in ("engine.order_context", "engine.kits", "engine.cross_check",
                "engine.project_spec", "engine.review"):
        try:
            __import__(mod)
            line(OK, mod)
        except Exception as e:
            line(BAD, f"{mod}: {type(e).__name__}: {e}")
    try:
        import engine.ocr as o
        line(OK if "запасний шлях: кожне фото" in open(o.__file__, encoding="utf-8").read() else BAD,
             "ocr.py з запасним шляхом по одному фото")
    except Exception as e:
        line(BAD, f"ocr.py: {e}")


if __name__ == "__main__":
    check_disk()
    check_modules()
    check_catalog()
    check_gemini(sys.argv[1:] or None)
    check_anthropic()
    print("\nГотово.")
