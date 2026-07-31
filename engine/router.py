"""
engine/router.py — Жорсткий маршрутизатор категорій.

ІДЕЯ:
  Після того як Gemini нормалізував позицію → визначаємо категорію
  (category_code) і далі пошук іде ТІЛЬКИ у тій папці каталогу.

  Два рівні маршрутизації:
  1. type_route()  — за типом товару (труба/кран/муфта...) → категорія
  2. text_route()  — за ключовими словами в normalized+original → уточнення

  Результат: category_code — один з ключів CATALOG_FILES.
  Якщо не вдалось визначити — повертає None (пошук по всьому каталогу).

PREFIX (2-3 літери) для логів і Excel:
  PP = plastic_ppr      KN = sewage          PS = push_systems
  SV = shutoff_valves   AR = adapters_reducers   HT = heating
  BL = boilers          WH = water_heaters    PM = pumps
  RD = radiators        MP = metal_plastic    FL = filtration
  IN = insulation       UF = underfloor_heating  WM = water_meters
  MX = mixers_faucets   TW = towel_warmers    FS = fasteners_sealants
  SW = sanitary_ware    SF = siphons_fittings  HS = hoses
  AT = automation       SV2= safety_valves    XX = не визначено
"""

import re

# ─── Префікси (для Excel-колонки і логів) ────────────────────────────────────

CAT_PREFIX = {          # category_code → 2-3 літерний префікс
    'plastic_ppr':             'PP',
    'sewage':                  'KN',
    'push_systems':            'PS',
    'shutoff_valves':          'SV',
    'adapters_reducers':       'AR',
    'heating':                 'HT',
    'boilers':                 'BL',
    'water_heaters':           'WH',
    'pumps':                   'PM',
    'radiators_radiatorsvalve':'RD',
    'metal_plastic':           'MP',
    'filtration':              'FL',
    'insulation':              'IN',
    'underfloor_heating':      'UF',
    'water_meters':            'WM',
    'mixers_faucets':          'MX',
    'towel_warmers':           'TW',
    'fasteners_sealants':      'FS',
    'sanitary_ware':           'SW',
    'siphons_fittings':        'SF',
    'hoses':                   'HS',
    'automation':              'AT',
    'safety_valves':           'SV2',
    'other':                   'XX',
}

PREFIX_CAT = {v: k for k, v in CAT_PREFIX.items()}  # зворотній маппінг: префікс → category_code


# ─── Маршрути за ТИПОМ товару ─────────────────────────────────────────────────
# Ключ = канонічний тип (після TYPE_SYNONYMS), значення = категорія
# Якщо тип може бути в кількох категоріях — вказуємо першу (основну),
# text_route() уточнить по ключових словах.

