"""
search.py — Пошук товарів у каталозі.

4 рівні пошуку (від швидкого до складного):
  1. Кеш клієнта
  2. Кеш бота
  3. Атрибутний пошук (детермінований: тип + діаметри + кут)
  4. Claude вибір з кандидатів (AI-арбітр)

Плюс: keyword_search як fallback, ⚡ авто-прийом очевидних збігів.
"""

import os
import re
import json
import anthropic

from clients.cache import (cache_lookup, cache_save, cache_confirm, cache_delete,
                            cache_set_status, cache_ban_pair, is_banned as cache_is_banned)
from clients.pending_cache import pending_add  # нові збіги → на підтвердження адміну
from clients import clients
from catalog.catalog import CATALOG, tokenize, ensure_tokens
from engine.router import (route_batch, get_prefix, CAT_PREFIX,
                            route_sub)  # жорстка маршрутизація + підкатегорії
from engine.voyage_search import (voyage_find_one, voyage_search,
                                   rebuild_if_needed, is_ready as voyage_ready,
                                   THRESHOLD_AUTO, THRESHOLD_MIN)  # векторний пошук Voyage AI

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
claude        = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ─── Таблиці виробників та категорій ─────────────────────────────────────────

BRAND_TOKENS = {    # словник: що пише менеджер → офіційні токени виробника для пошуку в назвах
    'raftec':      ['raftec', 'RAFTEC'],
    'рафтек':      ['raftec', 'RAFTEC'],
    'ekoplastik':  ['ekoplastik', 'Ekoplastik'],
    'екопластик':  ['ekoplastik', 'Ekoplastik'],
    'wavin':       ['wavin', 'Wavin'],  # PPR виробник
    'екопласт':    ['ekoplastik', 'Ekoplastik'],
    'asg':         ['asg', 'ASG'],
    'асг':         ['asg', 'ASG'],
    'ostendorf':   ['ostendorf', 'OSTENDORF'],
    'остендорф':   ['ostendorf', 'OSTENDORF'],
    'plm':         ['plm', 'PLM'],
    'плм':         ['plm', 'PLM'],
    'hidros':      ['hidros', 'Hidros', 'HIDROS'],
    'хідрос':      ['hidros', 'Hidros'],
    'гідрос':      ['hidros', 'Hidros'],
    'idmar':       ['idmar', 'IDMAR'],
    'termojet':    ['termojet', 'Termojet'],
    'термоджет':   ['termojet', 'Termojet'],
    'tatra':       ['tatra', 'TATRA', 'Tatra-Line'],
    'татра':       ['tatra', 'TATRA'],
    'rehau':       ['rehau', 'REHAU'],
    'рехау':       ['rehau', 'REHAU'],
    'ecosoft':     ['ecosoft', 'Ecosoft'],
    'екософт':     ['ecosoft', 'Ecosoft'],
    'biasi':       ['biasi', 'BIASI'],
    'біасі':       ['biasi', 'BIASI'],
    'valrom':      ['valrom', 'Valrom'],
    'unipak':      ['unipak', 'Unipak'],
    'kan':         ['kan', 'KAN'],
    'herz':        ['herz', 'HERZ', 'Herz'],
    'герц':        ['herz', 'HERZ'],
    'giacomini':   ['giacomini', 'Giacomini'],
    'джикоміні':   ['giacomini', 'Giacomini'],
    'danfoss':     ['danfoss', 'Danfoss'],
    'данфос':      ['danfoss', 'Danfoss'],
    'wilo':        ['wilo', 'WILO'],
    'віло':        ['wilo', 'WILO'],
    'grundfos':    ['grundfos', 'GRUNDFOS'],
    'грундфос':    ['grundfos', 'GRUNDFOS'],
    'bonomi':      ['bonomi', 'Bonomi'],
    'бономі':      ['bonomi', 'Bonomi'],
    'pattaroni':   ['pattaroni', 'Pattaroni'],
    'k-flex':      ['k-flex', 'K-FLEX'],
    'kflex':       ['k-flex', 'K-FLEX'],
    'кфлекс':      ['k-flex', 'K-FLEX'],
    'valsir':      ['valsir', 'Valsir'],
    'meibes':      ['meibes', 'Meibes'],
    'flamco':      ['flamco', 'Flamco'],
    'reflex':      ['reflex', 'Reflex'],
    'icma':        ['icma', 'Icma'],
    'drazice':     ['drazice', 'Drazice'],
    'дражице':     ['drazice', 'Drazice'],
    'vaillant':    ['vaillant', 'Vaillant'],
    'вайлант':     ['vaillant', 'Vaillant'],
    'alcaplast':   ['alcaplast', 'AlcaPlast'],
    'esbe':        ['esbe', 'ESBE'],
    'grohe':       ['grohe', 'Grohe'],
    'thermaflex':  ['thermaflex', 'Thermaflex'],
}

CATEGORY_ALIASES = {    # словник: що пише менеджер у підказці → внутрішня назва категорії
    'каналізація': 'sewage', 'канал': 'sewage', 'каналізац': 'sewage',
    'пайка': 'plastic_ppr',
    'wavin': 'plastic_ppr',  # Wavin = PPR виробник 'ппр': 'plastic_ppr', 'ppr': 'plastic_ppr',
    'пластик': 'plastic_ppr', 'поліпропілен': 'plastic_ppr',
    'кран': 'shutoff_valves', 'крани': 'shutoff_valves',
    'арматура': 'shutoff_valves', 'вентил': 'shutoff_valves',
    'пуш': 'push_systems', 'push': 'push_systems', 'пекс': 'push_systems',
    'pex': 'push_systems', 'натяжн': 'push_systems', 'гільз': 'push_systems',
    'насос': 'pumps', 'насоси': 'pumps',
    'радіатор': 'radiators_radiatorsvalve', 'радіатори': 'radiators_radiatorsvalve',
    'утепл': 'insulation', 'ізоляц': 'insulation', 'мірелон': 'insulation',
    'фільтр колба': 'filtration', 'колба': 'filtration', 'картридж': 'filtration',
    'фільтр груб': 'shutoff_valves', 'груб очист': 'shutoff_valves',
    'фільтр': 'filtration', 'очист': 'filtration',
    'металопласт': 'metal_plastic', 'мп': 'metal_plastic',
    'котел': 'boilers', 'котли': 'boilers',
    'бойлер': 'water_heaters', 'водонагрів': 'water_heaters',
    'тепла підлога': 'underfloor_heating', 'тп': 'underfloor_heating',
    'сифон': 'siphons_fittings',
    'змішувач': 'mixers_faucets',
    'кріплення': 'fasteners_sealants', 'хомут': 'fasteners_sealants',
    'перехідник': 'adapters_reducers',
    'опалення': 'heating',
    'шланг': 'hoses',
    'рушникосуш': 'towel_warmers',
}

