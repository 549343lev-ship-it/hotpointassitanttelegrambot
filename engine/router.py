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

    # Внутрішня — фітинги → виробники (ПЕРЕД трубами — специфічніші)
    # Guard: виключаємо латунь/нікель/хром — це перехідники ar, не каналізація
    ('kn.u.f.a', 'sewage',  [r'(коліно|муфта|трійник|заглушка|редукц|перехід|хрестовина|манжета).*(канал|°|\bф\d{2,3}).*asg(?!.*латун|.*нікел)'
                              r'|asg.*(коліно|трійник|муфта|заглушка|редукц|перехід|манжета).*(канал|°|\bф\d{2,3})'],
                            ['ASG', 'Фитинг']),

    # ВАЖЛИВО: kn.u.f.o — має збігатися з node_mapper (не kn.u.f.b!)
    ('kn.u.f.o', 'sewage',  [r'(коліно|муфта|трійник|заглушка|редукц|перехід|хрестовина|манжета|хомут|мастило).*(канал|°|\bф\d{2,3}).*ostendorf'
                              r'|ostendorf.*(коліно|трійник|муфта|заглушка|редукц|перехід|манжета|хомут|мастило)'
                              r'|kg2000|ht\s?safe.*(коліно|трійник|муфта|заглушка|перехід|хомут|мастило)'],
                            ['OSTENDORF', 'Ostendorf', 'KG2000']),

    # Внутрішня — фітинги (без бренду)
    ('kn.u.f',   'sewage',  [r'(коліно|муфта|трійник|заглушка|хрестовина|ревізія).*(канал|каналіз)'
                              r'|канал.*(коліно|муфта|трійник|заглушка|хрестовина)'
                              r'|фітинг.*канал'
                              r'|(коліно|трійник).*\d{2,3}°'
                              r'|редукц.*канал|перехід.*канал'],
                            ['Фитинг', '*Внутренняя']),

    # Внутрішня — труби → виробники
    ('kn.u.p.a', 'sewage',  [r'труба.*asg|asg.*труба'
                              r'|htr\b.*asg|asg.*htr\b'],
                            ['*ASG', 'ASG', 'Труба серая (внутренняя)']),

    # ВАЖЛИВО: kn.u.p.o — має збігатися з node_mapper (не kn.u.p.b!)
    ('kn.u.p.o', 'sewage',  [r'труба.*ostendorf|ostendorf.*труба'
                              r'|ht\s?safe.*ostendorf|ostendorf.*ht\s?safe'
                              r'|ostendorf.*(ф\d{2,3}|труба)'],
                            ['OSTENDORF', 'Ostendorf']),

    ('kn.u.p.v', 'sewage',  [r'труба.*valrom|valrom.*труба'],
                            ['*Valrom внутренняя', 'VALROM']),

    ('kn.u.p.d', 'sewage',  [r'труба.*канал.*plm|plm.*труба.*канал'],
                            ['PLM']),

    ('kn.u.p.g', 'sewage',  [r'труба.*канал.*raftec|raftec.*труба.*канал'],
                            ['Raftec (Germany)']),

    # Внутрішня — труби (без бренду)
    ('kn.u.p',   'sewage',  [r'труба\s+канал|труба.*вн.*канал|труба.*(ф\d{2,3}).*канал'],
                            ['Труба', '*Внутренняя', 'Труба серая (внутренняя)']),

    # Внутрішня (загально)
    ('kn.u',     'sewage',  [r'внутр\w*\s+канал|канал.*внутр|ф\s*\d{2,3}.*канал'
                              r'|ф50\b|ф110\b|ф32\b|ф40\b|ревізія\b'],
                            ['*Внутренняя', 'Внутрішня', 'Труба серая (внутренняя)']),

    # Каналізація загально — ВИДАЛЕНО ostendorf з паттерну щоб не захоплював загальний пул
    ('kn',       'sewage',  [r'канал|каналіз|sewage|htr\b|ht\s?safe'
                              r'|ревізія|хрестовин'],
                            []),

    # ─────────────────────────────────────────────────────────────────────────
    # PPR ПЛАСТИК (pp)
    # ─────────────────────────────────────────────────────────────────────────

    # ПНТ/ПНД труба — перед PPR щоб не потрапила в pp загально
    # ВИКЛЮЧАЄМО pex/push — вони йдуть в ps
    ('pp.n',    'plastic_ppr', [r'труба\s+пнт|пнт.*труба|труба\s+пнд|пнд.*труба'
                                 r'|труба\s+pe\b(?!x)|(?<!pe)pe(?!x).*труба'
                                 r'|поліетилен.*труба|труба.*поліетилен'],
                                ['ПНТ трубы', 'ПНТ BLUE LABEL', 'ПНТ GREEN LABEL',
                                 'ПНТ PLM', 'ПНТ VALROM']),

    # Труби → виробники
    ('pp.a.p',   'plastic_ppr', [r'труба\s+(ппр|ppr).*asg|asg.*труба\s+(ппр|ppr)'],
                                ['ASG труба', 'ASG']),

    ('pp.e.p',   'plastic_ppr', [r'труба\s+(ппр|ppr).*(ekoplastik|екопластик|eco|eko)'
                                  r'|(ekoplastik|eco\s+ppr).*труба'],
                                ['ECO PPR', 'Ekoplastik', 'OVI/EVCI Pipe', 'WHITE']),

    ('pp.r.p',   'plastic_ppr', [r'труба\s+(ппр|ppr).*raftec|raftec.*труба\s+(ппр|ppr)'],
                                ['RAFTEC', 'Raftec']),

    # Труби PPR (без бренду)
    ('pp.p',     'plastic_ppr', [r'труба\s+(ппр|ppr)|труба\s+поліпропіл|(ппр|ppr).*труба'],
                                ['ASG труба', 'ECO PPR', 'OVI/EVCI Pipe', 'WHITE']),

    # Фітинги → тип
    ('pp.f',   'plastic_ppr', [r'муфта\s+(ппр|ppr)|муфта.*(мрз|мрв|рн\b|рз\b|рв\b)'
                                  r'|(мрз|мрв|рн|рз|рв).*муфта'],
                                ['ASG фитинг', 'Фитинг PPR']),

    # Коліно PPR — НЕ настінне push коліно
    ('pp.f',   'plastic_ppr', [r'коліно\s+(ппр|ppr)'
                                  r'|коліно.*(рв|рз|рн|вз|нг).*(ппр|ppr)'
                                  r'|(ппр|ppr).*коліно.*(рв|рз|рн|вз|нг)'
                                  r'|настінн\w+\s+коліно.*(ппр|ppr)'],
                                ['ASG фитинг', 'Фитинг PPR']),

    ('pp.f.t',   'plastic_ppr', [r'трійник\s+(ппр|ppr)|(ппр|ppr).*трійник'],
                                ['ASG фитинг', 'Фитинг PPR']),

    ('pp.f',   'plastic_ppr', [r'заглушка\s+(ппр|ppr)|(ппр|ppr).*заглушка'],
                                ['ASG фитинг', 'Фитинг PPR']),

    # Фітинги PPR (загально)
    ('pp.f',     'plastic_ppr', [r'(муфта|коліно|трійник|заглушка|перехід|американка)\s+(ппр|ppr)'
                                  r'|(ппр|ppr).*(муфта|коліно|трійник|заглушка|фітинг)'],
                                ['ASG фитинг', 'Фитинг PPR']),

    # PPR загально — НЕ натяжні (push), НЕ латунні коліна ВВ
    ('pp',       'plastic_ppr', [r'ппр|ppr|поліпропіл|ekoplastik|екопластик'
                                  r'|evo\b|fiber\b|faser\b|stabi'],
                                []),

    # ─────────────────────────────────────────────────────────────────────────
    # PUSH / PEX (ps)
    # ─────────────────────────────────────────────────────────────────────────

    # Коліно настінне (монтажне) — PUSH фітинг з різьбою до стіни — перед трубами!
    ('ps.g.f',  'push_systems', [r'коліно\s+настінн|настінн\w*\s+коліно'
                                  r'|монтажн\w*\s+коліно|коліно\s+монтажн'
                                  r'|водорозетка|water.?box'],
                                ['General Fittings', 'RAFTEC']),

    # Труби PEX → виробники
    ('ps.e.r',  'push_systems', [r'труба.*rautitan|труба.*raubasic|rautitan.*труба|rehau.*труба.*pex'],
                                ['REHAU', 'Rautitan', 'Raubasic']),

    ('ps.e',  'push_systems', [r'труба.*aquapex|aquapex.*труба'],
                                ['Aquapex', 'AQUAPEX']),

    ('ps.h.g',  'push_systems', [r'труба.*heat.?pex|heat.?pex.*труба'],
                                ['HEAT-PEX']),

    # RAFTEC труби PEX-A — широкий паттерн (silver/evoh/pex-a без "push")
    ('ps.r.p',  'push_systems', [r'труба.*raftec|raftec.*труба'
                                  r'|труба.*(pex.?a|silver|evoh)'
                                  r'|(pex.?a|silver|evoh).*труба'],
                                ['RAFTEC', 'RAFTEC SILVER', 'RPXA']),

    # Труби PEX загально
    ('ps.e',    'push_systems', [r'труба.*(pex|пекс|push|пуш)|pex.*труба'],
                                ['Труба']),

    # PPSU фітинги RAFTEC — окремо від звичайних PUSH (містять ppsu в назві)
    ('ps.r.s',  'push_systems', [r'ppsu.*(муфта|трійник|фітинг|коліно|натяжн)'
                                  r'|(муфта|трійник|фітинг|коліно).*ppsu'
                                  r'|raftec.*ppsu|ppsu.*raftec'],
                                ['RAFTEC PPSU PUSH']),

    # Натяжні МРЗ/МРВ PUSH RAFTEC (не PPR!) — перед загальним ps.r.g
    ('ps.r.g',  'push_systems', [r'(муфта|коліно|трійник).*(натяжн|push|пуш).*(мрз|мрв|рв|рз|різьб)'
                                  r'|(натяжн|push|пуш).*(муфта|коліно|трійник).*(мрз|мрв|рв|рз)'
                                  r'|(мрз|мрв).*(натяжн|push|пуш)'
                                  r'|(муфта|коліно|трійник).*raftec'
                                  r'|raftec.*(муфта|коліно|трійник|фітинг|натяжн)'],
                                ['RAFTEC PUSH', 'RAFTEC']),

    # Фітинги PUSH → виробники
    ('ps.e',  'push_systems', [r'(муфта|коліно|трійник|фітинг).*(rehau|general.fitting)'
                                  r'|(rehau|general.?fitting).*(муфта|коліно|трійник|фітинг)'],
                                ['REHAU', 'General Fittings']),

    ('ps.k',  'push_systems', [r'(муфта|коліно|трійник|фітинг).*kan\b|(kan).*(муфта|коліно|трійник)'],
                                ['KAN', 'KAN-Therm PUSH']),

    ('ps.f.g',  'push_systems', [r'(муфта|коліно|трійник|фітинг).*fado'
                                  r'|fado.*(муфта|коліно|трійник|фітинг|натяжн)'],
                                ['FADO']),

    # Гільзи / кільця
    ('ps.g',    'push_systems', [r'гільза.*(push|pex)|кільце.*(push|pex)|(push|pex).*(гільза|кільце)'
                                  r'|гільза натяжна|гільза ф\s*\d{2}'],
                                ['RAFTEC', 'REHAU']),

    # PUSH загально (фітинги без виробника + загальне)
    ('ps',      'push_systems', [r'push|пуш|pex|пекс|натяжн|гільза|євроконус|rautitan|raubasic'
                                  r'|aquapex|heat.?pex|general.fitting'
                                  r'|(муфта|коліно|трійник|фітинг).*(push|пуш|pex|пекс)'
                                  r'|(push|пуш).*(муфта|коліно|трійник|фітинг)'],
                                []),

    # ─────────────────────────────────────────────────────────────────────────
    # МЕТАЛОПЛАСТИК (mp)
    # ─────────────────────────────────────────────────────────────────────────

    ('mp.f.a',  'metal_plastic', [r'(прес.?фіт|пресс.?фіт|муфта|коліно|трійник).*fado|fado.*(фіт|муфта|коліно)'],
                                 ['FADO', 'М/П всё']),

    ('mp.f.r',  'metal_plastic', [r'(прес.?фіт|муфта|коліно|трійник).*raftec|raftec.*(фіт|муфта|коліно|прес)'],
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

    # Зворотний клапан — окремо від кульових кранів
    ('sv.z',    'shutoff_valves', [r'зворотн\w*\s*клапан|клапан\s*зворотн\w*'
                                   r'|обратн\w*\s*клапан|клапан\s*обратн\w*'],
                                  ['зворот', 'Зворот']),

    # Фільтр грубої очистки — sv.fg (не sv.f — такого вузла немає)
    ('sv.fg',   'shutoff_valves', [r'фільтр.?(груб|сітч|y.тип)|грубої.?очистки|фільтр.*латун'],
                                  ['Фільтр', 'сетчат']),

    ('sv.b',    'shutoff_valves', [r'батерфляй|засувк|затвор.?диск'],
                                  ['батерфляй']),

    ('sv',      'shutoff_valves', [r'кран\s+(кульов|вв|вз|зв|dn|1_2|3_4)|засувк|затвор'
                                    r'|кран.?кульов|кульовий.?кран|запірн|вентиль'],
                                  []),

    # ─────────────────────────────────────────────────────────────────────────
    # ПЕРЕХІДНИКИ / АДАПТЕРИ (ar)
    # ─────────────────────────────────────────────────────────────────────────

    # ── ПРЕС-СИСТЕМИ: нержавійка / сталь / мідь ──────────────────────────────
    # Порядок у списку тут НЕ несучий: від перехоплення каналізацією
    # («коліно ... 90°») захищає прапорець _is_inox у _match_tree,
    # так само як _is_ppr / _is_mp / _is_push захищають свої системи.
    # Усі патерни вимагають ДВІ ознаки (прес + матеріал) — самого «нержав»
    # замало, бо нержавіючими бувають підводки, мийки, гофри.

    # KAN-Therm Steel — СПЕЦИФІЧНІШЕ за ASG INOX, тому вище.
    # Ловимо KAN у будь-якому місці рядка разом з прес/inox/steel маркером.
    ('ar.s.k',  'adapters_reducers',
                # ТІЛЬКИ steel/inox. Без них «Press KAN» — це KAN-Therm Press
                # (металопластик) або KAN PUSH, а не нержавіюча сталь.
                [r'kan.?therm\s*steel|steel\s*kan',
                 r'(?<![а-яa-z])kan(?![а-яa-z-]).{0,20}(inox|інокс)',
                 r'(inox|інокс).{0,20}(?<![а-яa-z])kan(?![а-яa-z-])',
                 r'press\s*steel.{0,20}(?<![а-яa-z])kan(?![а-яa-z-])',
                 r'(?<![а-яa-z])kan(?![а-яa-z-]).{0,20}press\s*steel'],
                ['KAN-Therm Steel (под заказ)']),

    # ASG INOX — основна нержавіюча прес-система (397 товарів)
    ('ar.s.a',  'adapters_reducers',
                [r'press\s*steel',
                 r'asg\s*inox|inox\s*asg',
                 r'(прес|press|пресс).{0,14}(інокс|inox|нержав)',
                 r'(інокс|inox|нержав\w*).{0,14}(прес|press|пресс)'],
                ['ASG INOX', 'Фітинг', 'Труба']),

    # INOXPRES / KAN-Therm Steel — інші нержавіючі прес-системи
    ('ar.s.n',  'adapters_reducers',
                [r'inoxpres|іноксprес'],
                ['Нерж', 'Пресс фитинги из нержавеющей стали']),

    # Мідь під прес — окрема система, не плутати з нержавійкою
    # Ціль ar.m (батьківський), а не ar.m.f — мідні перехідники живуть
    # в ar.m.p, фітинги в ar.m.f. Батько покриває обидва.
    ('ar.m',    'adapters_reducers',
                [r'(мідн\w*|мідь|copper).{0,14}(прес|press)',
                 r'(прес|press).{0,14}(мідн\w*|мідь|copper)',
                 r'sanha|aespres|uniko'],
                ['Фитинг (медь)', 'Медь']),

    # Сталь під приварку / з різьбою — НЕ прес
    ('ar.s.p',  'adapters_reducers',
                [r'(труба|згін|ніпель|муфта|коліно)\s+сталев\w*\s+(під\s+)?привар',
                 r'сталь.{0,10}під\s+привар|під\s+привар.{0,10}сталь'],
                ['Стальные под приварку и с резьбой']),

    ('ar.n',    'adapters_reducers', [r'ніпел.+радіатор|радіаторн.+ніпел'],
                                     ['НІПЕЛЬ РАДІАТОРНИЙ']),

    # Нікель — ar.n
    ('ar.n',    'adapters_reducers', [r'нікел.*(подовж|ніпел|перех|кут|коліно|футорк|згін|різьб)'
                                       r'|(подовж|ніпел|перех|кут|коліно).*нікел'],
                                     ['Никель', 'ASG', 'RAFTEC никель']),

    # Хром RAFTEC — ar.c.r (подовжувач 1/2 → RAFTEC хром)
    ('ar.c.r',  'adapters_reducers', [r'raftec.*(хром|подовж|ніпел|перех|кут|згін)'
                                       r'|хром.*raftec'
                                       r'|(подовж|ніпел|перех|кут|згін).*raftec.*хром'
                                       r'|(подовж|ніпел|перех|кут|згін).*хром.*raftec'],
                                     ['RAFTEC хром']),

    # Хром Pattaroni — ar.c.p (подовжувач 3/4)
    ('ar.c.p',  'adapters_reducers', [r'pattaroni|паттароні'],
                                     ['Pattaroni', 'PATTARONI']),

    # Хром SOLOMON — ar.c.s
    ('ar.c.s',  'adapters_reducers', [r'solomon.*хром|хром.*solomon|sd.*хром'],
                                     ['SOLOMON', 'SD']),

    # Хром загальний — ar.c
    ('ar.c',    'adapters_reducers', [r'хром.*(подовж|ніпел|перех|кут|коліно|футорк|згін)'
                                       r'|(подовж|ніпел|перех|кут|коліно).*хром'
                                       r'|подовжувач.*(зз|зв|вз|вв).*(1_2|1\/2|dn15|3_4|dn20)'
                                       r'|подовжувач.*dn\d{2}'],
                                     ['Хром', 'RAFTEC хром', 'HLV хром']),

    # Жовта латунь — ar.y (RAFTEC GOLD, LEXLINE, УЗКМ жовта)
    ('ar.y.r',  'adapters_reducers', [r'raftec.?gold.*(кут|коліно|угол|футорк|американка|розбірне|ніпел|подовж)'
                                       r'|(кут|коліно|угол|футорк|американка|розбірне).*raftec.?gold'],
                                     ['RAFTEC GOLD']),

    ('ar.y.l',  'adapters_reducers', [r'lexline.*(кут|коліно|подовж|футорк|ніпел)'
                                       r'|(кут|коліно|подовж|футорк|ніпел).*lexline'],
                                     ['LEXLINE желтая', 'LEXLINE']),

    # Жовта латунь загально
    ('ar.y',    'adapters_reducers', [r'жовт.?латун|raftec.?gold|lexline'
                                       r'|коліно.*(латун|вв|вз|зз|зв).*(dn|1\/2|3\/4)'
                                       r'|кут.*(латун|вв|вз).*(dn|1\/2|3\/4)'
                                       r'|футорк.*латун|згін.*латун|американка.*латун'
                                       r'|розбірне.*з.єднання'],
                                     ['LEXLINE желтая', 'RAFTEC GOLD', 'УЗКМ жовта']),

    ('ar',      'adapters_reducers', [r'перехідн|редукц|футорк|ніпел|штуцер|подовжувач'
                                       r'|бочон|напівзгін|згін\b|gebo|жебо|затискн.?муфт'
                                       r'|компресійн.?з.єднан'
                                       r'|коліно.*(латун|вв|вз|зз).*(dn\d|1\/2|3\/4)'
                                       r'|кут.*(латун|вв|вз|зз)'],
                                     []),

    # ─────────────────────────────────────────────────────────────────────────
    # РАДІАТОРИ (rd)
    # ─────────────────────────────────────────────────────────────────────────

    ('rd.s',  'radiators_radiatorsvalve', [r'тип\s*10\b|тип10'],   ['тип 10', 'тип10']),
    ('rd.s.11',  'radiators_radiatorsvalve', [r'тип\s*11\b|тип11'],   ['тип 11', 'тип11']),
    ('rd.s',  'radiators_radiatorsvalve', [r'тип\s*21\b|тип21'],   ['21', 'тип 21']),
    ('rd.s.22',  'radiators_radiatorsvalve', [r'тип\s*22\b|22\s*тип'], ['22 тип', 'тип 22']),
    ('rd.s',   'radiators_radiatorsvalve', [r'\bvk\b|нижн.*підключ'], ['VK', 'vk']),
    ('rd.bi',   'radiators_radiatorsvalve', [r'біметал|bi.vulcan|алюмін.*радіат'],
                                            ['біметал', 'алюміній']),
    ('rd.v',   'radiators_radiatorsvalve', [r'термоголовк|термостат.*радіат|термокомплект'],
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
    ('pm.g',    'pumps', [r'гідроакумул|бак.*мембран|мембран.*бак'],
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

    ('ht.v.h',    'heating', [r'herz.*клапан|клапан.*herz|термозмішув'],    ['Herz', 'HERZ']),
    ('ht.c',    'heating', [r'колектор\s+(опален|1\')|колектор.*opalen'],  ['Коллектора - теплый пол']),

    ('ht',      'heating', [r'опален|радіатор.*(підключ|кутов|прям)'
                             r'|колектор\s+(опален|1\'|ht)'], []),

    # ─────────────────────────────────────────────────────────────────────────
    # АВТОМАТИКА (at)
    # ─────────────────────────────────────────────────────────────────────────

    ('at.ht',    'automation', [r'насосн.?груп|змішувальн.?вузол|гідравліч.?стрілк'],
                              ['Насосні групи', 'Змішувальні вузли']),

    ('at.pg',    'automation', [r'погодн.?регул|регул.*sur03|lsg.?16|pcnr'],
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
    ('sf',      'siphons_fittings',  [r'сифон|злив.?арматур|арматур.?унітаз|заповнюв.?клапан|трап\b'], []),
    ('sw',      'sanitary_ware',     [r'унітаз|раковин|умивальник\s+(кераміч|фаянс)'
                                       r'|інсталяц|душов.*(піддон|кабін)|ванн.*(акрил|чавун)'], []),
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

def _refine_ppr_node(combined: str, base_node: str) -> str:     # уточнює вузол PPR фітингів по виробнику (pp.f.k → pp.e.f якщо ekoplastik)
    """
    Якщо в тексті є виробник PPR → повертає вузол виробника + тип фітинга.
    pp.f.k (коліно) + ekoplastik → pp.e.f
    pp.f.m (муфта) + asg → pp.a.f
    pp.f.t (трійник) + raftec → pp.r.f
    Без виробника → pp.f (загальний)
    """
    # Маппінг виробника → кореневий вузол
    BRAND_NODES = {
        r'ekoplastik|екопластик|pp-rct.*ekoplast': 'pp.e.f',
        r'\basg\b':                                  'pp.a.f',
        r'\braftec\b':                               'pp.r.f',
        r'\bplm\b':                                  'pp.p.f',
        r'\bkan\b':                                  'pp.k.f',
        r'fv\s*plast|fv\s+plast':                    'pp.f.f',
        r'\beco\s+ppr\b|\beco\b.*ppr':               'pp.c.f',
    }
    for pat, brand_node in BRAND_NODES.items():
        if re.search(pat, combined, re.IGNORECASE):
            return brand_node
    # Без виробника — загальний вузол фітингів
    return 'pp.f'


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

        # Уточнення node_id по виробнику для PPR фітингів
        # pp.f.k/pp.f.m/pp.f.t + виробник → pp.e.f / pp.a.f / pp.r.f тощо
        if node_id.startswith('pp.f') and cat == 'plastic_ppr':
            node_id = _refine_ppr_node(combined, node_id)

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
    # \b не працює з кирилицею — використовуємо (?<!\w) або просто без \b для кирилічних слів
    _is_push  = bool(re.search(
        r'\b(pex|push|rautitan|гільз)\b'
        r'|(?<![а-яА-ЯіІїЇєЄ])(пекс|пуш|натяжн)',
        combined, re.IGNORECASE))
    # Прес-нержавійка / прес-мідь: «коліно прес інокс 90°» не має потрапляти
    # в каналізацію через правило kn.u.f «(коліно|трійник).*\d{2,3}°».
    # Вимагаємо ДВІ ознаки — прес + матеріал, інакше false positive
    # на нержавіючі підводки, мийки, гофри.
    _is_inox  = bool(re.search(
        r'press\s*steel|asg\s*inox|inoxpres'
        r'|(прес|press|пресс)\w*.{0,14}(інокс|inox|нержав)'
        r'|(інокс|inox|нержав)\w*.{0,14}(прес|press|пресс)'
        r'|(прес|press)\w*.{0,14}(мідн|мідь|copper)'
        r'|(мідн|мідь|copper)\w*.{0,14}(прес|press)',
        combined, re.IGNORECASE))

    for node_id, cat, patterns, _groups in TREE:
        # Пропускаємо sewage якщо є PPR/металопластик/push маркери
        if cat == 'sewage' and (_is_ppr or _is_mp or _is_push):
            continue
        # Прес-нержавійка / прес-мідь живуть ТІЛЬКИ в adapters_reducers.
        # Тому не порядок правил, а цей прапорець вирішує: якщо маркер є —
        # усі інші категорії пропускаються, і правило ar спрацює хоч де в TREE.
        # Так само працюють _is_ppr / _is_mp / _is_push вище.
        if _is_inox and cat != 'adapters_reducers':
            continue
        # Пропускаємо plastic_ppr якщо є чіткі push маркери
        # (натяжні муфти/коліна МРЗ/МРВ = PUSH, не PPR)
        if cat == 'plastic_ppr' and _is_push and not _is_ppr:
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