TYPE_TO_CAT = {         # тип → основна категорія
    # ─ НІПЕЛЬНІ КОЛІНА PPR (з різьбою РВ/РЗ/РН/ВЗ) ────────────────────────
    # "Коліно ВЗ" / "Коліно РВ 25х3/4" / "Коліно РЗ" / "настінне коліно"
    # ВЗ у PPR = рівноосьове (без різьби) — це звичайне PPR коліно з маркером ВЗ
    # РВ/РЗ/РН = коліно з різьбою (ніпельне) — теж plastic_ppr!
    # НГ = настінне коліно для гіпсокартону — теж plastic_ppr
   # уточнюється нижче (PPR / канал / PEX)
    'муфта':        'plastic_ppr',   # уточнюється (PPR муфта / металопластик / перехід)
    'коліно':       'plastic_ppr',   # уточнюється (PPR / каналізація / PUSH)
    'трійник':      'plastic_ppr',   # уточнюється
    'заглушка':     'plastic_ppr',   # уточнюється
    'перехід':      'adapters_reducers',
    'футорка':      'adapters_reducers',
    'ніпель':       'adapters_reducers',  # але ніпель РАДІАТОРНИЙ → radiators_radiatorsvalve (уточнюється text_route)
    'штуцер':       'adapters_reducers',
    'подовжувач':   'adapters_reducers',  # латунний подовжувач ЗЗ, тільки хромований
    'згін':         'adapters_reducers',
    'напівзгін':    'adapters_reducers',
    'фланець':      'adapters_reducers',
    'хрестовина':   'sewage',         # хрестовини є тільки в каналізації

    # ─ Запірна арматура ─
    'кран':         'shutoff_valves',
    'засувка':      'shutoff_valves',
    'затвор':       'shutoff_valves',
    'клапан':       'shutoff_valves', # уточнюється (зворотній / термостат / безпеки)
    'фільтр':       'filtration',     # уточнюється (фільтр грубої = shutoff_valves)
    'американка':   'shutoff_valves',
    'вентиль':      'shutoff_valves',

    # ─ Каналізація ─
    'ревізія':      'sewage',
    'трап':         'siphons_fittings',
    'сифон':        'siphons_fittings',

    # ─ Опалення / радіатори ─
    'радіатор':     'radiators_radiatorsvalve',
    'термоголовка': 'radiators_radiatorsvalve',
    'термостат':    'radiators_radiatorsvalve',  # уточнюється

    # ─ Насоси / котли ─
    'насос':        'pumps',
    'котел':        'boilers',
    'водонагрівач': 'water_heaters',
    'бойлер':       'water_heaters',

    # ─ Утеплювач ─
    'утеплювач':    'insulation',

    # ─ Інше ─
    'лічильник':    'water_meters',
    'змішувач':     'mixers_faucets',
    'шланг':        'hoses',
    'підводка':     'hoses',
    'хомут':        'fasteners_sealants',
    'опора':        'fasteners_sealants',
    'скоба':        'fasteners_sealants',
    'стрічка':      'fasteners_sealants',
    'плівка':       'underfloor_heating',   # плівка з розміткою для теплої підлоги

    'колектор':     'underfloor_heating',  # уточнюється (гребінка ТП vs розподільчий)
    'гільза':       'push_systems',        # гільзи є тільки у PUSH!
    'кільце':       'push_systems',
    'шафа':         'underfloor_heating',
    'рушникосушка': 'towel_warmers',
    'група':        'automation',          # змішувальний вузол / насосна група
    'вузол':        'automation',
    'комплект':     'other',
}


# ─── Ключові слова для уточнення (text_route) ────────────────────────────────
# Список пар (regex-паттерн, category_code) — перший збіг перемагає.
# Паттерни перевіряються на normalized.lower() + ' ' + original.lower()

