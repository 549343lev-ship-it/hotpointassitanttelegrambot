"""
router.py — Ієрархічний маршрутизатор категорій.

КОНЦЕПЦІЯ (адресна система):
  Кожна "папка" каталогу має унікальний ID з інкрементних літер.
  Кожен рівень вкладеності додає літери до батьківського ID.

  Приклади:
    kn          → Каналізація (sewage)
    kn.u        → Внутрішня каналізація
    kn.u.p      → Труби внутрішньої каналізації
    kn.u.p.a    → ASG труби (внутрішня)
    kn.u.p.b    → Ostendorf труби (внутрішня)
    kn.u.f      → Фітинги внутрішньої каналізації
    kn.e        → Зовнішня каналізація
    kn.s        → Безшумна (S-LINE)
    pp          → PPR пластик
    pp.p        → PPR труби
    pp.p.a      → ASG труби PPR
    pp.f        → PPR фітинги
    pp.f.m      → Муфти PPR

ЛОГІКА ПОШУКУ:
  1. route() → повертає node_id (наприклад 'kn.u.p.a')
  2. Пошук фільтрує: item['node_id'].startswith(node_id)
     → знаходить усі товари у вузлі і всіх підвузлах

СТРУКТУРА ВУЗЛА (NODE):
  id:        str   — унікальний ієрархічний ID ('kn.u.p.a')
  category:  str   — назва категорії в каталозі ('sewage')
  patterns:  list  — regex-паттерни що потрапляють у цей вузол
  groups:    list  — рядки що шукаємо в полі group товару (для sub-фільтрації)
  brands:    list  — ключові слова виробника (для sub-фільтрації)

ЗВОРОТНЯ СУМІСНІСТЬ:
  CAT_PREFIX, PREFIX_CAT, get_prefix(), route_batch(), label() — збережено.
  route() тепер повертає category_code (як раніше), але також додає _node_id.
"""

import re
from typing import Optional


# ─── Рівень 1: Кореневі вузли (=категорії каталогу) ─────────────────────────
# id → category_code.  Збігається з ключами CATALOG_FILES.

ROOT_NODES: dict[str, str] = {   # node_id → category_code
    'pp':  'plastic_ppr',
    'kn':  'sewage',
    'ps':  'push_systems',
    'sv':  'shutoff_valves',
    'ar':  'adapters_reducers',
    'ht':  'heating',
    'bl':  'boilers',
    'wh':  'water_heaters',
    'pm':  'pumps',
    'rd':  'radiators_radiatorsvalve',
    'mp':  'metal_plastic',
    'fl':  'filtration',
    'ins': 'insulation',
    'uf':  'underfloor_heating',
    'wm':  'water_meters',
    'mx':  'mixers_faucets',
    'tw':  'towel_warmers',
    'fs':  'fasteners_sealants',
    'sw':  'sanitary_ware',
    'sf':  'siphons_fittings',
    'hs':  'hoses',
    'at':  'automation',
    'sav': 'safety_valves',
}

CAT_NODE: dict[str, str] = {v: k for k, v in ROOT_NODES.items()}   # category_code → root node_id


# ─── Дерево вузлів ───────────────────────────────────────────────────────────
# Кожен запис: (node_id, category_code, [regex_patterns], [group_keywords])
# Паттерни: перевіряються на normalized.lower()+' '+original.lower()
# group_keywords: перевіряються в полі 'group' + 'subgroup' товару при sub-фільтрації
#
# ВАЖЛИВО: порядок має значення — перший збіг перемагає.
# Специфічніші правила — вище!