DEFAULT_BRAND_PRIORITY = {  # якщо менеджер не вказав виробника — беремо з цього списку за пріоритетом
    'sewage':                  [['asg', 'ASG'], ['ostendorf', 'OSTENDORF']],
    'plastic_ppr':             [['ekoplastik', 'Ekoplastik'], ['asg', 'ASG'], ['raftec', 'RAFTEC']],
    'shutoff_valves':          [['raftec', 'RAFTEC']],
    'adapters_reducers':       [['raftec', 'RAFTEC']],
    'filtration':              [['ecosoft', 'Ecosoft']],
    'radiators_radiatorsvalve':[['hidros', 'Hidros'], ['idmar', 'IDMAR']],
    'pumps':                   [['tatra', 'TATRA'], ['termojet', 'Termojet']],
    'insulation':              [['plm', 'PLM']],
    'push_systems':            [['raftec', 'RAFTEC'], ['rehau', 'REHAU']],
    'metal_plastic':           [['raftec', 'RAFTEC']],
    'fasteners_sealants':      [['eco', 'ECO']],
}

SIMILAR_CATS = {    # суміжні категорії: якщо не знайшли в основній — шукаємо тут (Gemini міг помилитись категорією)
    'plastic_ppr':       ['adapters_reducers', 'heating'],
    'adapters_reducers': ['plastic_ppr', 'shutoff_valves', 'heating'],
    'heating':           ['plastic_ppr', 'shutoff_valves', 'adapters_reducers'],
    'push_systems':      ['metal_plastic', 'plastic_ppr'],
    'metal_plastic':     ['push_systems', 'adapters_reducers'],
    'sewage':            ['siphons_fittings'],
    'siphons_fittings':  ['sewage'],
    'shutoff_valves':    ['adapters_reducers', 'safety_valves', 'filtration'],
    'filtration':        ['shutoff_valves'],  # фільтр грубої очистки часто в shutoff_valves
    'underfloor_heating':['metal_plastic', 'push_systems'],
    'insulation':        ['fasteners_sealants'],
    'fasteners_sealants':['insulation'],
    'boilers':           ['heating', 'shutoff_valves'],
}

# ─── Типи та атрибути ────────────────────────────────────────────────────────

TYPE_SYNONYMS = {   # нормалізує синоніми типів до єдиного канонічного імені: "кутник" → "коліно"
    'коліно': 'коліно', 'колено': 'коліно', 'кутник': 'коліно', 'кут': 'коліно',
    'угол': 'коліно', 'уголок': 'коліно', 'відвід': 'коліно', 'отвод': 'коліно',
    'відведення': 'відведення',     # спец-фітинг з кількома кутами — окремий тип!
    'трійник': 'трійник', 'тройник': 'трійник', 'трійники': 'трійник',
    'труба': 'труба', 'труби': 'труба',
    'муфта': 'муфта', "з'єднувач": 'муфта', 'зєднувач': 'муфта', 'соединитель': 'муфта',
    "з'єднання": 'американка', 'американка': 'американка', 'американки': 'американка',
    'кран': 'кран', 'крани': 'кран', 'вентиль': 'кран', 'вентель': 'кран',
    'перехід': 'перехід', 'переход': 'перехід', 'перехідник': 'перехід',
    'редукція': 'перехід', 'редукци': 'перехід', 'футорка': 'футорка',
    'заглушка': 'заглушка', 'хрестовина': 'хрестовина', 'крестовина': 'хрестовина',
    'ревізія': 'ревізія', 'ревизия': 'ревізія',
    'ніпель': 'ніпель', 'нипель': 'ніпель', 'штуцер': 'штуцер',
    'подовжувач': 'подовжувач', 'бочонок': 'подовжувач', 'бочка': 'подовжувач',
    'згін': 'згін', 'згон': 'згін', 'напівзгін': 'напівзгін',
    'гільза': 'гільза', 'кільце': 'кільце',    # НЕ синоніми! гільза ≠ кільце
    'фланець': 'фланець',
    'клапан': 'клапан', 'засувка': 'засувка', 'затвор': 'затвор',
    'редуктор': 'редуктор', 'регулятор': 'редуктор',
    'редукція': 'редукція', 'муфта редукційна': 'редукція',  # канал редукція = муфта редукційна
    'трап': 'трап', 'трапик': 'трап',
    'плівка': 'плівка', 'фольга': 'плівка',
    'гак': 'гак монтажний',
    'манжет': 'манжета', 'манжета': 'манжета',  # манжета каналізаційна
    'корок': 'заглушка', 'коробок': 'заглушка',  # заглушка PPR
    'подовжувач': 'подовжувач', 'бочонок': 'подовжувач',  # подовжений ніпель
    'самоочисний': 'фільтр самоочисний', 'самоочищаючий': 'фільтр самоочисний',
    'біметалічний': 'радіатор біметалічний', 'алюмінієвий': 'радіатор алюмінієвий',
    'обвід': 'обвід', 'байпас ppр': 'обвід', 'якір монтажний': 'гак монтажний',
    'гак монтажний': 'гак монтажний', 'гак подвійний': 'гак монтажний подвійний',
    'муфта зєднання': 'муфта перехідна', 'муфта зединання': 'муфта перехідна',                     # відбивна плівка теплої підлоги
    'демпферна': 'демпферна стрічка', 'демперна': 'демпферна стрічка',
    'демпфер': 'демпферна стрічка', 'дємферна': 'демпферна стрічка',
    'ніпель': 'ніпель', 'футорка': 'футорка', 'штуцер': 'штуцер',
    'подовжувач': 'подовжувач', 'бочонок': 'подовжувач',
    'зворотний': 'зворотний клапан', 'зворотній': 'зворотний клапан',
    'вентиль': 'кран',  # вентиль будь-якого бренду → шукати як кран
    'гідроакумулятор': 'гідроакумулятор', 'гідроак': 'гідроакумулятор',
    'мембрана': 'мембрана', 'баку': 'гідроакумулятор',
    'фільтр': 'фільтр', 'фильтр': 'фільтр',
    'насос': 'насос', 'радіатор': 'радіатор', 'радиатор': 'радіатор',
    'колектор': 'колектор', 'гребінка': 'колектор', 'гребенка': 'колектор',
    'термоголовка': 'термоголовка', 'сифон': 'сифон', 'трап': 'трап',
    'хомут': 'хомут', 'опора': 'опора', 'скоба': 'скоба',
    'утеплювач': 'утеплювач', 'мірелон': 'утеплювач', 'мирелон': 'утеплювач',
    'шланг': 'шланг', 'підводка': 'підводка', 'підведення': 'підводка',
    'котел': 'котел', 'бойлер': 'водонагрівач', 'водонагрівач': 'водонагрівач',
    'змішувач': 'змішувач', 'мийка': 'мийка', 'умивальник': 'умивальник',
    'лічильник': 'лічильник', 'счетчик': 'лічильник',
    'стрічка': 'стрічка', 'шпилька': 'шпилька', 'дюбель': 'дюбель',
    'вузол': 'вузол', 'комплект': 'комплект', 'набір': 'комплект',
    'шафа': 'шафа', 'бак': 'бак', 'ємність': 'бак', 'емкость': 'бак',
    'группа': 'група', 'група': 'група',
}

