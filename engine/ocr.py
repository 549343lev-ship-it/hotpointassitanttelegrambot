"""
engine/ocr.py — Читання замовлень через Gemini.
"""
import os, re, json, base64
from google import genai as genai_new
from google.genai import types as genai_types
from knowledge.knowledge import get_knowledge
from knowledge.rules     import get_rules
from engine.search       import BRAND_TOKENS, CATEGORY_ALIASES

GEMINI_KEY    = os.environ.get("GEMINI_KEY", "")
gemini_client = genai_new.Client(api_key=GEMINI_KEY)
try:
    _GEMCFG = genai_types.GenerateContentConfig(temperature=0)
except Exception:
    _GEMCFG = None

DATA_DIR             = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
OCR_CORRECTIONS_FILE = os.path.join(DATA_DIR, "ocr_corrections.json")

def _gemini_call(contents):
    kwargs = {"model": "gemini-2.5-flash", "contents": contents}
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

    if not result and global_brands:
        first = global_brands[min(global_brands)]
        for cat in set(CATEGORY_ALIASES.values()):
            result[cat] = first
    return result

def normalize_photo(image_b64: str, caption: str = "", client_prefs: dict = None) -> list[dict]:
    brand_map  = parse_caption_brands(caption)
    brand_hint = ""
    if brand_map:
        lines      = [f"  {cat} → {toks[0]}" for cat, toks in brand_map.items()]
        brand_hint = "\n\n⚠️ ВИРОБНИКИ (суворо!):\n" + "\n".join(lines)
    ocr_block = _get_ocr_prompt_block()
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. Рукописний список замовлення.
ПІДКАЗКА: {caption}{brand_hint}{ocr_block}
БАЗА ЗНАНЬ:
{_get_full_knowledge()}
ЗАВДАННЯ: прочитай кожен рядок, нормалізуй назву (КОРОТКО!), витягни кількість.
JSON масив ТІЛЬКИ:
[{{"original":"що написано","normalized":"коротка назва","qty":"кількість",
"category":"plastic_ppr/sewage/push_systems/shutoff_valves/pumps/radiators_radiatorsvalve/filtration/insulation/metal_plastic/adapters_reducers/other",
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

def normalize_text(text: str, caption: str = "") -> list[dict]:
    ocr_block = _get_ocr_prompt_block()
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. Текстовий запит.
ПІДКАЗКА: {caption}{ocr_block}
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
        lines      = [f"  {cat} → {toks[0]}" for cat, toks in brand_map.items()]
        brand_hint = "\n⚠️ ВИРОБНИКИ (суворо!):\n" + "\n".join(lines)
    prompt = f"""Ти — досвідчений менеджер з продажу сантехніки. ПРОЕКТНА СПЕЦИФІКАЦІЯ (PDF).
ПІДКАЗКА: {caption}{brand_hint}{ocr_block}
БАЗА ЗНАНЬ:
{_get_full_knowledge()}
JSON масив ТІЛЬКИ:
[{{"original":"як у специфікації","normalized":"коротка назва","qty":"к-ть з од","category":"...","section":"розділ","type":"тип одним словом","dia":[32],"angle":null,"thread":null}}]"""
    try:
        pdf_bytes = base64.b64decode(pdf_b64)
        resp = _gemini_call([
            genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            genai_types.Part.from_text(text=prompt),
        ])
        raw = resp.text.strip().replace('```json','').replace('```','').strip()
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception as e:
        return [{"original": f"Помилка PDF: {e}", "normalized": "", "qty": "", "category": "other"}]
