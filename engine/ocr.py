"""
engine/ocr.py — Читання замовлень через Gemini.
"""
import os, re, json, base64
from google import genai as genai_new
from google.genai import types as genai_types
from knowledge.knowledge import get_knowledge
from knowledge.rules     import get_rules
from engine.search       import BRAND_TOKENS, CATEGORY_ALIASES
from engine.project_spec import PROJECT_PROMPT_BLOCK, split_pdf, merge_duplicates

GEMINI_KEY    = os.environ.get("GEMINI_KEY", "")
gemini_client = genai_new.Client(api_key=GEMINI_KEY)
try:
    _GEMCFG = genai_types.GenerateContentConfig(temperature=0)
except Exception:
    _GEMCFG = None

DATA_DIR             = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
OCR_CORRECTIONS_FILE = os.path.join(DATA_DIR, "ocr_corrections.json")

OCR_MODEL = os.environ.get("GEMINI_OCR_MODEL", "gemini-2.5-flash")   # п.2: перемикач через ENV


def _gemini_call(contents):
    kwargs = {"model": OCR_MODEL, "contents": contents}
    if _GEMCFG is not None:
        kwargs["config"] = _GEMCFG
    return gemini_client.models.generate_content(**kwargs)

def _get_full_knowledge() -> str:
    k = get_knowledge()
    r = get_rules()
    if r:
        return f"{k}\n\n# ПРАВИЛА ЦЬОГО МАГАЗИНУ (вищий пріоритет):\n{r}"
    return k