# регулярний вираз для пошуку типу в тексті (довші ключі перші — щоб "напівзгін" не з'їдався "згін")
_TYPE_RE = re.compile(
    r'(?<![а-яёіїєґa-z])(' +
    '|'.join(sorted((re.escape(k) for k in TYPE_SYNONYMS), key=len, reverse=True)) +
    r')(?![а-яёіїєґ])', re.IGNORECASE)


def normalize_angle_for_category(angle, category):  # нормалізує кут залежно від системи: каналізація 90→87, PUSH кут ігнорується
    if angle is None:
        return None
    if category == 'sewage' and angle == 90:
        return 87   # майстри пишуть 90, але виріб 87
    if category == 'push_systems':
        return None  # у PUSH кут у назвах відсутній
    return angle


def parse_attrs(text: str) -> dict:     # витягує атрибути з назви/запиту: тип, діаметри, кут, різьба, довжина
    t   = text.lower()
    typ = None
    m   = _TYPE_RE.search(t)
    if m:
        typ = TYPE_SYNONYMS.get(m.group(1).lower())

    # шукаємо кут у різних форматах: "х87°", "87 град", "на 90"
    angle = None
    ma = re.search(r'[хx]\s*(15|30|45|67|87|90)(?:[.,]5)?\s*[°º]', t)
    if not ma:
        ma = re.search(r'(?<![0-9])(15|30|45|67|87|90)(?:[.,]5)?\s*(?:[°º]|град)', t)
    if not ma:
        ma = re.search(r'(?:\bна\s+|[хx×]\s*)(45|67|87|90)(?:[.,]5)?(?![\d_.,])', t)
    if ma:
        angle = int(ma.group(1))

    toks   = tokenize(text)
    # різьба: числовий токен у форматі 1_2, 3_4; виключаємо tdim_ токени трійників
    thread = next((tk for tk in toks if '_' in tk and not tk.startswith('tdim')), None)

    # тип різьби: МРЗ/МРВ/РЗ/РВ → окремий атрибут (зовнішня vs внутрішня)
    thread_type = None
    _tl = t  # вже lower
    if any(x in _tl for x in ('mrz', 'рз', ' рз')):   thread_type = 'mrz'  # зовнішня
    elif any(x in _tl for x in ('mrv', 'рв', ' рв')): thread_type = 'mrv'  # внутрішня
    elif 'rn' in _tl or ' рн' in _tl:                  thread_type = 'rn'   # накидна

    # впорядкований рядок діаметрів трійника: ф25х16х25 → "25_16_25"
    dim_ordered = None
    _dm = re.search(r'(\d{2,3})[хx×](\d{2,3})[хx×](\d{2,3})', t)
    if _dm:
        dim_ordered = f"{_dm.group(1)}_{_dm.group(2)}_{_dm.group(3)}"

    # безшумна vs звичайна каналізація
    silent = None
    if any(x in t for x in ('безшум', 's-line', 'sline', 'silent', 'біла')):
        silent = True
    elif any(x in t for x in ('htr', 'ht safe', 'htsafe', 'сіра')):
        silent = False

    # vv/vz — тип з'єднання (внутр-внутр vs внутр-зовн)
    conn_type = None
    if ' vv ' in t or '\bвв\b' in t or 'вв' in t.split():
        conn_type = 'vv'
    elif ' vz ' in t or 'вз' in t.split() or ' vz' in t:
        conn_type = 'vz'

    # довжина труби: витягуємо окремим regex (уникаємо конфлікту з діаметрами)
    length = None
    _lm = re.search(r'l\s*[=:]?\s*(\d+[.,]\d+)\s*м', t)   # L=0,5м L=3,0м L=1.5м
    if _lm:
        length = int(round(float(_lm.group(1).replace(',', '.')) * 1000))
    else:
        _lm = re.search(r'l\s*[=:]?\s*(\d+)\s*м', t)        # L=3м L=1м
        if _lm:
            length = int(_lm.group(1)) * 1000

    # діаметри: числа від 10 до 630 (реальний діапазон каталогу), без кута
    dias = []
    for tk in toks:
        if tk.isdigit():
            v = int(tk)
            if 10 <= v <= 630 and v != angle:
                dias.append(v)
    return {'type': typ, 'dia': sorted(set(dias)), 'angle': angle,
            'thread': thread, 'thread_type': thread_type,
            'dim_ordered': dim_ordered, 'length': length,
            'silent': silent, 'conn_type': conn_type}


def build_qa(пос: dict) -> dict:    # будує атрибути запиту з полів Gemini + парсингу тексту (merge, Gemini пріоритетніший)
    qa = {'type': None, 'dia': [], 'angle': None, 'thread': None, 'thread_type': None, 'dim_ordered': None, 'length': None, 'silent': None, 'conn_type': None}
    # 1) прямо від Gemini (найточніше — він бачив фото)
    g_dia = пос.get('dia')
    if isinstance(g_dia, list):
        qa['dia'] = [int(x) for x in g_dia if str(x).isdigit()]
    if пос.get('type'):
        qa['type'] = TYPE_SYNONYMS.get(str(пос['type']).lower().strip())
    if пос.get('angle') not in (None, '', 0):
        try: qa['angle'] = int(пос['angle'])
        except Exception: pass
    if пос.get('thread'):
        qa['thread'] = str(пос['thread']).replace('/', '_').strip()
    # 2) доповнюємо парсингом тексту якщо Gemini не дав
    for txt in (пос.get('normalized', ''), пос.get('original', '')):
        if not txt: continue
        pa = parse_attrs(txt)
        if not qa['type']:      qa['type']   = pa['type']
        if not qa['dia']:       qa['dia']    = pa['dia']
        if qa['angle'] is None: qa['angle']  = pa['angle']
        if not qa['thread']:    qa['thread'] = pa['thread']
        if qa['length'] is None:      qa['length']      = pa.get('length')
        if qa['thread_type'] is None:  qa['thread_type'] = pa.get('thread_type')
        if qa['dim_ordered'] is None:  qa['dim_ordered'] = pa.get('dim_ordered')
        if qa['silent'] is None:       qa['silent']      = pa.get('silent')
        if qa['conn_type'] is None:    qa['conn_type']   = pa.get('conn_type')
    qa['_raw'] = f"{пос.get('normalized', '')} {пос.get('original', '')}"
    qa['angle'] = normalize_angle_for_category(qa['angle'], пос.get('category'))
    return qa


def _angle_match(qa_angle, item_angle) -> bool:     # перевіряє чи збігається кут запиту з кутом товару (None = будь-який)
    if qa_angle is None:
        return True
    return item_angle == qa_angle


# Виробники яких НЕ продаємо — validate_pick їх відхилятиме
_BANNED_BRANDS = ['raubasic', 'RAUBASIC']

# Типи що НЕ є взаємозамінними — validate_pick відхилятиме
_INCOMPATIBLE_TYPES = [
    ('біметалічний', 'алюмінієвий'),  # радіатори
    ('горизонтальний', 'вертикальний'),  # гідроакумулятори
    ('самоочисний', 'грубої очистки'),  # фільтри
]