TREE: list[tuple] = [
    # ─────────────────────────────────────────────────────────────────────────
    # КАНАЛІЗАЦІЯ (kn)
    # ─────────────────────────────────────────────────────────────────────────

    # Зовнішня каналізація
    ('kn.e',     'sewage',  [r'зовн\w*\s+канал|наруж\w*\s+канал|канал.*зовн|труба.*\bø\b.*нар|ф160|ф200|sn\d'],
                            ['*Наружная', 'зовн', 'Зовнішня']),

    # Безшумна (S-LINE / Ostendorf бесшумная / Valrom бесшумная)
    ('kn.s',     'sewage',  [r'безшум|s[\.\-]?line|sline|silent|raupiano'],
                            ['безшум', 'бесшум', 'OSTENDORF бесшумная', 'Valrom бесшумная',
                             'ASG безшумна', 'HTsafe', 'Rehau Raupiano']),

    # Внутрішня — труби → виробники
    ('kn.u.p.a', 'sewage',  [r'труба\s+канал.*asg|asg.*труба\s+канал|труба.*сіра.*asg|asg.*сіра'],
                            ['*ASG', 'ASG', 'Труба серая (внутренняя)']),

    ('kn.u.p.b', 'sewage',  [r'труба\s+канал.*ostendorf|ostendorf.*труба\s+канал|ostendorf.*сіра'],
                            ['OSTENDORF', 'Ostendorf']),

    ('kn.u.p.c', 'sewage',  [r'труба\s+канал.*valrom|valrom.*труба\s+канал'],
                            ['*Valrom внутренняя', 'VALROM']),

    ('kn.u.p.d', 'sewage',  [r'труба\s+канал.*plm|plm.*труба\s+канал'],
                            ['PLM']),

    ('kn.u.p.e', 'sewage',  [r'труба\s+канал.*raftec|raftec.*труба\s+канал'],
                            ['Raftec (Germany)']),

    # Внутрішня — труби (без бренду)
    # ТІЛЬКИ якщо немає маркерів PPR/металопластик/push в рядку
    ('kn.u.p',   'sewage',  [r'труба\s+канал|канал.*труб|сіра\s+труба'],
                            ['Труба', '*Внутренняя', 'Труба серая (внутренняя)']),

    # Внутрішня — фітинги → виробники
    # Паттерн: "коліно канал ... asg" АБО "asg ... коліно ... канал" АБО "коліно ° ... asg ... канал"
    ('kn.u.f.a', 'sewage',  [r'(коліно|муфта|трійник|заглушка|ревізія).*asg.*(канал|°|\d{2})'
                              r'|asg.*(коліно|трійник|муфта).*(канал|°|\d{2})'
                              r'|(коліно|муфта|трійник).*канал.*asg'],
                            ['ASG', 'Фитинг']),

    ('kn.u.f.b', 'sewage',  [r'(коліно|муфта|трійник|заглушка).*(канал|°|\d{2}).*ostendorf'
                              r'|ostendorf.*(коліно|трійник|муфта).*(канал|°|\d{2})'
                              r'|(коліно|муфта|трійник).*канал.*ostendorf'],
                            ['OSTENDORF', 'Ostendorf']),

    # Внутрішня — фітинги (без бренду)
    # НЕ PPR (ppr/ппр/вз без канал контексту)
    ('kn.u.f',   'sewage',  [r'(коліно|муфта|трійник|заглушка|хрестовина|ревізія)\s+(канал|°\s*\d)'
                              r'|фітинг\s+канал'
                              r'|канал.*(коліно|муфта|трійник|заглушка|хрестовина)'
                              r'|(коліно|трійник).*\d{2}°.*канал'
                              r'|\d{2}°.*канал.*(коліно|трійник)'],
                            ['Фитинг', '*Внутренняя']),

    # Внутрішня (загально)
    ('kn.u',     'sewage',  [r'внутр\w*\s+канал|канал.*внутр|ф\s*\d{2,3}.*канал'
                              r'|ф50\b|ф110\b|ф32\s+канал|ф40\b|ревізія|трап\b'],
                            ['*Внутренняя', 'Внутрішня', 'Труба серая (внутренняя)']),

    # Каналізація загально
    ('kn',       'sewage',  [r'канал|каналіз|sewage|htr\b|ht\s?safe|ostendorf|остендорф'
                              r'|ревізія|трап|сифон\s+канал|хрестовин'],
                            []),

    # ─────────────────────────────────────────────────────────────────────────
    # PPR ПЛАСТИК (pp)
    # ─────────────────────────────────────────────────────────────────────────

    # Труби → виробники
    ('pp.p.a',   'plastic_ppr', [r'труба\s+(ппр|ppr).*asg|asg.*труба\s+(ппр|ppr)'],
                                ['ASG труба', 'ASG']),

    ('pp.p.b',   'plastic_ppr', [r'труба\s+(ппр|ppr).*(ekoplastik|екопластик|eco|eko)'
                                  r'|(ekoplastik|eco\s+ppr).*труба'],
                                ['ECO PPR', 'Ekoplastik', 'OVI/EVCI Pipe', 'WHITE']),

    ('pp.p.c',   'plastic_ppr', [r'труба\s+(ппр|ppr).*raftec|raftec.*труба\s+(ппр|ppr)'],
                                ['RAFTEC', 'Raftec']),

    # Труби PPR (без бренду)
    ('pp.p',     'plastic_ppr', [r'труба\s+(ппр|ppr)|труба\s+поліпропіл|(ппр|ppr).*труба'],
                                ['ASG труба', 'ECO PPR', 'OVI/EVCI Pipe', 'WHITE']),

    # Фітинги → тип
    ('pp.f.m',   'plastic_ppr', [r'муфта\s+(ппр|ppr)|муфта.*(мрз|мрв|рн\b|рз\b|рв\b)'
                                  r'|(мрз|мрв|рн|рз|рв).*муфта'],
                                ['ASG фитинг', 'Фитинг PPR']),

    ('pp.f.k',   'plastic_ppr', [r'коліно\s+(ппр|ppr)|коліно.*(рв|рз|рн|вз|нг)'
                                  r'|настінн\w+\s+коліно.*ппр'],
                                ['ASG фитинг', 'Фитинг PPR']),

    ('pp.f.t',   'plastic_ppr', [r'трійник\s+(ппр|ppr)|(ппр|ppr).*трійник'],
                                ['ASG фитинг', 'Фитинг PPR']),

    ('pp.f.z',   'plastic_ppr', [r'заглушка\s+(ппр|ppr)|(ппр|ppr).*заглушка'],
                                ['ASG фитинг', 'Фитинг PPR']),

    # Фітинги PPR (загально)
    ('pp.f',     'plastic_ppr', [r'(муфта|коліно|трійник|заглушка|перехід|американка)\s+(ппр|ppr)'
                                  r'|(ппр|ppr).*(муфта|коліно|трійник|заглушка|фітинг)'],
                                ['ASG фитинг', 'Фитинг PPR']),

    # PPR загально
    ('pp',       'plastic_ppr', [r'ппр|ppr|поліпропіл|ekoplastik|екопластик|raftec.*муфт'
                                  r'|asg.*муфт|evo\b|fiber\b|faser\b|stabi'],
                                []),

    # ─────────────────────────────────────────────────────────────────────────
    # PUSH / PEX (ps)
    # ─────────────────────────────────────────────────────────────────────────

    # Труби PEX → виробники
    ('ps.p.a',  'push_systems', [r'труба.*rautitan|труба.*raubasic|rautitan.*труба|rehau.*труба.*pex'],
                                ['REHAU', 'Rautitan', 'Raubasic']),

    ('ps.p.b',  'push_systems', [r'труба.*aquapex|aquapex.*труба'],
                                ['Aquapex', 'AQUAPEX']),

    ('ps.p.c',  'push_systems', [r'труба.*heat.?pex|heat.?pex.*труба'],
                                ['HEAT-PEX']),

    ('ps.p.d',  'push_systems', [r'труба.*raftec.*push|raftec.*push.*труба'],
                                ['RAFTEC', 'RAFTEC PPSU PUSH']),

    # Труби PEX загально
    ('ps.p',    'push_systems', [r'труба.*(pex|пекс|push|пуш)|pex.*труба'],
                                ['Труба']),

    # Фітинги PUSH → виробники
    ('ps.f.a',  'push_systems', [r'(муфта|коліно|трійник|фітинг).*(rehau|general.fitting)'
                                  r'|(rehau|general.?fitting).*(муфта|коліно|трійник|фітинг)'],
                                ['REHAU', 'General Fittings']),

    ('ps.f.b',  'push_systems', [r'(муфта|коліно|трійник|фітинг).*kan\b|(kan).*(муфта|коліно|трійник)'],
                                ['KAN', 'KAN-Therm PUSH']),

    # Фітинги PUSH загально
    ('ps.f',    'push_systems', [r'(муфта|коліно|трійник|фітинг).*(push|пуш|pex|pекс)'
                                  r'|(push|пуш).*(муфта|коліно|трійник|фітинг)|євроконус|натяжн'],
                                ['Натяжной фитинг', 'General Fittings', 'FADO']),

    # Гільзи / кільця
    ('ps.g',    'push_systems', [r'гільза.*(push|pex)|кільце.*(push|pex)|(push|pex).*(гільза|кільце)'],
                                ['RAFTEC', 'REHAU']),

    # PUSH загально
    ('ps',      'push_systems', [r'push|пуш|pex|пекс|натяжн|гільза|євроконус|rautitan|raubasic'
                                  r'|aquapex|heat.?pex|general.fitting'],
                                []),

    # ─────────────────────────────────────────────────────────────────────────
    # МЕТАЛОПЛАСТИК (mp)
    # ─────────────────────────────────────────────────────────────────────────

    ('mp.f.a',  'metal_plastic', [r'(прес.?фіт|пресс.?фіт|муфта|коліно|трійник).*fado|fado.*(фіт|муфта|коліно)'],
                                 ['FADO', 'М/П всё']),

    ('mp.f.b',  'metal_plastic', [r'(прес.?фіт|муфта|коліно|трійник).*raftec|raftec.*(фіт|муфта|коліно|прес)'],
                                 ['RAFTEС', 'RAFTEC']),

    ('mp.f',    'metal_plastic', [r'прес.?фіт|пресс.?фіт|м/?п.*(муфта|коліно|трійник|фіт)'
                                   r'|(муфта|коліно|трійник).*(м/?п|металопласт)'],
                                 ['М/П всё']),

    ('mp',      'metal_plastic', [r'металопласт|м\/п|м\.п\.|mp\b|пресс.?фіт|прес.?фіт'],
                                 []),

    # ─────────────────────────────────────────────────────────────────────────
    # ЗАПІРНА АРМАТУРА (sv)
    # ─────────────────────────────────────────────────────────────────────────

    ('sv.k',    'shutoff_valves', [r'кран.*(кульов|шаров|куль)|кульовий.?кран'],
                                  ['ASG', 'RAFTEC', 'Giacomini', 'HLV', 'LEXLINE']),

    ('sv.z',    'shutoff_valves', [r'зворотн.?клапан|клапан.?зворот'],
                                  ['зворот', 'Зворот']),

    ('sv.f',    'shutoff_valves', [r'фільтр.?(груб|сітч|y.тип)|грубої.?очистки'],
                                  ['Фільтр', 'сетчат']),

    ('sv.b',    'shutoff_valves', [r'батерфляй|засувк|затвор.?диск'],
                                  ['батерфляй']),

    ('sv',      'shutoff_valves', [r'кран\s+(кульов|вв|вз|зв|dn|1_2|3_4)|засувк|затвор'
                                    r'|кран.?кульов|кульовий.?кран|запірн|вентиль'],
                                  []),

    # ─────────────────────────────────────────────────────────────────────────
    # ПЕРЕХІДНИКИ / АДАПТЕРИ (ar)
    # ─────────────────────────────────────────────────────────────────────────

    ('ar.n',    'adapters_reducers', [r'ніпел.+радіатор|радіаторн.+ніпел'],
                                     ['НІПЕЛЬ РАДІАТОРНИЙ']),

    ('ar.h',    'adapters_reducers', [r'хром.*(подовж|ніпел|перех)|подовж.*хром'],
                                     ['хром', 'Хром']),

    ('ar.l',    'adapters_reducers', [r'жовт.?латун|gold|brass|raftec.?gold|lexline'],
                                     ['LEXLINE желтая', 'RAFTEC GOLD', 'УЗКМ жовта']),

    ('ar',      'adapters_reducers', [r'перехідн|редукц|футорк|ніпел|штуцер|подовжувач'
                                       r'|бочон|напівзгін|згін\b|gebo|жебо|затискн.?муфт'
                                       r'|компресійн.?з.єднан'],
                                     []),

    # ─────────────────────────────────────────────────────────────────────────
    # РАДІАТОРИ (rd)
    # ─────────────────────────────────────────────────────────────────────────

    ('rd.t10',  'radiators_radiatorsvalve', [r'тип\s*10\b|тип10'],   ['тип 10', 'тип10']),
    ('rd.t11',  'radiators_radiatorsvalve', [r'тип\s*11\b|тип11'],   ['тип 11', 'тип11']),
    ('rd.t21',  'radiators_radiatorsvalve', [r'тип\s*21\b|тип21'],   ['21', 'тип 21']),
    ('rd.t22',  'radiators_radiatorsvalve', [r'тип\s*22\b|22\s*тип'], ['22 тип', 'тип 22']),
    ('rd.vk',   'radiators_radiatorsvalve', [r'\bvk\b|нижн.*підключ'], ['VK', 'vk']),
    ('rd.bm',   'radiators_radiatorsvalve', [r'біметал|bi.vulcan|алюмін.*радіат'],
                                            ['біметал', 'алюміній']),
    ('rd.th',   'radiators_radiatorsvalve', [r'термоголовк|термостат.*радіат|термокомплект'],
                                            ['Термоголовки', 'Термостат']),

    ('rd',      'radiators_radiatorsvalve', [r'радіатор|батарея\s+(опален|сталев)|термоголовк|термостат'],
                                            []),

    # ─────────────────────────────────────────────────────────────────────────
    # ТЕПЛА ПІДЛОГА (uf)
    # ─────────────────────────────────────────────────────────────────────────

    ('uf.c',    'underfloor_heating', [r'колектор.*(тп|підлог|uf)|гребінка.?(тп|uf)'
                                       r'|гребінка.*(plm|raftec|латун|steel|4.*вих|6.*вих|8.*вих|12.*вих)'
                                       r'|(plm|raftec).*(гребінк|колектор)'],
                                      ['Коллектора RAFTEC', 'Коллектора ASG', 'RAFTEC Brass',
                                       'RAFTEC Stainless']),

    ('uf.r',    'underfloor_heating', [r'терморегул.*(підлог|тп|tp)|терморегулятор\s+tp'],
                                      ['Терморегулятор']),

    ('uf.s',    'underfloor_heating', [r'монтажн.?стрічк|демпфер|якірн.?скоб|такер'
                                       r'|плівк.?(розміт|фольг|рулон)|металіз.*плівк'],
                                      ['Монтажна стрічка', 'Плівка']),

    ('uf',      'underfloor_heating', [r'тепл.?підлог|теплопідлог|колектор.*(тп|підлог)'
                                        r'|терморегул.*(підлог|tp)|монтажн.?стрічк|демпфер'
                                        r'|демферн|якірн.?скоб|такер|плівк.?розміт'],
                                      []),

    # ─────────────────────────────────────────────────────────────────────────
    # НАСОСИ (pm)
    # ─────────────────────────────────────────────────────────────────────────

    ('pm.c',    'pumps', [r'циркуляц|цирк.*насос'],  ['Wilo', 'Grundfos', 'циркул']),
    ('pm.s',    'pumps', [r'станц.*насос|насосн.?станц|New Wave|нью вейв'],
                         ['New Wave', 'Станция']),
    ('pm.h',    'pumps', [r'гідроакумул|бак.*мембран|мембран.*бак'],
                         ['Гідроакумулятор', 'бак']),

    ('pm',      'pumps', [r'насос|насосн|помпа'], []),

    # ─────────────────────────────────────────────────────────────────────────
    # КОТЛИ (bl)
    # ─────────────────────────────────────────────────────────────────────────

    ('bl.g',    'boilers', [r'газов.*котел|biasi|teknix'],    ['BIASI', 'газов']),
    ('bl.e',    'boilers', [r'електр.*котел|tatra'],          ['електр', 'Tatra']),
    ('bl.t',    'boilers', [r'твердопалив|дтм\b'],            ['ДТМ']),

    ('bl',      'boilers', [r'котел|бойлерна.*установка'], []),

    # ─────────────────────────────────────────────────────────────────────────
    # ВОДОНАГРІВАЧІ (wh)
    # ─────────────────────────────────────────────────────────────────────────

    ('wh',      'water_heaters', [r'водонагрівач|бойлер\b|титан\b|водонагр'], []),

    # ─────────────────────────────────────────────────────────────────────────
    # ОПАЛЕННЯ (ht)
    # ─────────────────────────────────────────────────────────────────────────

    ('ht.h',    'heating', [r'herz.*клапан|клапан.*herz|термозмішув'],    ['Herz', 'HERZ']),
    ('ht.c',    'heating', [r'колектор\s+(опален|1\')|колектор.*opalen'],  ['Коллектора - теплый пол']),

    ('ht',      'heating', [r'опален|радіатор.*(підключ|кутов|прям)'
                             r'|колектор\s+(опален|1\'|ht)'], []),

    # ─────────────────────────────────────────────────────────────────────────
    # АВТОМАТИКА (at)
    # ─────────────────────────────────────────────────────────────────────────

    ('at.m',    'automation', [r'насосн.?груп|змішувальн.?вузол|гідравліч.?стрілк'],
                              ['Насосні групи', 'Змішувальні вузли']),

    ('at.r',    'automation', [r'погодн.?регул|регул.*sur03|lsg.?16|pcnr'],
                              ['Регулятор']),

    ('at',      'automation', [r'насосн.?груп|змішувальн.?вузол|гідравліч.?стрілк'
                                r'|погодн.?регул|sur03|lsg.?16|pcnr|автоматик.*(насос|котел)'],
                              []),

    # ─────────────────────────────────────────────────────────────────────────
    # ІНШІ КАТЕГОРІЇ (без підвузлів)
    # ─────────────────────────────────────────────────────────────────────────

    ('fl',      'filtration',        [r'фільтр|filtration|ecosoft|екософт|картридж|колб.?фільтр'], []),
    ('ins',     'insulation',        [r'утеплюв|мірелон|k.?flex|thermaflex|ізоляц.?труб'], []),
    ('wm',      'water_meters',      [r'лічильн|водомір|водомер|gidrotek|dn\s*(15|20|25)\s*лічильн'], []),
    ('mx',      'mixers_faucets',    [r'змішувач|кран\s+(умивальн|мийк|ванн|душ)|смесител|однорукавк'], []),
    ('tw',      'towel_warmers',     [r'рушникосуш|полотенцесуш|towel.?warm'], []),
    ('hs',      'hoses',             [r'шланг|підводк|підведен.*(води|газу)|гнучк.?підвод'], []),
    ('fs',      'fasteners_sealants',[r'хомут|скоб.*(монтаж|кріпл)|дюбель|шпилька'
                                       r'|льон\b|тефлон|фум\b|пакля|прокладк|ущільн'
                                       r'|glidex|глідекс|герметик|мастило|силікон'], []),
    ('sw',      'sanitary_ware',     [r'унітаз|раковин|умивальник\s+(кераміч|фаянс)'
                                       r'|інсталяц|душов.*(піддон|кабін)|ванн.*(акрил|чавун)'], []),
    ('sf',      'siphons_fittings',  [r'сифон|злив.?арматур|арматур.?унітаз|заповнюв.?клапан|трап\b'], []),
    ('sav',     'safety_valves',     [r'запобіжн.?клапан|клапан.*безпек|клапан.*запобіжн'
                                       r'|safety.?valve|скидн.?клапан'], []),
]


