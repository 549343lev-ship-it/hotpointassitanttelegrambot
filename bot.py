"""
bot.py — Telegram бот підбору сантехніки (Hotpoint).
Gemini OCR → keyword пошук → Claude вибір → Excel.
"""
import os, json, re, base64, threading, time
from io import BytesIO

import telebot
from flask import Flask, request as flask_request
from telebot.types import (Update, InlineKeyboardMarkup, InlineKeyboardButton,
                            ReplyKeyboardMarkup, KeyboardButton)
import anthropic
import pandas as pd
from google import genai as genai_new
from google.genai import types as genai_types

# ═══════════════════════════════════════════════════════════════════════════════
# ІНІЦІАЛІЗАЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")
GEMINI_KEY     = os.environ.get("GEMINI_KEY", "")

# КРИТИЧНО: threaded=False!
# За замовчуванням telebot виконує хендлери у своєму ThreadPool, де винятки
# зберігаються мовчки і піднімаються тільки в polling. З webhook вони ЗНИКАЮТЬ:
# process_new_updates повертається "успішно", а відповідь не відправляється.
_ExcBase = getattr(telebot, 'ExceptionHandler', object)

class _LogExc(_ExcBase):
    def handle(self, exception):
        import traceback
        print(f"❌ ПОМИЛКА В ХЕНДЛЕРІ: {exception}", flush=True)
        traceback.print_exc()
        return True

try:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False, exception_handler=_LogExc())
except TypeError:
    # стара версія telebot без exception_handler
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

claude        = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
gemini_client = genai_new.Client(api_key=GEMINI_KEY)

CATALOG_PATH = "catalog.json"
RULES_FILE   = "rules.txt"