def load_ocr_corrections() -> dict:
    if os.path.exists(OCR_CORRECTIONS_FILE):
        try:
            with open(OCR_CORRECTIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_ocr_correction(wrong: str, right: str):
    wrong = wrong.lower().strip()
    right = right.lower().strip()
    if not wrong or not right or wrong == right:
        return
    d = load_ocr_corrections()
    d[wrong] = right
    with open(OCR_CORRECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def _get_ocr_prompt_block() -> str:
    d = load_ocr_corrections()
    if not d:
        return ""
    lines = [f"  «{w}» часто насправді «{r}»" for w, r in d.items()]
    return ("\nЧАСТІ ПОМИЛКИ ЧИТАННЯ ЦЬОГО ПОЧЕРКУ:\n" + "\n".join(lines))

def parse_caption_brands(caption: str) -> dict:
    if not caption or not caption.strip():
        return {}
    result = {}
    chunks = re.split(r'[\n,;|]+', caption.lower())

    def _find_brands(text):
        out = {}
        for bk, bt in BRAND_TOKENS.items():
            for m in re.finditer(r'(?<![a-zа-яёіїєґ0-9])' + re.escape(bk) + r'(?![a-zа-яёіїєґ0-9])', text):
                out[m.start()] = bt
        return out

    def _find_cats(text):
        out = {}
        for alias, cat in CATEGORY_ALIASES.items():
            for m in re.finditer(r'(?<![a-zа-яёіїєґ])' + re.escape(alias), text):
                out[m.start()] = cat
        return out

    all_text = caption.lower()
    if re.search(r'(?<![а-я])(усе|все|всё|all)(?![а-я])', all_text):
        fb = _find_brands(all_text)
        if fb:
            first = fb[min(fb)]
            for cat in set(CATEGORY_ALIASES.values()):
                result[cat] = first
            return result

    global_brands = {}
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        brands = _find_brands(chunk)
        cats   = _find_cats(chunk)
        if brands and cats:
            for cpos, cat in cats.items():
                best, best_score = None, 1e9
                for bpos, bt in brands.items():
                    d     = bpos - cpos
                    score = d if d >= 0 else abs(d) + 100
                    if score < best_score:
                        best_score, best = score, bt
                if best and cat not in result:
                    result[cat] = best
        elif brands and not cats:
            global_brands.update(brands)

    # Якщо бренд БЕЗ категорії — НЕ розкидаємо на всі категорії!
    # Це підказка для Gemini але НЕ жорстке обмеження пошуку.
    # Зберігаємо як '_global' — search.py знатиме що це "м'яка" підказка.
    if not result and global_brands:
        first = global_brands[min(global_brands)]
        result['_global'] = first   # м'яка підказка без прив'язки до категорії
    return result

def normalize_photo(image_b64: str, caption: str = "", client_prefs: dict = None) -> list[dict]:
    brand_map  = parse_caption_brands(caption)
    brand_hint = ""
    if brand_map:
        # '_global' = м'яка підказка без категорії — показуємо Gemini як пріоритет
        global_b = brand_map.get('_global')
        cat_brands = {k: v for k, v in brand_map.items() if k != '_global'}
        lines = []
        if global_b:
            lines.append(f"  загальний пріоритет → {global_b[0]}")
        lines.extend(f"  {cat} → {toks[0]}" for cat, toks in cat_brands.items())
        if lines:
            brand_hint = "\n\n⚠️ ВИРОБНИКИ (пріоритет!):\n" + "\n".join(lines)
    ocr_block = _get_ocr_prompt_block()
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. Рукописний список замовлення.
ПІДКАЗКА: {caption}{brand_hint}{ocr_block}
БАЗА ЗНАНЬ:
{_get_full_knowledge()}
ПРАВИЛО ПРОДОВЖЕННЯ: рядок без типу ("0,5м – 3шт", "ф110×30° – 1шт") — продовження попереднього рядка:
допиши тип і діаметр попереднього.
ЗАВДАННЯ: прочитай кожен рядок, нормалізуй назву (КОРОТКО!), витягни кількість.
JSON масив ТІЛЬКИ:
[{{"original":"що написано","normalized":"коротка назва","qty":"кількість",
"category":"plastic_ppr/sewage/push_systems/shutoff_valves/pumps/radiators_radiatorsvalve/filtration/insulation/metal_plastic/adapters_reducers/heating/underfloor_heating/water_heaters/boilers/mixers_faucets/sanitary_ware/siphons_fittings/hoses/water_meters/towel_warmers/safety_valves/automation/fasteners_sealants/other",
"type":"труба/коліно/трійник/муфта/кран/гільза/перехід/...","dia":[110,50],"angle":87,"thread":"1/2 або null"}}]"""
    try:
        image_bytes = base64.b64decode(image_b64)
        resp = _gemini_call([
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            genai_types.Part.from_text(text=prompt),
        ])
        raw = resp.text.strip().replace('```json','').replace('```','').strip()
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception as e:
        return [{"original": f"Помилка OCR: {e}", "normalized": "", "qty": ""}]

def normalize_photos(images_b64: list[str], caption: str = "") -> list[dict]:
    """п.1: усі фото замовлення — ОДНИМ викликом, як сторінки одного документа."""
    if len(images_b64) == 1:
        return normalize_photo(images_b64[0], caption)
    brand_map = parse_caption_brands(caption)
    lines = []
    if brand_map.get('_global'):
        lines.append(f"  загальний пріоритет → {brand_map['_global'][0]}")
    lines.extend(f"  {c} → {t[0]}" for c, t in brand_map.items() if c != '_global')
    brand_hint = ("\n\n⚠️ ВИРОБНИКИ (пріоритет!):\n" + "\n".join(lines)) if lines else ""
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. {len(images_b64)} фото — це СТОРІНКИ ОДНОГО
замовлення по порядку. Читай їх як один список.
ПІДКАЗКА: {caption}{brand_hint}{_get_ocr_prompt_block()}
БАЗА ЗНАНЬ:
{_get_full_knowledge()}
ПРАВИЛО ПРОДОВЖЕННЯ: рядок без типу ("0,5м – 3шт", "ф110×30° – 1шт") — продовження попереднього рядка:
допиши тип і діаметр попереднього.
ЗАВДАННЯ: прочитай кожен рядок, нормалізуй назву (КОРОТКО!), витягни кількість.
JSON масив ТІЛЬКИ:
[{{"original":"що написано","normalized":"коротка назва","qty":"кількість",
"category":"plastic_ppr/sewage/push_systems/shutoff_valves/pumps/radiators_radiatorsvalve/filtration/insulation/metal_plastic/adapters_reducers/heating/underfloor_heating/water_heaters/boilers/mixers_faucets/sanitary_ware/siphons_fittings/hoses/water_meters/towel_warmers/safety_valves/automation/fasteners_sealants/other",
"type":"труба/коліно/трійник/муфта/кран/гільза/перехід/...","dia":[110,50],"angle":87,"thread":"1/2 або null"}}]"""
    try:
        parts = [genai_types.Part.from_bytes(data=base64.b64decode(b), mime_type="image/jpeg")
                 for b in images_b64]
        resp = _gemini_call(parts + [genai_types.Part.from_text(text=prompt)])
        raw = resp.text.strip().replace('```json', '').replace('```', '').strip()
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']') + 1]
        rows = json.loads(raw)
        if rows:
            return rows
        print(f"⚠️ OCR {OCR_MODEL}: порожній результат на {len(images_b64)} фото", flush=True)
    except Exception as e:
        print(f"⚠️ OCR {OCR_MODEL} одним викликом не вдався: {type(e).__name__}: {e}", flush=True)

    rows = []                       # запасний шлях: кожне фото окремо, як було раніше
    for i, b64 in enumerate(images_b64, 1):
        try:
            rows.extend(normalize_photo(b64, caption))
        except Exception as e:
            print(f"⚠️ OCR фото {i}: {type(e).__name__}: {e}", flush=True)
    return rows or [{"original": "Помилка OCR: жодне фото не прочитано",
                     "normalized": "", "qty": ""}]