# ─── Таблиця для зворотного пошуку: category_code → всі node_id у цій категорії ──
# Використовується для sub-фільтрації у пошуку.

_cat_to_nodes: dict[str, list[str]] = {}
for _nid, _cat, *_ in TREE:
    _cat_to_nodes.setdefault(_cat, []).append(_nid)


# ─── Зворотня сумісність: CAT_PREFIX, PREFIX_CAT ─────────────────────────────

CAT_PREFIX: dict[str, str] = {   # category_code → верхній node_id (як 2-3-літерний префікс)
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
    'insulation':              'INS',
    'underfloor_heating':      'UF',
    'water_meters':            'WM',
    'mixers_faucets':          'MX',
    'towel_warmers':           'TW',
    'fasteners_sealants':      'FS',
    'sanitary_ware':           'SW',
    'siphons_fittings':        'SF',
    'hoses':                   'HS',
    'automation':              'AT',
    'safety_valves':           'SAV',
    'other':                   'XX',
}

PREFIX_CAT: dict[str, str] = {v: k for k, v in CAT_PREFIX.items()}


# ─── Основні функції ─────────────────────────────────────────────────────────

def route(пос: dict) -> str:    # визначає category_code і node_id для позиції
    """
    Головна функція маршрутизації.

    Вхід:  позиція від Gemini (dict: normalized, original, category, type, dia)
    Вихід: category_code (рядок типу 'sewage').

    Побічно додає в пос:
      _node_id   — ієрархічний ID найточнішого вузла ('kn.u.p.a')
      _routed_cat — category_code ('sewage')
      _prefix    — 2-3 літерний префікс для Excel ('KN')

    Пріоритети:
      1. TREE — від специфічніших до загальних (перший збіг перемагає)
      2. Gemini category як fallback
      3. 'other'
    """
    combined = (
        (пос.get('normalized') or '') + ' ' +
        (пос.get('original')   or '')
    ).lower()

    node_id = _match_tree(combined)

    if node_id:
        cat = _node_category(node_id)
        пос['_node_id']    = node_id
        пос['_routed_cat'] = cat
        пос['_prefix']     = CAT_PREFIX.get(cat, 'XX')
        return cat

    # Fallback: Gemini category
    gemini_cat = (пос.get('category') or '').lower().strip()
    if gemini_cat and gemini_cat != 'other' and gemini_cat in CAT_PREFIX:
        root = CAT_NODE.get(gemini_cat, gemini_cat)
        пос['_node_id']    = root
        пос['_routed_cat'] = gemini_cat
        пос['_prefix']     = CAT_PREFIX.get(gemini_cat, 'XX')
        return gemini_cat

    пос['_node_id']    = 'xx'
    пос['_routed_cat'] = 'other'
    пос['_prefix']     = 'XX'
    return 'other'