TEXT_RULES = [          # список (паттерн, категорія) — перший збіг перемагає
    # ─ PUSH / PEX (перевіряємо першими — найвищий пріоритет) ─
    (r'push|пуш|pex|пекс|натяжн|гільз|євроконус|rautitan|raubasic'
     r'|aquapex|heat.pex|general.fitting|kan\s+pex|tece\s+pex|purmo\s+pex', 'push_systems'),

    # ─ Каналізація ─
    (r'канал|каналіз|htr|htsafe|ht\s?safe|ostendorf|остендорф|s.?line|безшум'
     r'|ревізія|трап|сифон|хрестовин|коліно\s+канал|труба\s+канал'
     r'|skolan|ostendorf.silent'  # крос-бренди безшумної каналізації
     r'|ф\s*\d*\s*\d{2,3}\s*(87|45|30|67|15)|ф110|ф50|ф40|ф32\s+канал', 'sewage'),

    # ─ Металопластик ─
    (r'металопласт|м\/п|м\.п\.|mp\b|пресс.?фітинг|прес.?фіт', 'metal_plastic'),

    # ─ НІПЕЛЬНІ КОЛІНА PPR ────────────────────────────────────────────────────
    (r'колін\w*\s+(ппр|ppr).*(вз|рв|рз|рн|нг)'
     r'|колін\w*\s+(вз|рв|рз|рн)\s+(\d|ф)'
     r'|коліно\s+з\s+накидн\w+\s+гайк'
     r'|настінн\w+\s+колін\w+\s+ппр'
     r'|колін\w+\s+настінн\w+\s+ппр', 'plastic_ppr'),

    # ─ PPR пластик ─
    (r'ппр|ppr|поліпропіл|ekoplastik|екопластик|raftec.*муфт|asg.*муфт'
     r'|муфта\s+(ппр|ppr|mrz|мрз|мрв|rn\b)|коліно\s+(ппр|ppr)'
     r'|трійник\s+(ппр|ppr)|fiber.basalt|faser\b|stabi'
     r'|shengde|wavin.*ppr|pilsa|kalde', 'plastic_ppr'),   # крос-бренди PPR

    # ─ Перехідники / адаптери ─
    (r'ніпел\w+\s+радіатор|радіаторн\w+\s+ніпел|ніпел.+1_1_2|ніпел.+1_2.*радіат', 'radiators_radiatorsvalve'),
    (r'перехідн|редукц|футорк|ніпел|штуцер|подовжувач|бочон|напівзгін'
     r'|згін\b|муфта\s+(перех|редукц)|муфта\s+вв|муфта\s+зз'
     r'|gebo|жебо|затискн\w+\s+муфт|компресійн\w+\s+з.єднан'
     r'|ремонтн\w+\s+затискач'
     r'|fado|valtec|saci|solomon', 'adapters_reducers'),    # крос-бренди перехідників

    # ─ Запірна арматура — крани ─
    (r'кран\s+(кульов|шаров|куль)|кульовий\s+кран|засувк|затвор\s+диск'
     r'|кран\s+(вв|вз|зв|dn|1_2|3_4)|запірн|вентиль\s+(прям|прямий)', 'shutoff_valves'),

    # ─ Арматура безпеки ─
    (r'клапан\s+(безпек|запобіж|скидн|safety|предохран)'
     r'|запобіжн\s+клапан|груп\s+безпек', 'safety_valves'),

    # ─ Зворотній клапан → запірна ─
    (r'клапан\s+(зворот|обратн|зворот)|зворотн\s+клапан', 'shutoff_valves'),

    # ─ Фільтри ─
    (r'фільтр\s+(груб|грубий|осад|магніт|сітч|y.тип)'
     r'|фільтр\s+(dn|1_2|3_4|1\b|вв|вз)|сітчас', 'shutoff_valves'),
    (r'фільтр\s+(тонк|картридж|колб|BB|бб|засип|пом\'якш|умякш|осмос|ugf)'
     r'|система\s+очист|фільтраційн|очищення', 'filtration'),

    # ─ IoT / автоматика ─
    (r'ajax|waterst\w+|розумний\s+кран|iot\s+кран|електрокран'
     r'|waterstop|аквасторож|neptune.*кран|протизатопн', 'automation'),

    # ─ Котли ─
    (r'котел|котл\b|boiler\b|газов\w+\s+котел|твердопалив', 'boilers'),

    # ─ Водонагрівачі ─
    (r'бойлер|водонагрів|накопичув\w+\s+нагрів|нагрівач\s+води'
     r'|електр\w+\s+нагрів', 'water_heaters'),

    # ─ Насоси ─
    (r'насос|цирк\w+\s+насос|підвищ\w+\s+тиск|ape\s*25|grundfos|wilo\b', 'pumps'),

    # ─ Гідроакумулятор → насоси ─
    (r'гідроакумул|бак\s+(мембран|розширюв)|розширюв\w+\s+бак', 'pumps'),

    # ─ Радіатори ─
    (r'радіатор|термоголовк|термостат\w*\s+клапан|клапан\s+радіатор'
     r'|комплект\s+підключ\w+\s+радіат|hidros|хідрос|idmar'
     r'|біметал|bimetal|bi.?vulcan|алюмін.*радіат|радіат.*алюмін', 'radiators_radiatorsvalve'),

    # ─ Автоматика / змішувальні вузли ─
    (r'змішув\w+\s+вузол|насосн\w+\s+груп|автоматик|регулят\w+\s+температ'
     r'|погодн\w+\s+регул|sur03|lsg.16|pcnr', 'automation'),

    # ─ Тепла підлога ─
    (r'тепл\w+\s+підлог|теплопідлог|колектор\s+(тп|підлог)|терморегул\w+\s+(підлог|tp)'
     r'|монтажн\w+\s+стрічк|демпфер|демферн|якір\w+\s+скоб|такер'
     r'|кінцев\w+\s+елемент|кінцевик\s+колект|торцев\w+\s+секц|кінцев\w+\s+комплект'
     r'|плівк\w+\s+розміт|плівк\w+\s+фольг|металіз.*плівк|плівка\s+рулон'
     r'', 'underfloor_heating'),

    # ─ Утеплювач ─
    (r'утеплюв|мірелон|мирелон|k.?flex|thermaflex|plm\b|плм\b|ізоляц\w+\s+труб', 'insulation'),

    # ─ Водоміри ─
    (r'лічильн|водомір|счетчик\s+воды|dn\s*(15|20|25)\s*лічильн|gidrotek', 'water_meters'),

    # ─ Рушникосушки ─
    (r'рушникосуш|полотенцесуш|towel.?warm', 'towel_warmers'),

    # ─ Змішувачі ─
    (r'змішувач|кран\s+(умивальн|мийк|ванн|душ)|смесител|однорукавк', 'mixers_faucets'),

    # ─ Шланги / підводки ─
    (r'шланг|підводк|підведен\w+\s+(води|газу)|гнучк\w+\s+підвод', 'hoses'),

    # ─ Кріплення ─
    (r'хомут|скоб\w+\s+(монтаж|кріпл)|дюбель|шпилька|льон\b|тефлон|фум\b'
     r'|сантехн\w+\s+лен|пакля|прокладк|ущільн|розхідник'
     r'|якір\s+(чорн|одинарн|подвійн|монтажн)'
     r'|glidex|глідекс|силікон.*(каналіз|сантех|труб|герм)'
     r'|герметик|мастило|змазк', 'fasteners_sealants'),  # якір=гак, glidex=мастило

    # ─ Санфаянс ─
    (r'унітаз|раковин|умивальник\s+(кераміч|фаянс)|інсталяц|інсталяція\s+(унітаз|тиск)'
     r'|душов\w+\s+(піддон|кабін)|ванн\w+\s+(акрил|чавун)', 'sanitary_ware'),

    # ─ Сифони ─
    (r'сифон|злив\w+\s+арматур|арматур\w+\s+унітаз|заповнюв\w+\s+клапан|трап\b', 'siphons_fittings'),

    # ─ Опалення (труби, колектори опалення) ─
    (r'опален|радіатор\w+\s+(підключ|кутов|прям)|колектор\s+(опален|1\')', 'heating'),
]