def validate_pick(qa: dict, item: dict) -> bool:    # пост-валідація вибору: діаметри, тип, кут, різьба, трійник dim
    ia    = item.get('_attrs') or parse_attrs(item.get('name', ''))

    # Відхиляємо виробників яких не продаємо
    item_name_lower = item.get('name', '').lower()
    if any(b.lower() in item_name_lower for b in _BANNED_BRANDS):
        return False

    # Відхиляємо несумісні типи (біметал ≠ алюміній, горизонт ≠ вертикаль)
    _raw = (qa.get('_raw') or '').lower()
    for t1, t2 in _INCOMPATIBLE_TYPES:
        if t1 in _raw and t2 in item_name_lower:
            return False
        if t2 in _raw and t1 in item_name_lower:
            return False

    q_dia = set(qa.get('dia') or [])
    # Плівка, стрічка, демпфер — товари без діаметра, валідація по діаметру не застосовується
    _skip_dia_types = {'плівка', 'стрічка', 'утеплювач', 'мірелон', 'лічильник'}
    _qa_type = (qa.get('type') or '').lower()
    _item_name_lc = item.get('name', '').lower()
    _qa_raw = (qa.get('_raw') or '').lower()
    _skip_dia = (
        _qa_type in _skip_dia_types or
        any(w in _item_name_lc for w in ('плівк', 'стрічк', 'демпфер', 'мірелон', 'утеплюв', 'лічильн', 'водомір')) or
        any(w in _qa_raw for w in ('лічильн', 'водомір', 'gidrotek'))
    )
    if q_dia and not _skip_dia and not q_dia.issubset(set(ia['dia'])):
        return False
    if qa.get('type') and ia.get('type') and qa['type'] != ia['type']:
        if {qa['type'], ia['type']} != {'коліно', 'відведення'}:
            return False
    if qa.get('angle') and not _angle_match(qa['angle'], ia.get('angle')):
        return False
    # перевірка типу різьби МРЗ/МРВ (зовнішня vs внутрішня — різні товари!)
    if qa.get('thread_type') and ia.get('thread_type'):
        if qa['thread_type'] != ia['thread_type']:
            return False
    # перевірка впорядкованих діаметрів трійника (ф25х16х25 ≠ ф25х16х16)
    if qa.get('dim_ordered') and ia.get('dim_ordered'):
        if qa['dim_ordered'] != ia['dim_ordered']:
            return False
    # перевірка безшумна vs звичайна (sline ≠ htr)
    # Безшумна підходить ТІЛЬКИ якщо явно запитали
    q_sl = qa.get('silent')
    i_sl = ia.get('silent')
    if i_sl is True and q_sl is not True:
        return False   # товар безшумний але не запитували
    if q_sl is True and i_sl is False:
        return False   # запитали безшумну але товар звичайний
    # перевірка ВВ vs ВЗ (conn_type)
    if qa.get('conn_type') and ia.get('conn_type'):
        if qa['conn_type'] != ia['conn_type']:
            return False

    # перевірка типу радіатора (тип 10 ≠ тип 22 — різні товари!)
    _raw_lc = (qa.get('_raw') or '').lower()
    _item_lc = item.get('name', '').lower()
    import re as _re_v
    _q_rad_type = _re_v.search(r'тип\s*(\d+)', _raw_lc)
    _i_rad_type = _re_v.search(r'тип\s*(\d+)', _item_lc)
    if _q_rad_type and _i_rad_type:
        if _q_rad_type.group(1) != _i_rad_type.group(1):
            return False  # запитали тип 10 але знайшло тип 22 — відхиляємо

    # перевірка категорії: якщо запит в [AR] а товар plastic_ppr → відхиляємо
    # (подовжувач/Gebo ≠ PPR муфта)
    _qa_cat   = qa.get('_routed_cat', '')
    _item_cat = item.get('category', '')
    _incompatible_cats = {
        # якщо запитали адаптери — PPR не підходить і навпаки
        ('adapters_reducers', 'plastic_ppr'),
        ('plastic_ppr', 'adapters_reducers'),
        # запірна арматура ≠ PPR
        ('shutoff_valves', 'plastic_ppr'),
        ('plastic_ppr', 'shutoff_valves'),
        # каналізація ≠ PPR  
        ('sewage', 'plastic_ppr'),
        ('plastic_ppr', 'sewage'),
        # тепла підлога ≠ PPR
        ('underfloor_heating', 'plastic_ppr'),
        # котли ≠ радіатори
        ('boilers', 'radiators_radiatorsvalve'),
        ('radiators_radiatorsvalve', 'boilers'),
    }
    if _qa_cat and _item_cat and (_qa_cat, _item_cat) in _incompatible_cats:
        return False  # несумісні категорії

    # перевірка різьби: 1/2" ≠ 1 1/2" (різні розміри!)
    _q_thread = qa.get('thread', '')
    _i_thread = (item.get('_attrs') or {}).get('thread', '')
    if _q_thread and _i_thread and _q_thread != _i_thread:
        # Строга перевірка тільки якщо обидва мають різьбу
        # 1_1_2 (1 1/2") ≠ 1_2 (1/2") — це різні товари!
        if len(_q_thread) != len(_i_thread):
            return False  # різна кількість символів = різний розмір різьби

    return True


# ─── Атрибутний пошук ────────────────────────────────────────────────────────