def filter_by_node(catalog: list[dict], node_id: str) -> list[dict]:   # повертає товари з каталогу що належать вузлу або його підвузлам
    """
    Фільтрує каталог за node_id (ієрархічно).

    'kn.u.p' → повертає всі товари kn.u.p, kn.u.p.a, kn.u.p.b, kn.u.p.c...
    Якщо node_id == 'kn' → всі товари каналізації.

    Використовує поле '_node_id' у товарі (додається при завантаженні каталогу).
    Якщо товар не має '_node_id' — фільтруємо тільки за category.
    """
    if not node_id or node_id == 'xx':
        return catalog

    cat = _node_category(node_id)
    result = []
    for it in catalog:
        if it.get('category') != cat:
            continue
        item_node = it.get('_node_id', '')
        if item_node and item_node.startswith(node_id):
            result.append(it)
        elif not item_node:
            # Товар без node_id — включаємо якщо node_id кореневий
            if '.' not in node_id:
                result.append(it)
    return result


def get_node_groups(node_id: str) -> list[str]:     # повертає group-ключові слова для sub-фільтрації
    """Повертає список group_keywords для вузла — для фільтрації в smart_search."""
    for nid, _cat, _pats, groups in TREE:
        if nid == node_id:
            return groups
    return []


def route_sub(пос: dict, cat: str) -> list[str] | None:    # повертає group-keywords підкатегорії або None
    """
    Зворотня сумісність з search.py.
    Повертає group_keywords для поточного _node_id позиції.
    """
    node_id = пос.get('_node_id', '')
    if not node_id or node_id == 'xx':
        return None
    groups = get_node_groups(node_id)
    return groups if groups else None