# ─── Основні функції ─────────────────────────────────────────────────────────


SUBCAT_RULES = [    # (regex на normalized+original, keywords для поля group в каталозі)
    # PPR — типи фітингів
    (r'труба\s+(ппр|ppr)|fiber.basalt|faser\b',                ['Труба', 'труба', 'pipe', 'Fiber', 'Faser']),
    (r'муфта.*(мрн|рн\b)|муфта.*накидн',                      ['МРН', 'РН', 'накидн']),
    (r'муфта.*(мрз|рз\b)|mp3\b|мп3\b',                        ['МРЗ', 'РЗ']),
    (r'муфта.*(мрв|рв\b)|mpb\b|мпв\b',                        ['МРВ', 'РВ']),
    (r'колін.*(рв|рз|рн)\b|колін.*настінн|накидн.*гайк',      ['РВ', 'РЗ', 'РН', 'настінн', 'накидн']),
    (r'американка.*(ппр|ppr)',                                  ['Американка', 'американка']),
    # Каналізація
    (r'безшум|s.?line|sline|skolan|ostendorf.silent',          ['безшум', 'бесшум', 'S-LINE']),
    (r'зовн.*канал|зовн.*труб',                                ['зовн', 'Зовн']),
    # PUSH — виробник
    (r'rautitan|raubasic|rehau',                               ['REHAU', 'Rautitan', 'RAUTITAN']),
    (r'raftec.*(push|пуш)|гільза.*raftec',                     ['RAFTEC', 'Raftec']),
    # Запірна
    (r'американка|згін.американ|розбірн.*з.єднан',             ['Американк', 'американк', 'сгон']),
    (r'зворотн.*клапан|клапан.*зворот',                        ['зворот', 'Зворот', 'обратн']),
    (r'фільтр.*(груб|сітч|y.тип|\d)',                          ['Фільтр', 'сетчат', 'фільтр']),
    (r'батерфляй|засувк|затвор.*диск',                         ['батерфляй', 'Батерфляй']),
    # Перехідники — матеріал
    (r'хром.*(подовж|ніпел|перех)|подовж.*хром',               ['хром', 'Хром']),
    (r'жовт.*латун|золот|gold|brass',                          ['жовт', 'Gold', 'Жёлт']),
    (r'нікел|nickel|никел',                                    ['нікел', 'никел', 'Никель']),
    # Радіатори — тип
    (r'тип\s*10\b',                                            ['тип 10', 'тип10']),
    (r'тип\s*11\b',                                            ['тип 11', 'тип11']),
    (r'тип\s*21\b',                                            ['тип 21', 'тип21']),
    (r'тип\s*22\b|22\s*тип',                                   ['тип 22', '22 тип']),
    (r'\bvk\b|нижн.*підключ',                                  ['VK', 'vk', 'нижн']),
    (r'біметал|bi.?vulcan|bi.?vulkan|алюмін.*радіат',          ['біметал', 'Bi-Vulcan', 'алюміній']),
    # Насоси
    (r'циркуляц|цирк.*насос',                                  ['циркул', 'Циркул']),
    (r'гідроакумул|бак.*мембран',                              ['бак', 'Бак', 'гідроакумул']),
    # Котли
    (r'газов.*котел|biasi|teknix',                             ['газов', 'BIASI', 'Biasi']),
    (r'електр.*котел|tatra.*котел',                            ['електр', 'Tatra']),
    (r'твердопалив|дтм\b',                                     ['ДТМ', 'дтм']),
]