def attr_search(qa: dict, top_n: int = 10,
                brand_tokens: list = None, category: str = None,
                strict_cat: bool = False) -> list[dict]:  # детермінований пошук; strict_cat=True → тільки ця категорія, без розширення на суміжні
    ensure_tokens()
    if not qa.get('dia') and not qa.get('type'):
        return []   # без атрибутів пошук не має сенсу

    brand_lc = [t.lower() for t in brand_tokens] if brand_tokens else None
    q_dia    = set(qa.get('dia') or [])
    q_type   = qa.get('type')
    q_thread = qa.get('thread')

    # ЖОРСТКА МАРШРУТИЗАЦІЯ: strict_cat=True → тільки ця категорія, без розширення
    if category and strict_cat:
        pools = [
            [it for it in CATALOG if it.get('category') == category],
        ]
    elif category:
        pools = [
            [it for it in CATALOG if it.get('category') == category],
            [it for it in CATALOG if it.get('category') in SIMILAR_CATS.get(category, [])],
            CATALOG,
        ]
    else:
        pools = [CATALOG]

    for pool in pools:
        tiers = {1: [], 2: [], 3: [], 4: []}
        for item in pool:
            if brand_lc and not any(t in item['name'].lower() for t in brand_lc):
                continue
            ia = item.get('_attrs')
            if ia is None:
                continue
            i_dia    = set(ia['dia'])
            if q_thread and ia['thread'] != q_thread:
                continue
            type_ok  = (q_type is not None and ia['type'] == q_type)
            dia_sub  = bool(q_dia) and q_dia.issubset(i_dia)
            dia_eq   = dia_sub and (q_dia == i_dia)
            ang_ok   = _angle_match(qa.get('angle'), ia['angle'])

            # перевірка довжини труби
            q_len  = qa.get('length')
            i_len  = ia.get('length')
            len_ok = (q_len is None or i_len is None or q_len == i_len)

            # перевірка типу різьби МРЗ/МРВ
            q_tt   = qa.get('thread_type')
            i_tt   = ia.get('thread_type')
            tt_ok  = (q_tt is None or i_tt is None or q_tt == i_tt)

            # перевірка впорядкованих діаметрів трійника (ф25х16х25 ≠ ф25х16х16)
            q_do   = qa.get('dim_ordered')
            i_do   = ia.get('dim_ordered')
            do_ok  = (q_do is None or i_do is None or q_do == i_do)

            # перевірка безшумна vs звичайна каналізація (sline ≠ htr)
            # Безшумна підходить ТІЛЬКИ якщо явно запитали (q_sl=True)
            # Якщо запит без маркера (q_sl=None) → безшумну НЕ давати (за замовчуванням звичайна)
            q_sl   = qa.get('silent')
            i_sl   = ia.get('silent')
            if i_sl is True and q_sl is not True:
                sl_ok = False   # товар безшумний але не запитували — не підходить
            elif q_sl is True and i_sl is False:
                sl_ok = False   # запитали безшумну але товар звичайний — не підходить
            else:
                sl_ok = True

            # перевірка ВВ vs ВЗ з'єднання
            q_ct   = qa.get('conn_type')
            i_ct   = ia.get('conn_type')
            ct_ok  = (q_ct is None or i_ct is None or q_ct == i_ct)

            # tier 1: все точно; tier 2: тип+діаметри⊆+кут; tier 3: тип+діаметри; tier 4: тільки діаметри
            if type_ok and dia_eq and ang_ok and len_ok and tt_ok and do_ok and sl_ok and ct_ok:    tiers[1].append(item)
            elif type_ok and dia_sub and ang_ok and len_ok and tt_ok and do_ok and sl_ok and ct_ok: tiers[2].append(item)
            elif type_ok and dia_sub:                                                                 tiers[3].append(item)
            elif dia_sub and q_type is None:                                                          tiers[4].append(item)

        for tier, pct in ((1, 100), (2, 95), (3, 85), (4, 70)):
            cand = tiers[tier]
            if not cand:
                continue
            q_series = {w for w in tokenize(qa.get('_raw', ''))
                        if not w.isdigit() and '_' not in w}

            def score(it):      # ранжування: менше зайвих діаметрів, більше збіг серії, (п/з) вниз
                ia      = it['_attrs']
                extra   = len(set(ia['dia']) - q_dia)
                ser_hit = len(q_series & it.get('_tokens', set()))
                pz      = 5 if '(п/з)' in it['name'] else 0
                return (-ser_hit, extra, pz, len(it['name']))

            cand.sort(key=score)
            out = []
            for it in cand[:top_n]:
                c = dict(it)
                c['_match_pct']  = pct
                c['_attr_tier']  = tier
                out.append(c)
            return out
    return []


def keyword_search(query: str, top_n: int = 12,
                   brand_tokens: list = None,
                   category: str = None, strict_cat: bool = False) -> list[dict]:  # пошук за токенами; strict_cat=True → тільки задана категорія
    ensure_tokens()
    q_tokens  = tokenize(query)
    q_numbers = set(re.findall(r'\d+', query.lower()))
    q_words   = q_tokens - q_numbers
    if not q_tokens:
        return []
    brand_lc = [t.lower() for t in brand_tokens] if brand_tokens else None

    # ЖОРСТКА МАРШРУТИЗАЦІЯ: strict_cat → шукаємо тільки в одній категорії
    if category and strict_cat:
        pool = [it for it in CATALOG if it.get('category') == category]
    else:
        pool = CATALOG

    scores   = []
    for item in pool:
        if brand_lc and not any(t in item['name'].lower() for t in brand_lc):
            continue
        it        = item.get('_tokens', set())
        num_hits  = len(q_numbers & it)
        word_hits = len(q_words & it)
        if num_hits == 0 and word_hits == 0:
            continue
        raw     = num_hits * 3 + word_hits   # числа вагоміші за слова
        penalty = max(0, len(it) - len(q_tokens)) * 0.1    # штраф за зайві токени в назві
        if '(п/з)' in item['name']:
            penalty += 5    # (п/з) товари вниз списку
        pct = min(int(raw / max(len(q_numbers) * 3 + len(q_words), 1) * 100), 100)
        scores.append((raw - penalty, pct, item))
    scores.sort(key=lambda x: -x[0])
    result = []
    for _, pct, item in scores[:top_n]:
        c = dict(item)
        c['_match_pct'] = pct
        result.append(c)
    return result


def smart_search(пос: dict, top_n: int = 12,
                 brand_tokens: list = None) -> list[dict]:  # жорсткий пошук: Voyage AI → attr → keyword; повертає топ кандидатів
    qa = пос.get('_qa')
    if qa is None:
        qa = build_qa(пос)
        пос['_qa'] = qa

    routed_cat = пос.get('_routed_cat') or пос.get('category')
    is_other   = (routed_cat in (None, 'other'))
    query_text = пос.get('normalized', '') or пос.get('original', '')

    # ── КРОК 0: VOYAGE AI — вимкнено (потребує >512MB RAM на Render)
    # if voyage_ready() and query_text: ...

    # ── КРОК 0.5: ПІДГРУПА — шукаємо в точній підгрупі каталогу ────────────
    # group/subgroup береться з xlsx (наприклад "ASG труба", "REHAU", "тип 22")
    if not is_other:
        sub_keywords = route_sub(пос, routed_cat)
        if sub_keywords:
            sub_cand = [
                it for it in CATALOG
                if it.get('category') == routed_cat
                and any(kw.lower() in (it.get('group','') + ' ' + it.get('subgroup','')).lower()
                        for kw in sub_keywords)
            ]
            if sub_cand:
                # Шукаємо attr/keyword тільки серед товарів підгрупи
                sub_tokens_built = all('_tokens' in it for it in sub_cand[:5])
                if sub_tokens_built:
                    q_toks = tokenize(query_text)
                    scored = []
                    for it in sub_cand:
                        hits = len(q_toks & it.get('_tokens', set()))
                        if hits > 0:
                            c = dict(it)
                            c['_match_pct'] = min(int(hits / max(len(q_toks),1) * 100), 100)
                            scored.append(c)
                    scored.sort(key=lambda x: -x['_match_pct'])
                    if scored:
                        return scored[:top_n]

    # ── КРОК 1: СТРОГО у своїй категорії (attr_search strict) ──────────────
    if not is_other:
        cand = attr_search(qa, top_n=top_n, brand_tokens=brand_tokens,
                           category=routed_cat, strict_cat=True)
        if cand:
            return cand

        # ── КРОК 2: keyword у своїй категорії ──────────────────────────────
        cand = keyword_search(
            query_text, top_n=top_n, brand_tokens=brand_tokens,
            category=routed_cat, strict_cat=True
        )
        if cand:
            return cand

        # ── КРОК 3: суміжні категорії (SIMILAR_CATS) ───────────────────────
        similar = SIMILAR_CATS.get(routed_cat, [])
        if similar:
            cand = attr_search(qa, top_n=top_n, brand_tokens=brand_tokens,
                               category=routed_cat, strict_cat=False)
            if cand:
                return cand

    # ── КРОК 4: весь каталог ─────────────────────────────────────────────────
    cand = attr_search(qa, top_n=top_n, brand_tokens=brand_tokens)
    if cand:
        return cand
    return keyword_search(query_text, top_n=top_n, brand_tokens=brand_tokens)