def normalize_text(text: str, caption: str = "") -> list[dict]:
    ocr_block = _get_ocr_prompt_block()
    brand_map  = parse_caption_brands(caption)
    brand_hint = ""
    if brand_map:
        global_b   = brand_map.get('_global')
        cat_brands = {k: v for k, v in brand_map.items() if k != '_global'}
        lines = []
        if global_b:
            lines.append(f"  загальний пріоритет → {global_b[0]}")
        lines.extend(f"  {cat} → {toks[0]}" for cat, toks in cat_brands.items())
        if lines:
            brand_hint = "\n\n⚠️ ВИРОБНИКИ (пріоритет!):\n" + "\n".join(lines)
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. Текстовий запит.
ПІДКАЗКА: {caption}{brand_hint}{ocr_block}
БАЗА ЗНАНЬ:
{_get_full_knowledge()}
ЗАПИТ: {text}
JSON масив ТІЛЬКИ:
[{{"original":"...","normalized":"...","qty":"...","category":"...","type":"тип одним словом","dia":[25],"angle":null,"thread":"3/4 або null"}}]"""
    try:
        resp = _gemini_call([genai_types.Part.from_text(text=prompt)])
        raw  = resp.text.strip().replace('```json','').replace('```','').strip()
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception as e:
        return [{"original": text, "normalized": text, "qty": "", "category": "other"}]

def normalize_pdf(pdf_b64: str, caption: str = "") -> list[dict]:
    ocr_block  = _get_ocr_prompt_block()
    brand_map  = parse_caption_brands(caption)
    brand_hint = ""
    if brand_map:
        global_b = brand_map.get('_global')
        cat_brands = {k: v for k, v in brand_map.items() if k != '_global'}
        lines = []
        if global_b:
            lines.append(f"  загальний пріоритет → {global_b[0]}")
        lines.extend(f"  {cat} → {toks[0]}" for cat, toks in cat_brands.items())
        if lines:
            brand_hint = "\n⚠️ ВИРОБНИКИ (пріоритет!):\n" + "\n".join(lines)
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. ПРОЕКТНА СПЕЦИФІКАЦІЯ (PDF).
ПІДКАЗКА: {caption}{brand_hint}{ocr_block}
{PROJECT_PROMPT_BLOCK}
БАЗА ЗНАНЬ:
{_get_full_knowledge()}
JSON масив ТІЛЬКИ:
[{{"original":"як у специфікації","normalized":"коротка назва","qty":"к-ть з од","category":"...","section":"розділ","type":"тип одним словом","dia":[32],"angle":null,"thread":null}}]"""
    def _call(chunk: bytes) -> list[dict]:
        resp = _gemini_call([
            genai_types.Part.from_bytes(data=chunk, mime_type="application/pdf"),
            genai_types.Part.from_text(text=prompt),
        ])
        raw = resp.text.strip().replace('```json', '').replace('```', '').strip()
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']') + 1]
        return json.loads(raw)

    pdf_bytes = base64.b64decode(pdf_b64)
    try:                                   # основний шлях: весь документ одним викликом
        rows = _call(pdf_bytes)
        if rows:
            return merge_duplicates(rows)
    except (json.JSONDecodeError, ValueError) as e:
        rows_err = str(e)
    except Exception as e:                 # помилка API — пробуємо частинами
        rows_err = str(e)
    else:
        rows_err = "порожній результат"

    rows: list[dict] = []                  # запасний шлях: по частинах
    for chunk in split_pdf(pdf_bytes):
        try:
            rows.extend(_call(chunk))
        except Exception:
            continue
    if not rows:
        return [{"original": f"Помилка PDF: {rows_err}", "normalized": "", "qty": "", "category": "other"}]
    return merge_duplicates(rows)