def route_sub(пос: dict, cat: str) -> list[str] | None:    # повертає ключові слова підкатегорії або None якщо фільтр не потрібен
    import re as _re
    combined = ((пос.get('normalized') or '') + ' ' + (пос.get('original') or '')).lower()
    for pattern, keywords in SUBCAT_RULES:
        if _re.search(pattern, combined, _re.IGNORECASE):
            return keywords
    return None


# Товари яких НЕ ПРОДАЄМО — бот не шукатиме, одразу знайдено=false
NOT_OUR_ITEMS = re.compile(
    r'рукавиц|рукавичк'
    r'|диск відрізн|диск по (бетон|метал)'
    r'|бур по бетон'
    r'|мішок будівельн|будівельн мішок'
    r'|олівець будівельн'
    r'|маркер (чорн|синій)'
    r'|скоби (до|для) степлера\s+\d'
    r'|серветка (до|для) міді'
    , re.IGNORECASE
)

# Додаткова перевірка для "мішок" — не наш якщо немає контексту солі
def _is_bag_not_ours(combined: str) -> bool:
    if re.search(r'\bмішок\b', combined, re.IGNORECASE):
        if re.search(r'сіл|таблет', combined, re.IGNORECASE):
            return False   # сіль у мішку — наша позиція
        return True
    return False