def get_prefix(category_code: str) -> str:  # повертає 2-3 літерний префікс для Excel
    return CAT_PREFIX.get(category_code, 'XX')


def label(пос: dict) -> str:    # повертає рядок "[KN.U.P] Труба канал..." для логів
    """Формує читабельний лейбл з node_id + normalized для Excel/логів."""
    node = пос.get('_node_id') or CAT_NODE.get(пос.get('_routed_cat', ''), 'xx')
    return f"[{node.upper()}] {пос.get('normalized', '')}"


def route_batch(позиції: list[dict]) -> list[dict]:     # мутує позиції — додає _routed_cat, _prefix, _node_id
    """Додає _routed_cat, _prefix і _node_id до кожної позиції списку."""
    for пос in позиції:
        route(пос)   # route() вже мутує пос in-place
    return позиції


# ─── Внутрішні ───────────────────────────────────────────────────────────────

def _match_tree(combined: str) -> Optional[str]:    # шукає перший збіг у TREE; повертає node_id або None
    """Перебирає TREE від специфічніших до загальних, повертає перший node_id."""
    # Якщо є чіткі маркери PPR або металопластик — пропускаємо sewage вузли
    _is_ppr   = bool(re.search(r'\b(ppr|ппр|fiber|pn\s*\d{2}|polipr|pp-rct)\b', combined, re.IGNORECASE))
    _is_mp    = bool(re.search(r'металопласт|м\/п\b|м\.п\.\b|\bmp\b|прес.?фіт|пресс.?фіт', combined, re.IGNORECASE))
    _is_push  = bool(re.search(r'\b(pex|пекс|push|пуш|rautitan|натяжн|гільз)\b', combined, re.IGNORECASE))

    for node_id, cat, patterns, _groups in TREE:
        # Пропускаємо sewage якщо є PPR/металопластик/push маркери
        if cat == 'sewage' and (_is_ppr or _is_mp or _is_push):
            continue
        for pat in patterns:
            if re.search(pat, combined, re.IGNORECASE):
                return node_id
    return None