# ─── Claude вибір ────────────────────────────────────────────────────────────

def _parse_claude_json(raw: str) -> list:   # витягує JSON-масив з відповіді Claude (прибирає markdown-огорожі, збирає об'єкти)
    raw   = re.sub(r'```\w*', '', raw).strip()
    start = raw.find('[')
    end   = raw.rfind(']') + 1
    if start != -1 and end > 0:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    objects = []
    for m in re.finditer(r'\{[^{}]*\}', raw[start:] if start != -1 else raw):
        try:
            objects.append(json.loads(m.group()))
        except Exception:
            pass
    return objects


def claude_pick_batch(позиції: list[dict], _retry=True) -> list[dict]:  # відправляє батч кандидатів до Claude; він вибирає правильний товар для кожної позиції
    запити = []
    for i, пос in enumerate(позиції):
        brand_note = f"\n   ⚠️ ТІЛЬКИ: {пос['required_brand']}" if пос.get('required_brand') else ""
        кандидати  = "\n".join(
            f"  {j+1}. [{c.get('_match_pct', 0)}%] {c['name']}"
            for j, c in enumerate(пос['candidates'])
        )
        запити.append(f"{i+1}. {пос['normalized']}{brand_note}\n{кандидати}")

    prompt = f"""Сантехнік. Для кожного запиту — номер кандидата або знайдено=false.

ЕКВІВАЛЕНТИ: кутник/кут/відвід=коліно; канал 87≈90; ASG=HTR; OSTENDORF=HT Safe;
Ekoplastik труби=EVO; Джикоміні≈Raftec (термоклапани);
PUSH: гільза≠кільце, "натяжний" обов'язково.

КРИТИЧНІ ПРАВИЛА:
1. Діаметр/різьба ОБОВ'ЯЗКОВО збігається: 1/2" ≠ 1", 3/4" ≠ 1/2"
2. ВИРОБНИК якщо вказано — ТІЛЬКИ він або знайдено=false
3. ТИП товару МУСИТЬ збігатись (СУВОРО — краще НЕ ЗНАЙДЕНО ніж неправильний тип!):
   - ніпель/футорка/подовжувач ≠ ножиці/муфта/американка/PPR фітинг
   - гідроакумулятор ≠ термостат/мембрана (навіть від того самого виробника!)
   - радіатор БІМЕТАЛІЧНИЙ ≠ алюмінієвий (ЗАБОРОНЕНО як аналог!)
   - зворотний клапан ≠ змішувальний/запобіжний клапан
   - фільтр самоочисний ≠ фільтр грубої очистки
   - трійник латунний ≠ трійник PPR (якщо в запиті немає EKO/PPR → латунний)
   - ніпель латунний ≠ американка PPR
4. Якщо жоден кандидат не підходить → знайдено=false (НЕ підбирати "щось схоже"!)
5. Різьба 1/2 = DN15, 3/4 = DN20, 1" = DN25 — перевіряй відповідність

{chr(10).join(запити)}
JSON рівно {len(позиції)} елементів:
[{{"знайдено":true,"номер_кандидата":1,"confidence":95,"reason":"причина","fail_reason":""}}]"""

    try:
        resp   = claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        parsed = _parse_claude_json(resp.content[0].text)
        if not parsed:
            raise ValueError("порожній JSON")
        while len(parsed) < len(позиції):
            parsed.append({"знайдено": False, "confidence": 0,
                           "reason": "", "fail_reason": "немає відповіді"})
        return parsed[:len(позиції)]
    except Exception as e:
        if _retry and len(позиції) > 1:
            print(f"⚠️ Батч впав ({e}), ретрай поштучно...")
            results = []
            for п in позиції:
                results.extend(claude_pick_batch([п], _retry=False))
            return results
        return [{"знайдено": False, "confidence": 0,
                 "reason": "", "fail_reason": f"Claude: {e}"}] * len(позиції)


# ─── Головна функція пошуку ──────────────────────────────────────────────────