# ═══════════════════════════════════════════════════════════════════════════════
# АДМІН
# ═══════════════════════════════════════════════════════════════════════════════
ADMIN_ID           = 395121797
PENDING_RULES_FILE = "pending_rules.json"
USAGE_LOG_FILE     = "usage_log.json"

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def log_usage(chat_id, username, total, found, files):
    try:
        log = []
        if os.path.exists(USAGE_LOG_FILE):
            with open(USAGE_LOG_FILE, encoding="utf-8") as f:
                log = json.load(f)
        log.append({"chat_id": chat_id, "username": username,
                    "date": time.strftime("%Y-%m-%d %H:%M"),
                    "total": total, "found": found, "files": files})
        log = log[-1000:]
        with open(USAGE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ log_usage: {e}")

def get_usage_stats() -> str:
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

def load_pending_rules():
    if os.path.exists(PENDING_RULES_FILE):
        try:
            with open(PENDING_RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_pending_rules(rules):
    with open(PENDING_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════════════════════════════════════════
# КАТАЛОГ
# ═══════════════════════════════════════════════════════════════════════════════
CATALOG_FILES = [
    ("adapters_reducers","adapters_reducers"),("automation","automation"),
    ("boilers","boilers"),("fasteners_sealants","fasteners_sealants"),
    ("filtration","filtration"),("heating","heating"),("hoses","hoses"),
    ("insulation","insulation"),("metal_plastic","metal_plastic"),
    ("mixers_faucets","mixers_faucets"),("plastic_ppr","plastic_ppr"),
    ("pumps","pumps"),("push_systems","push_systems"),
    ("radiators_radiatorsvalve","radiators_radiatorsvalve"),
    ("safety_valves","safety_valves"),("sanitary_ware","sanitary_ware"),
    ("sewage","sewage"),("shutoff_valves","shutoff_valves"),
    ("siphons_fittings","siphons_fittings"),("towel_warmers","towel_warmers"),
    ("underfloor_heating","underfloor_heating"),("water_heaters","water_heaters"),
    ("water_meters","water_meters"),
]

def is_header_row(name, artikul, or_val):
    name = str(name).strip()
    if not name or name == 'nan':
        return True
    art = str(artikul).strip()
    if art and art not in ('nan', '0', ''):
        return False
    try:
        if float(or_val) > 0:
            return False
    except Exception:
        pass
    return True

def build_catalog_from_xlsx():
    catalog = []
    bot_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    search_dirs = ['.', 'src', bot_dir]
    for key, category in CATALOG_FILES:
        found = False
        for d in search_dirs:
            path = os.path.join(d, f"{key}.xlsx")
            if not os.path.exists(path):
                continue
            try:
                df = pd.read_excel(path, header=0)
                cols = list(df.columns)
                rename = {}
                if len(cols) >= 1: rename[cols[0]] = 'name'
                if len(cols) >= 2: rename[cols[1]] = 'artikul'
                if len(cols) >= 3: rename[cols[2]] = 'price'
                df = df.rename(columns=rename)
                count = 0
                for _, row in df.iterrows():
                    name    = str(row.get('name', '')).strip()
                    artikul = row.get('artikul', '')
                    price   = row.get('price', 0)
                    if is_header_row(name, artikul, price):
                        continue
                    try:
                        p = float(price)
                    except Exception:
                        p = 0.0
                    art = str(artikul).strip()
                    name_full = name
                    name_clean = re.sub(r'\s*\{[^}]+\}', '', name).strip()
                    catalog.append({
                        'name': name_clean, 'name_full': name_full,
                        'artikul': art if art != 'nan' else '',
                        'category': category, 'price': p,
                    })
                    count += 1
                print(f"  ✅ {key}: {count} товарів")
                found = True
                break
            except Exception as e:
                print(f"  ❌ {key}: {e}")
        if not found:
            print(f"  ⚠️ {key}.xlsx не знайдено")
    return catalog

print("📦 Завантажую каталог...", flush=True)
if os.path.exists(CATALOG_PATH):
    with open(CATALOG_PATH, encoding="utf-8") as f:
        CATALOG = json.load(f)
    print(f"✅ Каталог: {len(CATALOG)} позицій", flush=True)
else:
    print("⚙️ Будую catalog.json...", flush=True)
    CATALOG = build_catalog_from_xlsx()
    if CATALOG:
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(CATALOG, f, ensure_ascii=False)
        print(f"✅ Збережено: {len(CATALOG)} позицій", flush=True)

# Видаляємо виведені
before = len(CATALOG)
CATALOG = [c for c in CATALOG if 'виведено з асортименту' not in c.get('name','').lower()]
if len(CATALOG) < before:
    print(f"🧹 Видалено {before - len(CATALOG)} виведених", flush=True)

# Індексуємо токени (lazy)
_tokens_built = False

def tokenize(text: str) -> set:
    t = text.lower()
    t = re.sub(r'(\d+)/(\d+)', r'\1_\2', t)
    t = re.sub(r'[фfдd]\s*(\d)', r'\1', t)
    t = re.sub(r'(\d+)\s*[хxX×]\s*(\d+)/(\d+)', r'\1 \2_\3', t)
    def strip_thick(m):
        d1, d2 = m.group(1), m.group(2)
        if '_' in d2: return m.group(0)
        try:
            if float(d2.replace(',', '.')) < 15: return d1
        except Exception: pass
        return m.group(0)
    t = re.sub(r'(\d+)\s*[хxX×]\s*(\d+(?:[.,]\d+)?)', strip_thick, t)
    t = re.sub(r'(8[67])[.,]5', r'\1', t)
    t = re.sub(r'(?<![0-9_])\d+[.,]\d+(?![0-9_])', '', t)
    return set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+_[0-9]+|[0-9]+', t))

def ensure_tokens():
    global _tokens_built
    if not _tokens_built:
        print("🔨 Індексую токени...", flush=True)
        for item in CATALOG:
            item['_tokens'] = tokenize(item['name'])
        print("✅ Індексація завершена", flush=True)
        _tokens_built = True

# ═══════════════════════════════════════════════════════════════════════════════
# ПРАВИЛА
# ═══════════════════════════════════════════════════════════════════════════════
def get_rules() -> str:
    if not os.path.exists(RULES_FILE): return ""
    with open(RULES_FILE, encoding="utf-8") as f: return f.read().strip()

def add_rule(rule: str):
    with open(RULES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {rule}\n")

# ═══════════════════════════════════════════════════════════════════════════════
# ПОШУК
# ═══════════════════════════════════════════════════════════════════════════════
BRAND_TOKENS = {
    'raftec':      ['raftec', 'RAFTEC'],
    'рафтек':     ['raftec', 'RAFTEC'],
    'ekoplastik':  ['ekoplastik', 'Ekoplastik'],
    'екопластик':  ['ekoplastik', 'Ekoplastik'],
    'asg':         ['asg', 'ASG'],
    'асг':         ['asg', 'ASG'],
    'ostendorf':   ['ostendorf', 'OSTENDORF', 'Safe'],
    'остендорф':   ['ostendorf', 'OSTENDORF', 'Safe'],
    'plm':         ['plm', 'PLM'],
    'hidros':      ['hidros', 'Hidros', 'HIDROS'],
    'хідрос':      ['hidros', 'Hidros'],
    'termojet':    ['termojet', 'Termojet'],
    'термоджет':   ['termojet', 'Termojet'],
    'rehau':       ['rehau', 'REHAU'],
    'рехау':       ['rehau', 'REHAU'],
    'ecosoft':     ['ecosoft', 'Ecosoft'],
    'екософт':     ['ecosoft', 'Ecosoft'],
}

DEFAULT_BRAND_PRIORITY = {
    'sewage':         [['ostendorf','OSTENDORF','Safe'], ['asg','ASG']],
    'plastic_ppr':    [['ekoplastik','Ekoplastik'], ['asg','ASG'], ['raftec','RAFTEC']],
    'shutoff_valves': [['raftec','RAFTEC']],
    'adapters_reducers': [['raftec','RAFTEC']],
    'filtration':     [['ecosoft','Ecosoft']],
    'radiators_radiatorsvalve': [['hidros','Hidros']],
    'pumps':          [['termojet','Termojet']],
    'insulation':     [['plm','PLM']],
    'push_systems':   [['raftec','RAFTEC']],
}

def keyword_search(query: str, top_n: int = 12, brand_tokens: list = None) -> list[dict]:
    ensure_tokens()
    q_tokens  = tokenize(query)
    q_numbers = set(re.findall(r'\d+', query.lower()))
    q_words   = q_tokens - q_numbers
    if not q_tokens: return []

    brand_lc = [t.lower() for t in brand_tokens] if brand_tokens else None
    scores = []
    for item in CATALOG:
        if brand_lc and not any(t in item['name'].lower() for t in brand_lc):
            continue
        it = item.get('_tokens', set())
        num_hits  = len(q_numbers & it)
        word_hits = len(q_words & it)
        if num_hits == 0 and word_hits == 0: continue
        raw = num_hits * 3 + word_hits
        penalty = max(0, len(it) - len(q_tokens)) * 0.1
        if '(п/з)' in item['name']: penalty += 5
        pct = min(int(raw / max(len(q_numbers)*3 + len(q_words), 1) * 100), 100)
        scores.append((raw - penalty, pct, item))
    scores.sort(key=lambda x: -x[0])
    result = []
    for _, pct, item in scores[:top_n]:
        c = dict(item)
        c['_match_pct'] = pct
        result.append(c)
    return result

def parse_caption_brands(caption: str) -> dict:
    if not caption: return {}
    cap_lc = caption.lower().strip()
    result = {}
    CATEGORY_ALIASES = {
        'каналізація': 'sewage', 'канал': 'sewage',
        'пайка': 'plastic_ppr', 'ппр': 'plastic_ppr', 'пластик': 'plastic_ppr',
        'крани': 'shutoff_valves', 'кран': 'shutoff_valves', 'арматура': 'shutoff_valves',
        'пуш': 'push_systems', 'push': 'push_systems', 'пекс': 'push_systems',
        'насос': 'pumps', 'радіатор': 'radiators_radiatorsvalve',
        'утеплювач': 'insulation', 'фільтр': 'filtration',
    }
    found_brands = {}
    for bkey, btoks in BRAND_TOKENS.items():
        if re.search(r'(?<![a-zа-яёіїєґ0-9])' + re.escape(bkey) + r'(?![a-zа-яёіїєґ0-9])', cap_lc):
            for m in re.finditer(re.escape(bkey), cap_lc):
                found_brands[m.start()] = btoks
    found_cats = {}
    for alias, cat in CATEGORY_ALIASES.items():
        for m in re.finditer(re.escape(alias), cap_lc):
            found_cats[m.start()] = cat
    if not found_brands: return {}
    is_global = bool(re.search(r'\b(усе|все|all)\b', cap_lc))
    if is_global or not found_cats:
        first_brand = sorted(found_brands.items())[0][1]
        for cat in set(CATEGORY_ALIASES.values()):
            result[cat] = first_brand
        return result
    for cat_pos, cat_val in found_cats.items():
        best, best_dist = None, 999
        for bp, btoks in found_brands.items():
            dist = bp - cat_pos
            if -30 < dist < 60 and abs(dist) < best_dist:
                best_dist = abs(dist)
                best = btoks
        if best and cat_val not in result:
            result[cat_val] = best
    return result

# ═══════════════════════════════════════════════════════════════════════════════
# ЗНАННЯ САНТЕХНІКИ (коротко для Gemini)
# ═══════════════════════════════════════════════════════════════════════════════
ЗНАННЯ = """
КАНАЛІЗАЦІЯ: 90°→ шукати 87 (без ,5). ASG→HTR, OSTENDORF→HT Safe. умивальник=ф40, ванна=ф50, стояк=ф110.
PPR: МРЗ/МРН=зовнішня різьба, МРВ=внутрішня. ВВ→МРЗ, ВЗ→МРВ. Ekoplastik труби=EVO. Fiber=опалення.
PUSH (натяжна, PEX-A): маркер "натяжний"+"PUSH". PUSH16≈PPR20. Гільзи обов'язкові!
Труба 0,3м→0,25м. EK/ЄК=Ekoplastik. Бінокль=кран кутовий Hidros. Компенсаційна муфта=Муфта вставна.
НОРМАЛІЗАЦІЯ: коротко! "Труба PPR Fiber ф25 RAFTEC" (без товщини!), "Коліно канал ф110 87 OSTENDORF"
"""

# ═══════════════════════════════════════════════════════════════════════════════
# НОРМАЛІЗАЦІЯ (Gemini)
# ═══════════════════════════════════════════════════════════════════════════════
def normalize_photo(image_b64: str, caption: str = "", client_prefs: dict = None) -> list[dict]:
    rules = get_rules()
    rules_block = f"\nПравила менеджера:\n{rules}" if rules else ""
    brand_map = parse_caption_brands(caption)

    brand_hint = ""
    if brand_map:
        lines = [f"  {cat} → {toks[0]}" for cat, toks in brand_map.items()]
        brand_hint = "\n\n⚠️ ВИРОБНИКИ (суворо!):\n" + "\n".join(lines)
        brand_hint += "\nПриклад: каналізація→ostendorf значить ВСЯ каналізація OSTENDORF (НЕ ASG!)"

    prompt = f"""Ти — експерт сантехніки України. Рукописний список замовлення.
ПІДКАЗКА: {caption}{brand_hint}{rules_block}
ЗНАННЯ: {ЗНАННЯ}

ЗАВДАННЯ: прочитай кожен рядок, нормалізуй назву (КОРОТКО!), витягни кількість.
JSON масив ТІЛЬКИ:
[{{"original":"що написано","normalized":"коротка назва для пошуку","qty":"кількість","category":"plastic_ppr/sewage/push_systems/shutoff_valves/pumps/radiators_radiatorsvalve/filtration/insulation/other"}}]"""

    try:
        image_bytes = base64.b64decode(image_b64)
        resp = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                genai_types.Part.from_text(text=prompt)
            ]
        )
        raw = resp.text.strip().replace('```json','').replace('```','').strip()
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception as e:
        return [{"original": f"Помилка OCR: {e}", "normalized": "", "qty": ""}]

# ═══════════════════════════════════════════════════════════════════════════════
# CLAUDE ВИБІР
# ═══════════════════════════════════════════════════════════════════════════════
def _parse_claude_json(raw: str) -> list:
    raw = re.sub(r'```\w*', '', raw).strip()
    start, end = raw.find('['), raw.rfind(']') + 1
    if start != -1 and end > 0:
        try: return json.loads(raw[start:end])
        except Exception: pass
    objects = []
    for m in re.finditer(r'\{[^{}]*\}', raw[start:] if start != -1 else raw):
        try: objects.append(json.loads(m.group()))
        except Exception: pass
    return objects

def claude_pick_batch(позиції: list[dict], _retry=True) -> list[dict]:
    запити = []
    for i, пос in enumerate(позиції):
        brand_note = f"\n   ⚠️ ТІЛЬКИ: {пос['required_brand']}" if пос.get('required_brand') else ""
        кандидати = "\n".join(f"  {j+1}. [{c.get('_match_pct',0)}%] {c['name']}"
                               for j, c in enumerate(пос['candidates']))
        запити.append(f"{i+1}. {пос['normalized']}{brand_note}\n{кандидати}")

    prompt = f"""Сантехнік. Для кожного запиту — номер кандидата.
{chr(10).join(запити)}
Правила: діаметр ОБОВ'ЯЗКОВО збігається; ВИРОБНИК якщо вказано — тільки він; (п/з) тільки як останній варіант.
JSON рівно {len(позиції)} елементів:
[{{"знайдено":true,"номер_кандидата":1,"confidence":95,"reason":"причина","fail_reason":""}}]"""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        parsed = _parse_claude_json(resp.content[0].text)
        if not parsed: raise ValueError("порожній JSON")
        while len(parsed) < len(позиції):
            parsed.append({"знайдено": False, "confidence": 0, "reason": "", "fail_reason": "немає відповіді"})
        return parsed[:len(позиції)]
    except Exception as e:
        if _retry and len(позиції) > 1:
            print(f"⚠️ Батч впав ({e}), ретрай поштучно...")
            results = []
            for п in позиції:
                results.extend(claude_pick_batch([п], _retry=False))
            return results
        return [{"знайдено": False, "confidence": 0, "reason": "", "fail_reason": f"Claude: {e}"}] * len(позиції)

# ═══════════════════════════════════════════════════════════════════════════════
# FIND ITEMS
# ═══════════════════════════════════════════════════════════════════════════════
def find_items(позиції: list[dict], progress_cb=None) -> list[dict]:
    результати = [None] * len(позиції)
    потребують_claude = []

    for i, пос in enumerate(позиції):
        if progress_cb: progress_cb(i+1, len(позиції))
        original   = пос.get('original', '')
        normalized = пос.get('normalized', '')
        category   = пос.get('category', 'other')
        brand_map  = пос.get('_brand_map', {})
        manager_brand = brand_map.get(category)

        кандидати = []
        required_brand = None
        джерело = ''

        if manager_brand:
            кандидати = keyword_search(normalized, top_n=12, brand_tokens=manager_brand)
            if кандидати:
                required_brand = manager_brand[0]
                джерело = '👨 менеджер'
            else:
                кандидати = keyword_search(normalized, top_n=12)
                джерело = '⚠️ fallback'
        else:
            for priority in DEFAULT_BRAND_PRIORITY.get(category, []):
                кандидати = keyword_search(normalized, top_n=12, brand_tokens=priority)
                if кандидати:
                    required_brand = priority[0]
                    джерело = '⚙️ дефолт'
                    break
            if not кандидати:
                кандидати = keyword_search(normalized, top_n=12)
                джерело = '🔍 вільний'

        if not кандидати:
            результати[i] = {**пос, 'знайдено': False, 'назва': '', 'артикул': '',
                              'ціна': '', 'confidence': 0, 'джерело': '',
                              'reason': '', 'fail_reason': 'не знайдено кандидатів',
                              'candidates_debug': []}
            continue

        потребують_claude.append({
            'idx': i, 'normalized': normalized, 'original': original,
            'candidates': кандидати, 'candidates_debug': [c['name'] for c in кандидати[:5]],
            'qty': пос.get('qty', ''), 'required_brand': required_brand,
            'category': category, 'brand_map': brand_map, 'джерело': джерело,
        })

    if потребують_claude:
        відповіді = claude_pick_batch(потребують_claude)
        for j, пос in enumerate(потребують_claude):
            r = відповіді[j] if j < len(відповіді) else {'знайдено': False}
            idx = пос['idx']
            conf = int(r.get('confidence', 0))
            if r.get('знайдено') and r.get('номер_кандидата'):
                n = max(0, min(int(r['номер_кандидата'])-1, len(пос['candidates'])-1))
                found = пос['candidates'][n]
                результати[idx] = {
                    'original': пос['original'], 'normalized': пос['normalized'],
                    'знайдено': True, 'назва': found['name'],
                    'назва_повна': found.get('name_full', found['name']),
                    'артикул': found.get('artikul', ''),
                    'ціна': found.get('price', ''), 'qty': пос['qty'],
                    'category': пос['category'], 'confidence': conf,
                    'keyword_pct': found.get('_match_pct', 0),
                    'джерело': пос['джерело'], 'reason': r.get('reason', ''),
                    'fail_reason': '', 'candidates_debug': пос['candidates_debug'],
                }
            else:
                результати[idx] = {
                    'original': пос['original'], 'normalized': пос['normalized'],
                    'знайдено': False, 'назва': '', 'артикул': '', 'ціна': '',
                    'qty': пос['qty'], 'confidence': conf, 'джерело': '',
                    'reason': '', 'fail_reason': r.get('fail_reason', 'не знайдено'),
                    'candidates_debug': пос['candidates_debug'],
                }
    return результати

# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═══════════════════════════════════════════════════════════════════════════════
def parse_qty(s):
    s = str(s or '').strip()
    if not s: return '', ''
    m = re.match(r'(\d+(?:[.,]\d+)?)\s*(.*)', s)
    if m:
        try:
            num = float(m.group(1).replace(',', '.'))
            num = int(num) if num == int(num) else num
        except Exception:
            num = m.group(1)
        return num, m.group(2).strip().rstrip('.').strip()
    return s, ''

def create_excel(результати: list[dict]):
    from openpyxl.styles import PatternFill
    RED    = PatternFill('solid', fgColor='FFC7CE')
    YELLOW = PatternFill('solid', fgColor='FFF3B0')

    rows, flags = [], []
    not_found, warn = [], []

    for r in результати:
        if not r: continue
        conf, kw = r.get('confidence', 0), r.get('keyword_pct', 0)
        qty_num, qty_unit = parse_qty(r.get('qty', ''))

        if r.get('знайдено'):
            suspicious = conf < 70 or kw < 50 or r.get('джерело','') in ('⚠️ fallback','🔍 вільний')
            rows.append({
                'Артикул':      r.get('артикул', ''),
                'Наименование': r.get('назва_повна') or r.get('назва', ''),
                'Кількість':    qty_num, 'Од.': qty_unit,
                'Ціна':         r.get('ціна', ''),
                'Збіг':         f"🔍{kw}%/🤖{conf}%",
                'Джерело':      r.get('джерело', ''),
                'Оригінал':     r.get('original', ''),
            })
            flags.append('warn' if suspicious else '')
            if suspicious: warn.append(r.get('original',''))
        else:
            cands = r.get('candidates_debug', [])
            best = cands[0] if cands else ''
            art, full, price = '', best, ''
            for it in CATALOG:
                if it['name'] == best:
                    art, full, price = it.get('artikul',''), it.get('name_full', best), it.get('price','')
                    break
            rows.append({
                'Артикул': art, 'Наименование': full,
                'Кількість': qty_num, 'Од.': qty_unit,
                'Ціна': price, 'Збіг': '—',
                'Джерело': '❓ НЕ ЗНАЙДЕНО',
                'Оригінал': r.get('original', ''),
            })
            flags.append('nf')
            not_found.append(r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=['Артикул','Наименование','Кількість','Од.','Ціна','Збіг','Джерело','Оригінал'])
        df.to_excel(writer, index=False, sheet_name='Замовлення')
        ws = writer.sheets['Замовлення']
        for i, fl in enumerate(flags, start=2):
            fill = RED if fl == 'nf' else (YELLOW if fl == 'warn' else None)
            if fill:
                for cell in ws[i]: cell.fill = fill
    output.seek(0)
    return output, not_found, warn

# ═══════════════════════════════════════════════════════════════════════════════
# СТАНИ (визначаємо ДО хендлерів і process_batch!)
# ═══════════════════════════════════════════════════════════════════════════════
user_batches  = {}
stop_flags    = {}
pending_hints = {}
last_results  = {}
_fix_state    = {}
_train_state  = {}
BATCH_TIMEOUT = 4

def safe_edit(chat_id, msg_id, text):
    try: bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except Exception: pass

# ═══════════════════════════════════════════════════════════════════════════════
# PROCESS BATCH
# ═══════════════════════════════════════════════════════════════════════════════
def process_batch(chat_id: int):
    batch = user_batches.pop(chat_id, None)
    if not batch: return
    stop_flags.pop(chat_id, None)
    items = batch['items']

    status = bot.send_message(chat_id, f"⏳ Обробляю {len(items)} файл(ів)...")
    msg_id = status.message_id

    всі_позиції, errors = [], []

    for idx, item in enumerate(items, 1):
        if stop_flags.get(chat_id):
            safe_edit(chat_id, msg_id, "🛑 Зупинено.")
            return
        try:
            safe_edit(chat_id, msg_id, f"📖 Читаю файл {idx}/{len(items)}...")
            if item['type'] == 'photo':
                позиції = normalize_photo(item['data'], item.get('caption',''))
            else:
                позиції = []
            всі_позиції.extend(позиції)
        except Exception as e:
            errors.append(f"❌ Файл {idx}: {e}")

    if not всі_позиції:
        safe_edit(chat_id, msg_id, "😕 Не розпізнано позицій.\n" + "\n".join(errors))
        return

    # Прикріплюємо brand_map
    caption = items[0].get('caption','') if items else ''
    brand_map = parse_caption_brands(caption)
    for п in всі_позиції:
        п['_brand_map'] = brand_map

    safe_edit(chat_id, msg_id, f"🔍 Шукаю {len(всі_позиції)} позицій...")

    def progress(cur, total):
        if cur % 5 == 0 or cur == total:
            safe_edit(chat_id, msg_id, f"🔍 Пошук: {cur}/{total}...")

    результати = find_items(всі_позиції, progress_cb=progress)

    safe_edit(chat_id, msg_id, "📊 Формую Excel...")
    excel, not_found, warn = create_excel(результати)

    знайдено = [r for r in результати if r and r.get('знайдено')]
    total = len([r for r in результати if r])

    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    звіт = f"✅ Знайдено: {len(знайдено)}/{total}\n"
    if not_found: звіт += f"🟥 Не знайдено: {len(not_found)}\n"
    if warn: звіт += f"🟨 Перевір: {len(warn)}\n"
    if errors: звіт += "\n" + "\n".join(errors)
    safe_edit(chat_id, msg_id, звіт)

    # Кнопки навчання адміну
    if chat_id == ADMIN_ID:
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("🎓 Навчання", callback_data="tr_go"),
               InlineKeyboardButton("✖️ Закрити",  callback_data="tr_close"))
        bot.send_message(chat_id, "Перевір файл. Якщо помилки — тапни Навчання:", reply_markup=mk)

    last_results[chat_id] = {'результати': [r for r in результати if r], 'client_slug': None}
    username = items[0].get('username', str(chat_id)) if items else str(chat_id)
    log_usage(chat_id, username, total, len(знайдено), len(items))

def add_to_batch(chat_id: int, item: dict):
    if chat_id not in user_batches:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав! Чекаю ще файли (4 сек)...")
    if 'timer' in user_batches[chat_id]:
        user_batches[chat_id]['timer'].cancel()
    user_batches[chat_id]['items'].append(item)
    t = threading.Timer(BATCH_TIMEOUT, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = t
    t.daemon = True
    t.start()

# ═══════════════════════════════════════════════════════════════════════════════
# КЛАВІАТУРА
# ═══════════════════════════════════════════════════════════════════════════════
def main_keyboard(uid: int) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("📸 Як користуватись"), KeyboardButton("🛑 Стоп"))
    kb.add(KeyboardButton("📋 Правило"), KeyboardButton("📊 Кеш"))
    kb.add(KeyboardButton("👥 Клієнти"), KeyboardButton("👥 Кеш клієнта"))
    if is_admin(uid):
        kb.add(KeyboardButton("👑 Статистика"), KeyboardButton("👑 Правила на розгляд"))
        kb.add(KeyboardButton("👑 Логи"))
    return kb

# ═══════════════════════════════════════════════════════════════════════════════
# ХЕНДЛЕРИ
# ═══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    admin_note = ""
    if is_admin(message.from_user.id):
        admin_note = "\n\n👑 Адмін:\n`вірно 3` / `помилка 5` — навчання\n`виправ 5` — кнопки варіантів"
    bot.reply_to(message,
        f"👋 Привіт! Бот підбору сантехніки.\n\n"
        f"📸 Кинь фото списку — знайду в базі\n"
        f"📝 *пошук <текст>* — текстовий запит\n"
        f"📋 *правило <текст>* — навчи новому сленгу\n"
        f"🛑 /stop — зупинити{admin_note}",
        parse_mode="Markdown",
        reply_markup=main_keyboard(message.from_user.id))


@bot.message_handler(func=lambda m: m.text == "📸 Як користуватись")
def kb_howto(message):
    bot.reply_to(message,
        "📸 *Як користуватись:*\n\n"
        "1. Напиши підказку:\n`каналізація остендорф\nпайка екопластик\nкрани рафтек`\n\n"
        "2. Кинь фото списку від майстра\n\n"
        "3. Отримай Excel з підібраними товарами",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ("🛑 Стоп", "🛑 стоп"))
def kb_stop(message):
    handle_stop(message)

@bot.message_handler(func=lambda m: m.text in ("📋 Правило",))
def kb_rule_btn(message):
    bot.reply_to(message, "Напиши: `правило <текст>`\nПриклад: `правило рожон = трійник`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 Кеш")
def kb_cache(message):
    handle_cache_info(message)

@bot.message_handler(func=lambda m: m.text == "👥 Клієнти")
def kb_clients(message):
    bot.reply_to(message, "Функція профілів клієнтів в розробці.")

@bot.message_handler(func=lambda m: m.text == "👥 Кеш клієнта")
def kb_client_cache(message):
    bot.reply_to(message, "Кеш клієнта: функція в розробці.")

@bot.message_handler(func=lambda m: m.text == "👑 Статистика")
def kb_stats(message):
    if not is_admin(message.from_user.id): return
    bot.reply_to(message, get_usage_stats(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👑 Логи")
def kb_logs(message):
    if not is_admin(message.from_user.id): return
    if not os.path.exists(USAGE_LOG_FILE):
        bot.reply_to(message, "Лог порожній.")
        return
    with open(USAGE_LOG_FILE, "rb") as f:
        bot.send_document(message.chat.id, f, visible_file_name="usage_log.json")

@bot.message_handler(func=lambda m: m.text == "👑 Правила на розгляд")
def kb_pending(message):
    if not is_admin(message.from_user.id): return
    show_pending_rules(message.chat.id)

def show_pending_rules(chat_id):
    rules = load_pending_rules()
    if not rules:
        bot.send_message(chat_id, "✅ Немає правил на розгляд.")
        return
    for i, r in enumerate(rules):
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_{i}"),
               InlineKeyboardButton("❌ Відхилити",   callback_data=f"reject_{i}"))
        bot.send_message(chat_id,
            f"📋 #{i+1} від {r['username']} ({r['date']}):\n`{r['rule']}`",
            parse_mode="Markdown", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith(('approve_','reject_')))
def handle_rule_decision(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    action, idx = call.data.split('_')
    idx = int(idx)
    rules = load_pending_rules()
    if idx >= len(rules):
        bot.answer_callback_query(call.id, "Вже оброблено"); return
    rule = rules.pop(idx)
    save_pending_rules(rules)
    if action == 'approve':
        add_rule(rule['rule'])
        bot.edit_message_text(f"✅ ПІДТВЕРДЖЕНО:\n`{rule['rule']}`",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try: bot.send_message(rule['user_id'], f"✅ Твоє правило підтверджено:\n`{rule['rule']}`", parse_mode="Markdown")
        except Exception: pass
    else:
        bot.edit_message_text(f"❌ ВІДХИЛЕНО:\n`{rule['rule']}`",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.answer_callback_query(call.id, "Готово")

@bot.callback_query_handler(func=lambda c: c.data in ("tr_go","tr_close"))
def tr_start(call):
    if call.data == "tr_close":
        try: bot.edit_message_text("✖️ Закрито.", call.message.chat.id, call.message.message_id)
        except Exception: pass
        bot.answer_callback_query(call.id); return
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    _train_state[call.message.chat.id] = {'stage': 'rows'}
    try:
        bot.edit_message_text(
            "✍️ Напиши номери НЕПРАВИЛЬНИХ рядків через пробіл (напр: 3 7 12):",
            call.message.chat.id, call.message.message_id)
    except Exception: pass
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.text and m.chat.id in _train_state
                     and _train_state[m.chat.id].get('stage') == 'rows'
                     and re.fullmatch(r'[\d\s,]+', m.text.strip()))
def tr_rows(message):
    last = last_results.get(message.chat.id)
    total = len(last['результати']) if last else 0
    nums = sorted({int(x) for x in re.findall(r'\d+', message.text)
                   if 1 <= int(x) <= total})
    if not nums:
        bot.reply_to(message, f"⚠️ Валідних номерів немає (всього {total} рядків).")
        return
    _train_state[message.chat.id] = {'stage': 'classify', 'rows': nums, 'i': 0}
    _tr_show_row(message.chat.id)

def _tr_show_row(chat_id):
    st = _train_state.get(chat_id)
    last = last_results.get(chat_id)
    if not st or not last: return
    if st['i'] >= len(st['rows']):
        bot.send_message(chat_id, f"✅ Навчання завершено! Оброблено {len(st['rows'])} рядків.")
        _train_state.pop(chat_id, None); return
    row = st['rows'][st['i']]
    r = last['результати'][row-1]
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("🔴 Взагалі не той товар", callback_data="trw"),
           InlineKeyboardButton("🏷 Не той виробник",      callback_data="trb"),
           InlineKeyboardButton("🟡 Трохи промахнувся",     callback_data="trc"),
           InlineKeyboardButton("⏭ Пропустити",            callback_data="trs"))
    m = bot.send_message(chat_id,
        f"🎓 Рядок {row} ({st['i']+1}/{len(st['rows'])})\n"
        f"Написано: {r.get('original','')[:50]}\n"
        f"Бот дав: {(r.get('назва','') or '❓ не знайдено')[:60]}\n\nЩо не так?",
        reply_markup=mk)
    st['msg_id'] = m.message_id

@bot.callback_query_handler(func=lambda c: c.data in ("trw","trb","trc","trs","trn") or c.data.startswith("trp_"))
def tr_classify(call):
    chat_id = call.message.chat.id
    st = _train_state.get(chat_id)
    last = last_results.get(chat_id)
    if not st or not last:
        bot.answer_callback_query(call.id, "Сесія застаріла"); return
    row = st['rows'][st['i']]
    r = last['результати'][row-1]
    old_name = r.get('назва', '')

    def advance():
        st['i'] += 1
        _tr_show_row(chat_id)

    if call.data == "trs":
        bot.answer_callback_query(call.id, "Пропущено"); advance(); return

    if call.data == "trn":
        bot.answer_callback_query(call.id, "Ок"); advance(); return

    if call.data.startswith("trp_"):
        idx = int(call.data[4:])
        new_name = st.get('cands', [])[idx]
        # Просто додаємо правило в rules.txt
        add_rule(f"{r.get('original','')} = {new_name}")
        r['назва'] = new_name
        try:
            bot.edit_message_text(
                f"✅ Навчено!\n`{r.get('original','')[:40]}` → {new_name[:60]}",
                chat_id, st.get('msg_id', call.message.message_id), parse_mode="Markdown")
        except Exception: pass
        bot.answer_callback_query(call.id, "✅ Збережено")
        advance(); return

    # Причина вибрана → показати кандидатів
    query = r.get('normalized') or r.get('original', '')
    cands = [c['name'] for c in keyword_search(query, top_n=8) if c['name'] != old_name][:7]
    st['cands'] = cands
    mk = InlineKeyboardMarkup(row_width=1)
    for i, name in enumerate(cands):
        mk.add(InlineKeyboardButton(f"{i+1}. {name[:55]}", callback_data=f"trp_{i}"))
    mk.add(InlineKeyboardButton("❌ Немає правильного", callback_data="trn"))
    try:
        bot.edit_message_text(
            f"🎓 Рядок {row}: {r.get('original','')[:45]}\n"
            f"❌ Старе: {old_name[:55] or '—'}\n\nТапни ПРАВИЛЬНИЙ:",
            chat_id, st.get('msg_id', call.message.message_id), reply_markup=mk)
    except Exception:
        m = bot.send_message(chat_id, f"Тапни правильний варіант:", reply_markup=mk)
        st['msg_id'] = m.message_id
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda m: m.text and re.match(r'^вірно\s+\d+', m.text.lower()))
def handle_virno(message):
    if not is_admin(message.from_user.id): return
    row = int(re.search(r'\d+', message.text).group())
    last = last_results.get(message.chat.id)
    if not last:
        bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
    if row < 1 or row > len(last['результати']):
        bot.reply_to(message, f"⚠️ Рядок {row} не існує."); return
    r = last['результати'][row-1]
    add_rule(f"ПІДТВЕРДЖЕНО: {r.get('original','')} = {r.get('назва','')}")
    bot.reply_to(message, f"✅ Рядок {row} підтверджено:\n`{r.get('назва','')[:60]}`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and re.match(r'^помилка\s+\d+', m.text.lower()))
def handle_pomylka(message):
    if not is_admin(message.from_user.id): return
    row = int(re.search(r'\d+', message.text).group())
    last = last_results.get(message.chat.id)
    if not last:
        bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
    if row < 1 or row > len(last['результати']):
        bot.reply_to(message, f"⚠️ Рядок {row} не існує."); return
    r = last['результати'][row-1]
    add_rule(f"ПОМИЛКА: {r.get('original','')} НЕ є {r.get('назва','')}")
    bot.reply_to(message, f"❌ Рядок {row} позначено як помилку:\n`{r.get('назва','')[:60]}`", parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    rule = message.text[7:].strip()
    if not rule:
        bot.reply_to(message, "Напиши правило після слова 'правило'."); return
    if is_admin(message.from_user.id):
        add_rule(rule)
        bot.reply_to(message, f"✅ Записав:\n_{rule}_", parse_mode="Markdown")
    else:
        rules = load_pending_rules()
        rules.append({"rule": rule, "user_id": message.from_user.id,
                      "username": message.from_user.username or str(message.from_user.id),
                      "date": time.strftime("%Y-%m-%d %H:%M")})
        save_pending_rules(rules)
        bot.reply_to(message, f"📥 Правило відправлено на розгляд ({len(rules)} в черзі):\n_{rule}_",
                     parse_mode="Markdown")
        try:
            bot.send_message(ADMIN_ID, f"🔔 Нове правило від @{message.from_user.username}:\n`{rule}`",
                             parse_mode="Markdown")
        except Exception: pass


@bot.message_handler(commands=['кеш', 'cache'])
def handle_cache_info(message):
    bot.reply_to(message, "📋 Кеш нормалізацій: використовується rules.txt для навчання.\n"
                          "Команди: `вірно N`, `помилка N`, `виправ N`", parse_mode="Markdown")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    fuid = message.photo[-1].file_unique_id
    _b = user_batches.get(message.chat.id)
    if _b and any(it.get('fuid') == fuid for it in _b.get('items', [])):
        return
    for attempt in range(3):
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            image_b64 = base64.b64encode(downloaded).decode('utf-8')
            caption = message.caption or ""
            hint = pending_hints.pop(message.chat.id, "")
            full_caption = " | ".join(filter(None, [caption, hint]))
            add_to_batch(message.chat.id, {
                'type': 'photo', 'data': image_b64, 'caption': full_caption,
                'fuid': fuid,
                'username': message.from_user.username or str(message.from_user.id),
            })
            return
        except Exception as e:
            if attempt == 2:
                bot.reply_to(message, f"❌ Не вдалося завантажити фото: {e}")
            else:
                time.sleep(2)


@bot.message_handler(content_types=['document'])
def handle_document(message):
    doc = message.document
    mime = (doc.mime_type or '').lower()
    fname = (doc.file_name or '').lower()
    if not (mime.startswith('image/') or fname.endswith(('.jpg','.jpeg','.png','.webp'))):
        bot.reply_to(message, "📎 Це не зображення. Надішли фото (jpg/png).")
        return
    fuid = doc.file_unique_id
    _b = user_batches.get(message.chat.id)
    if _b and any(it.get('fuid') == fuid for it in _b.get('items', [])):
        return
    for attempt in range(3):
        try:
            file_info = bot.get_file(doc.file_id)
            downloaded = bot.download_file(file_info.file_path)
            image_b64 = base64.b64encode(downloaded).decode('utf-8')
            caption = message.caption or ""
            hint = pending_hints.pop(message.chat.id, "")
            full_caption = " | ".join(filter(None, [caption, hint]))
            add_to_batch(message.chat.id, {
                'type': 'photo', 'data': image_b64, 'caption': full_caption, 'fuid': fuid,
                'username': message.from_user.username or str(message.from_user.id),
            })
            return
        except Exception as e:
            if attempt == 2:
                bot.reply_to(message, f"❌ Не вдалося завантажити: {e}")
            else:
                time.sleep(2)


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text_search(message):
    запит = message.text[5:].strip()
    if запит:
        add_to_batch(message.chat.id, {
            'type': 'photo', 'data': None, 'caption': запит,
            'username': message.from_user.username or str(message.from_user.id),
        })
    else:
        bot.reply_to(message, "Напиши запит після слова 'пошук'.")


@bot.message_handler(commands=['stop'])
def handle_stop(message):
    chat_id = message.chat.id
    stop_flags[chat_id] = True
    if chat_id in user_batches:
        if 'timer' in user_batches[chat_id]:
            user_batches[chat_id]['timer'].cancel()
        user_batches.pop(chat_id, None)
    bot.reply_to(message, "🛑 Зупинено.")


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/')
                     and not m.text.lower().startswith('пошук')
                     and not m.text.lower().startswith('правило')
                     and not re.match(r'^(вірно|помилка|виправ)\s+\d+', m.text.lower().strip())
                     and not m.text.startswith(('📸','🛑','📋','📊','👥','👑')))
def handle_text_hint(message):
    text = message.text.strip()
    if text:
        pending_hints[message.chat.id] = text
        bot.reply_to(message, f"💬 Підказка: _{text}_\nТепер кидай фото!", parse_mode="Markdown")
        def clear(cid): pending_hints.pop(cid, None)
        t = threading.Timer(120.0, clear, args=[message.chat.id])
        t.daemon = True
        t.start()


print("🤖 bot.py завантажено, хендлери зареєстровані", flush=True)