def _node_category(node_id: str) -> str:    # повертає category_code для node_id
    """Повертає category_code для будь-якого node_id через TREE."""
    for nid, cat, *_ in TREE:
        if nid == node_id:
            return cat
    # Якщо точного збігу нема — шукаємо кореневий вузол
    root = node_id.split('.')[0]
    return ROOT_NODES.get(root, 'other')


# ─── Приклади адрес (для документації і тестів) ──────────────────────────────
# kn           → Вся каналізація
# kn.u         → Внутрішня каналізація
# kn.u.p       → Труби внутрішньої каналізації
# kn.u.p.a     → ASG труби (внутрішня)
# kn.u.p.b     → Ostendorf труби (внутрішня)
# kn.u.f       → Фітинги внутрішньої каналізації
# kn.e         → Зовнішня каналізація
# kn.s         → Безшумна каналізація
# pp           → Весь PPR пластик
# pp.p         → PPR труби
# pp.p.a       → ASG PPR труби
# pp.f         → PPR фітинги
# pp.f.m       → PPR муфти (МРЗ/МРВ/РН)
# pp.f.k       → PPR коліна
# pp.f.t       → PPR трійники
# ps           → Вся PUSH/PEX система
# ps.p.a       → REHAU труби PEX
# ps.f         → PUSH фітинги
# rd.t22       → Радіатори тип 22
# bl.g         → Газові котли
# pm.c         → Циркуляційні насоси