def is_not_our(пос: dict) -> bool:      # повертає True якщо товар не з нашого асортименту
    combined = (пос.get('normalized', '') + ' ' + пос.get('original', '')).lower()
    if NOT_OUR_ITEMS.search(combined):
        return True
    return _is_bag_not_ours(combined)


def route(пос: dict) -> str:    # визначає category_code для позиції; повертає код або 'other'
    """
    Головна функція маршрутизації.
    Вхід:  позиція від Gemini (dict з полями normalized, original, category, type, dia)
    Вихід: category_code — один з ключів CATALOG_FILES

    Пріоритети:
      0. NOT_OUR_ITEMS — якщо не наш товар → одразу 'not_ours'
      1. Якщо Gemini вже дав category і це не 'other' → перевіряємо text_rules для уточнення
      2. type_route() за полем type від Gemini
      3. text_route() за ключовими словами
      4. Категорія від Gemini як є
      5. 'other'
    """
    normalized = (пос.get('normalized') or '').lower()
    original   = (пос.get('original')   or '').lower()
    gemini_cat = (пос.get('category')   or '').lower().strip()
    gemini_type = (пос.get('type')      or '').lower().strip()
    combined   = normalized + ' ' + original    # рядок для regex-пошуку

    # 0. Не наш товар → не шукати
    if is_not_our(пос):
        return 'not_ours'

    # 1. Спочатку — жорсткий text_route (незалежно від Gemini)
    #    Він перемагає бо маппінг детальніший за Gemini
    tr = _text_route(combined)
    if tr:
        return tr

    # 2. type_route — за типом від Gemini
    #    Потім уточнюємо text_route для multi-category типів
    if gemini_type:
        from engine.search import TYPE_SYNONYMS  # lazy import
        canon = TYPE_SYNONYMS.get(gemini_type)
        if canon and canon in TYPE_TO_CAT:
            base = TYPE_TO_CAT[canon]
            # Для типів що живуть в кількох категоріях — уточнюємо
            if canon in ('труба', 'муфта', 'коліно', 'трійник', 'заглушка',
                         'клапан', 'фільтр', 'колектор'):
                return _refine_multicat(combined, base)
            return base

    # 3. Категорія від Gemini якщо не 'other'
    if gemini_cat and gemini_cat != 'other' and gemini_cat in CAT_PREFIX:
        return gemini_cat

    return 'other'


def get_prefix(category_code: str) -> str:  # повертає 2-3 літерний префікс для категорії
    return CAT_PREFIX.get(category_code, 'XX')


def label(пос: dict) -> str:    # повертає рядок "[PP] Труба PPR..." для логів і Excel-стовпця
    cat = пос.get('_routed_cat') or route(пос)
    return f"[{get_prefix(cat)}] {пос.get('normalized', '')}"


# ─── Внутрішні ───────────────────────────────────────────────────────────────

def _text_route(combined: str) -> str | None:   # перевіряє TEXT_RULES по черзі; повертає першу категорію або None
    for pattern, cat in TEXT_RULES:
        if re.search(pattern, combined, re.IGNORECASE):
            return cat
    return None


def _refine_multicat(combined: str, base_cat: str) -> str:  # уточнює категорію для типів що зустрічаються у кількох прайсах
    """
    Для типів які є в кількох категоріях (труба, муфта, коліно, трійник...)
    намагаємось уточнити категорію за ключовими словами.
    Якщо не вдалось — повертаємо base_cat.
    """
    # Вже перевірено в _text_route але на всяк випадок
    tr = _text_route(combined)
    return tr if tr else base_cat


# ─── Статистика для дебагу ───────────────────────────────────────────────────

def route_batch(позиції: list[dict]) -> list[dict]:     # додає поле _routed_cat і _prefix до кожної позиції списку
    """Додає _routed_cat і _prefix до кожної позиції. Повертає позиції."""
    for пос in позиції:
        cat = route(пос)
        пос['_routed_cat'] = cat
        пос['_prefix']     = get_prefix(cat)
    return позиції