def find_items(позиції: list[dict], progress_cb=None) -> list[dict]:    # основна функція: маршрутизація + 4 рівні пошуку для кожної позиції; повертає список результатів
    # ── ЖОРСТКА МАРШРУТИЗАЦІЯ: визначаємо категорію ПЕРЕД пошуком ──────────
    # Кожна позиція отримує _routed_cat і _prefix на основі тексту нормалізації
    route_batch(позиції)   # мутує позиції: додає _routed_cat і _prefix in-place

    результати        = [None] * len(позиції)
    потребують_claude = []
    retry_позиції     = []

    for i, пос in enumerate(позиції):
        if progress_cb:
            progress_cb(i + 1, len(позиції))
        original     = пос.get('original', '')
        normalized   = пос.get('normalized', '')
        category     = пос.get('category', 'other')
        brand_map    = пос.get('_brand_map', {})
        client_slug  = пос.get('_client_slug')
        client_prefs = пос.get('_client_prefs', {})
        manager_brand = brand_map.get(category)
        # якщо Gemini дав неточну категорію — пробуємо суміжні (але НЕ _global)
        if not manager_brand and brand_map:
            for similar_cat in SIMILAR_CATS.get(category, []):
                if similar_cat in brand_map and similar_cat != '_global':
                    manager_brand = brand_map[similar_cat]
                    break
        # '_global' = м'яка підказка (бренд без категорії від менеджера)
        # використовуємо ТІЛЬКИ якщо виробник прямо в рядку майстра не знайдено
        # і тільки як підказку для normalized — НЕ як жорстке обмеження
        _global_brand = brand_map.get('_global')  # може бути None

        # РІВЕНЬ 1.5: виробник у самому рядку майстра (Wilo, Bonomi, Herz...)
        line_brand = None
        _orig_lc   = original.lower()
        for bk, bt in BRAND_TOKENS.items():
            if re.search(r'(?<![a-zа-яёіїєґ0-9])' + re.escape(bk) + r'(?![a-zа-яёіїєґ0-9])', _orig_lc):
                line_brand = bt
                break
        # hard_brand = жорстке обмеження пошуку (тільки цей виробник)
        # manager_brand — ТІЛЬКИ якщо категорія явно вказана менеджером
        # _global_brand — м'яка підказка, НЕ hard constraint
        hard_brand = manager_brand or line_brand

        # РІВЕНЬ 2: кеш клієнта
        if client_slug:
            c = clients.client_cache_lookup(client_slug, original,
                                            required_brand_tokens=hard_brand)
            if c and cache_is_banned(original, c.get('catalog_name', '')):
                c = None    # бан головніший за клієнтський кеш
            if c:
                _qa_c = пос.get('_qa') or build_qa(пос)
                пос['_qa'] = _qa_c
                if not validate_pick(_qa_c, {'name': c['catalog_name']}):
                    c = None    # кеш суперечить діаметру/типу — ігноруємо
            if c:
                результати[i] = {
                    'original': original, 'normalized': normalized,
                    'знайдено': True, 'назва': c['catalog_name'],
                    'назва_повна': '', 'артикул': '', 'ціна': '',
                    'qty': пос.get('qty', ''), 'category': c.get('category', category),
                    'confidence': c.get('confidence', 0), 'keyword_pct': 100,
                    'джерело': '👤 кеш клієнта' + (' ✅' if c.get('status') == 'confirmed' else ''),
                    'reason': f"З кешу клієнта ({c.get('confidence', 0)}%)",
                    'fail_reason': '', 'candidates_debug': [], '_from_cache': True,
                    '_prefix': пос.get('_prefix', 'XX'),
                    '_routed_cat': пос.get('_routed_cat', 'other'),
                }
                continue

        # РІВЕНЬ 3: кеш бота
        cached = cache_lookup(original, brand_map)
        if cached and cache_is_banned(original, cached.get('catalog_name', '')):
            cached = None   # страховка: бан головніший за будь-який запис
        if cached:
            _qa_b = пос.get('_qa') or build_qa(пос)
            пос['_qa'] = _qa_b
            if not validate_pick(_qa_b, {'name': cached['catalog_name']}):
                cached = None   # кеш суперечить діаметру/типу запиту — у пошук
        if cached:
            ok = True
            if hard_brand:
                nl = cached.get('catalog_name', '').lower()
                ok = any(t.lower() in nl for t in hard_brand)
            if ok:
                результати[i] = {
                    'original': original, 'normalized': cached.get('normalized', normalized),
                    'знайдено': True, 'назва': cached['catalog_name'],
                    'назва_повна': '', 'артикул': '', 'ціна': '',
                    'qty': пос.get('qty', ''), 'category': cached.get('category', category),
                    'confidence': cached.get('confidence', 0), 'keyword_pct': 100,
                    'джерело': '🤖 кеш бота' + (' ✅' if cached.get('status') == 'confirmed' else ''),
                    'reason': f"З кешу ({cached.get('confidence', 0)}%)",
                    'fail_reason': '', 'candidates_debug': [], '_from_cache': True,
                    '_prefix': пос.get('_prefix', 'XX'),
                    '_routed_cat': пос.get('_routed_cat', 'other'),
                }
                continue
            # кеш суперечить підказці виробника — ігноруємо, шукаємо заново

        # РІВЕНЬ 4: живий пошук кандидатів
        кандидати      = []
        required_brand = None
        джерело        = ''
        brand_warning  = ''

        if hard_brand:
            кандидати = smart_search(пос, top_n=12, brand_tokens=hard_brand)
            if кандидати:
                required_brand = hard_brand[0]
                джерело = '👨 менеджер' if manager_brand else '📝 з рядка'
            else:
                кандидати = smart_search(пос, top_n=12)    # виробник не знайдений — шукаємо без нього
                brand_warning = f"⚠️ {hard_brand[0]} відсутній для цієї позиції"
                джерело = '⚠️ fallback'
        else:
            # 4а: преференції клієнта з історії замовлень
            for brand, _cnt in client_prefs.get('by_category', {}).get(category, [])[:3]:
                bt = BRAND_TOKENS.get(brand)
                if not bt: continue
                кандидати = smart_search(пос, top_n=12, brand_tokens=bt)
                if кандидати:
                    required_brand = bt[0]
                    джерело = '👤 профіль клієнта'
                    break
            # 4б: дефолтні виробники для категорії
            if not кандидати:
                for pt in DEFAULT_BRAND_PRIORITY.get(category, []):
                    кандидати = smart_search(пос, top_n=12, brand_tokens=pt)
                    if кандидати:
                        required_brand = pt[0]
                        джерело = '⚙️ дефолт'
                        break
            # 4в: вільний пошук без виробника
            if not кандидати:
                кандидати = smart_search(пос, top_n=12)
                джерело   = '🔍 вільний'

        if not кандидати:
            результати[i] = {**пос, 'знайдено': False, 'назва': '', 'артикул': '',
                              'ціна': '', 'confidence': 0, 'джерело': '',
                              'reason': '', 'fail_reason': 'не знайдено кандидатів',
                              'candidates_debug': []}
            continue

        # фільтруємо заборонені і шрабер (інструмент, не фітинг)
        кандидати = [c for c in кандидати if not cache_is_banned(original, c['name'])]
        if re.search(r'муфт|перех|редукц', normalized.lower()):
            кандидати = [c for c in кандидати if 'шрабер' not in c['name'].lower()]
        if not кандидати:
            результати[i] = {**пос, 'знайдено': False, 'назва': '', 'артикул': '',
                              'ціна': '', 'confidence': 0, 'джерело': '',
                              'reason': '', 'fail_reason': 'всі кандидати забанені',
                              'candidates_debug': []}
            continue

        # ⚡ VOYAGE АВТО-ПРИЙОМ — вимкнено

        # ⚡ АВТО-ПРИЙОМ: очевидний збіг без Claude (швидше і дешевше)
        # умови: tier-1 атрибутний або всі числа збіглись + pct≥95 + відрив від №2 ≥25%
        if кандидати and not brand_warning:
            top      = кандидати[0]
            q_toks   = tokenize(normalized)
            q_nums   = {t for t in q_toks if t[0].isdigit()}
            top_toks = top.get('_tokens', set())
            gap_ok   = (len(кандидати) == 1 or
                        top.get('_match_pct', 0) - кандидати[1].get('_match_pct', 0) >= 25)
            _qa_auto     = пос.get('_qa') or build_qa(пос)
            attr_perfect = (top.get('_attr_tier') == 1 and validate_pick(_qa_auto, top))
            if attr_perfect or (q_nums and q_nums.issubset(top_toks)
                    and top.get('_match_pct', 0) >= 95 and gap_ok
                    and validate_pick(_qa_auto, top)):
                результати[i] = {
                    'original': original, 'normalized': normalized,
                    'знайдено': True, 'назва': top['name'],
                    'назва_повна': top.get('name_full', top['name']),
                    'артикул': top.get('artikul', ''), 'ціна': top.get('price', ''),
                    'qty': пос.get('qty', ''), 'category': category,
                    'confidence': 95, 'keyword_pct': top.get('_match_pct', 0),
                    'джерело': '⚡ точний збіг', 'brand_warning': '',
                    'reason': 'Всі розміри і назва збіглись — вибрано без AI',
                    'fail_reason': '', 'candidates_debug': [c['name'] for c in кандидати[:3]],
                    '_prefix': пос.get('_prefix', 'XX'),
                    '_routed_cat': пос.get('_routed_cat', 'other'),
                }
                # ⚡ точний збіг — відправляємо на підтвердження адміну замість автозбереження
                pending_add(original, brand_map, normalized, top['name'], category, 95, source='auto')
                if client_slug:
                    clients.client_cache_save(client_slug, original, top['name'], category, 95)
                continue

        # збираємо для батчу Claude
        потребують_claude.append({
            'idx': i, 'normalized': normalized, 'original': original,
            'candidates': кандидати, 'candidates_debug': [c['name'] for c in кандидати[:5]],
            'qty': пос.get('qty', ''), 'required_brand': required_brand,
            'category': category, 'brand_map': brand_map,
            'client_slug': client_slug, 'джерело': джерело,
            'brand_warning': brand_warning,
            '_prefix': пос.get('_prefix', 'XX'),
            '_routed_cat': пос.get('_routed_cat', 'other'),
        })

    # ─── Claude batch: відправляємо всі неочевидні позиції одним запитом ─────
    if потребують_claude:
        відповіді = claude_pick_batch(потребують_claude)
        for j, пос in enumerate(потребують_claude):
            r    = відповіді[j] if j < len(відповіді) else {'знайдено': False}
            idx  = пос['idx']
            conf = int(r.get('confidence', 0))

            if r.get('знайдено') and r.get('номер_кандидата'):
                n     = max(0, min(int(r['номер_кандидата']) - 1, len(пос['candidates']) - 1))
                found = пос['candidates'][n]
                # ПОСТ-ВАЛІДАЦІЯ: Claude міг впевнено помилитись — перевіряємо діаметри
                _qa   = пос.get('_qa') or build_qa(пос)
                if not validate_pick(_qa, found):
                    _alt = next((c for c in пос['candidates'] if validate_pick(_qa, c)), None)
                    if _alt is not None:
                        found = _alt
                        r['confidence'] = min(int(r.get('confidence', 0)), 75)
                        r['reason'] = (r.get('reason', '') + ' | ⚙️ авто-заміна: валідація')[:120]
                    else:
                        r['знайдено']    = False
                        r['fail_reason'] = 'валідація: діаметр/тип не збігається'

            if r.get('знайдено') and r.get('номер_кандидата'):
                reason = r.get('reason', '')
                if пос.get('brand_warning'):
                    reason = f"{пос['brand_warning']}. {reason}"
                результати[idx] = {
                    'original': пос['original'], 'normalized': пос['normalized'],
                    'знайдено': True, 'назва': found['name'],
                    'назва_повна': found.get('name_full', found['name']),
                    'артикул': found.get('artikul', ''), 'ціна': found.get('price', ''),
                    'qty': пос['qty'], 'category': пос['category'], 'confidence': conf,
                    'keyword_pct': found.get('_match_pct', 0),
                    'джерело': пос['джерело'], 'brand_warning': пос.get('brand_warning', ''),
                    'reason': reason, 'fail_reason': '',
                    'candidates_debug': пос['candidates_debug'],
                    '_prefix': пос.get('_prefix', 'XX'),
                    '_routed_cat': пос.get('_routed_cat', 'other'),
                }
                # Claude вибір — відправляємо на підтвердження адміну
                pending_add(пос['original'], пос['brand_map'], пос['normalized'],
                            found['name'], пос['category'], conf, source='claude')
                if пос.get('client_slug'):
                    clients.client_cache_save(пос['client_slug'], пос['original'],
                                              found['name'], пос['category'], conf)
            else:
                результати[idx] = {
                    'original': пос['original'], 'normalized': пос['normalized'],
                    'знайдено': False, 'назва': '', 'артикул': '', 'ціна': '',
                    'qty': пос['qty'], 'category': пос['category'],
                    'confidence': conf, 'джерело': '',
                    'reason': '', 'fail_reason': r.get('fail_reason', 'не знайдено'),
                    'candidates_debug': пос['candidates_debug'],
                    '_prefix': пос.get('_prefix', 'XX'),
                    '_routed_cat': пос.get('_routed_cat', 'other'),
                }
                if пос.get('required_brand'):
                    retry_позиції.append(пос)   # кандидат на другий шанс з іншим виробником

        # ─── Другий шанс: у потрібного виробника нема → аналог з ⚠️ ──────────
        if retry_позиції:
            retry_batch = []
            for пос in retry_позиції:
                nc = smart_search(пос, top_n=12)
                nc = [c for c in nc if not cache_is_banned(пос['original'], c['name'])]
                if re.search(r'муфт|перех|редукц', пос['normalized'].lower()):
                    nc = [c for c in nc if 'шрабер' not in c['name'].lower()]
                _rb = пос['required_brand'].lower()
                nc2 = [c for c in nc if _rb not in c['name'].lower()]  # намагаємось знайти інший виробник
                nc  = nc2 or nc
                if nc:
                    retry_batch.append({**пос, 'candidates': nc,
                        'candidates_debug': [c['name'] for c in nc[:5]],
                        'required_brand': None, 'old_brand': пос['required_brand']})
            if retry_batch:
                відп2 = claude_pick_batch(retry_batch)
                for j, пос in enumerate(retry_batch):
                    r = відп2[j] if j < len(відп2) else {'знайдено': False}
                    if r.get('знайдено') and r.get('номер_кандидата'):
                        n     = max(0, min(int(r['номер_кандидата']) - 1, len(пос['candidates']) - 1))
                        found = пос['candidates'][n]
                        warn  = f"⚠️ у {пос['old_brand']} немає — аналог"
                        результати[пос['idx']] = {
                            'original': пос['original'], 'normalized': пос['normalized'],
                            'знайдено': True, 'назва': found['name'],
                            'назва_повна': found.get('name_full', found['name']),
                            'артикул': found.get('artikul', ''), 'ціна': found.get('price', ''),
                            'qty': пос['qty'], 'category': пос['category'],
                            'confidence': int(r.get('confidence', 0)),
                            'keyword_pct': found.get('_match_pct', 0),
                            'джерело': '⚠️ аналог', 'brand_warning': warn,
                            'reason': f"{warn}. {r.get('reason', '')}",
                            'fail_reason': '', 'candidates_debug': пос['candidates_debug'],
                            '_prefix': пос.get('_prefix', 'XX'),
                            '_routed_cat': пос.get('_routed_cat', 'other'),
                        }
                        # аналоги НЕ кешуємо щоб не закріпити заміну назавжди

    # Ціна/артикул/повна назва для кешованих результатів (беремо з актуального каталогу)
    for r in результати:
        if r and r.get('_from_cache') and r.get('назва'):
            for it in CATALOG:
                if it['name'] == r['назва']:
                    r['ціна']        = it.get('price', '')
                    r['артикул']     = it.get('artikul', '')
                    r['назва_повна'] = it.get('name_full', it['name'])
                    break
            r.pop('_from_cache', None)

    return результати
