"""
node_mapper.py — Маппінг (category, group, subgroup) → node_id

Логіка: кожен товар з xlsx має group і subgroup.
Ця таблиця перетворює їх на node_id з дерева TREE_DESIGN_V2.

Використання в catalog.py:
    from engine.node_mapper import assign_node_id
    item['_node_id'] = assign_node_id(item['category'], item['group'], item['subgroup'])

Якщо збіг не знайдено — повертає кореневий вузол категорії (наприклад 'kn').
"""

import re

# ─── Кореневі вузли (category → root node_id) ────────────────────────────────

CAT_ROOT: dict[str, str] = {
    'plastic_ppr':             'pp',
    'sewage':                  'kn',
    'push_systems':            'ps',
    'shutoff_valves':          'sv',
    'adapters_reducers':       'ar',
    'heating':                 'ht',
    'boilers':                 'bl',
    'water_heaters':           'wh',
    'pumps':                   'pm',
    'radiators_radiatorsvalve':'rd',
    'metal_plastic':           'mp',
    'filtration':              'fl',
    'insulation':              'ins',
    'underfloor_heating':      'uf',
    'water_meters':            'wm',
    'mixers_faucets':          'mx',
    'towel_warmers':           'tw',
    'fasteners_sealants':      'fs',
    'sanitary_ware':           'sw',
    'siphons_fittings':        'sf',
    'hoses':                   'hs',
    'automation':              'at',
    'safety_valves':           'sav',
}


# ─── Таблиця маппінгу: (category, group_pattern, subgroup_pattern) → node_id ─
# group_pattern і subgroup_pattern — це рядки або regex (починаються з 'r:')
# '' означає "будь-який" (not checked)
# Порядок важливий: специфічніші правила — вище

_RULES: list[tuple] = [

    # ══════════════════════════════════════════════════════════════════════════
    # КАНАЛІЗАЦІЯ (sewage) kn
    # Ієрархія: ТИП → ПІДТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════

    # Безшумна — найвищий пріоритет (є в різних groups)
    ('sewage', r'*ASG безшумна',             '',              'kn.s.a'),
    ('sewage', r'ASG безшумна',              '',              'kn.s.a'),
    ('sewage', 'Фитинг',                     '*ASG безшумна', 'kn.s.a'),
    ('sewage', 'OSTENDORF бесшумная',        '',              'kn.s.b'),
    ('sewage', 'Ostendorf Безшумний',        '',              'kn.s.b'),
    ('sewage', 'Valrom бесшумная',           '',              'kn.s.c'),
    ('sewage', 'Безшумний',                  '*VALROM',       'kn.s.c'),
    ('sewage', 'Безшумний',                  '',              'kn.s.c'),  # загальна безшумна
    ('sewage', 'ПОД ЗАКАЗ',                  'REHAU RAUPIANO Plus бесшумная', 'kn.s.d'),
    ('sewage', 'под заказ',                  'raupiano plus', 'kn.s.d'),

    # Зовнішня каналізація
    ('sewage', '*Наружная',                  'Труба',         'kn.e.p'),
    ('sewage', '*Наружная',                  'ASG',           'kn.e.p.a'),
    ('sewage', '*Наружная',                  '',              'kn.e'),
    ('sewage', 'EVCI PLASTIK/OVI PLAST/XV Plast', '',        'kn.e.f.e'),
    ('sewage', 'EVCI PLASTIK (TURKEY)',      '',              'kn.e.f.e'),
    ('sewage', r'Ostendorf$',               '',              'kn.e.f.o'),   # Ostendorf без "бесшумная"
    ('sewage', r'PLM$',                     '',              'kn.e.f.p'),
    ('sewage', r'VALROM$',                  '',              'kn.e.f.v'),

    # Внутрішня — труби
    ('sewage', 'КАНАЛІЗАЦІЯ',               '* OSTENDORF внутренняя', 'kn.u.p.o'),
    ('sewage', 'КАНАЛІЗАЦІЯ',               '*Внутренняя',   'kn.u.p'),
    ('sewage', 'КАНАЛІЗАЦІЯ',               'Труба',         'kn.u.p'),
    ('sewage', '*ASG',                       '',              'kn.u.p.a'),  # G: *ASG (труби)
    ('sewage', '*Valrom внутренняя',         '',              'kn.u.p.v'),
    ('sewage', 'Труба серая (внутренняя)',   '',              'kn.u.p.g'),
    ('sewage', 'ABS',                        '',              'kn.u.p.g'),  # ABS труби
    ('sewage', 'PLM',                        '',              'kn.u.f.p'),  # PLM фітинги (нижче є PLM труба)
    ('sewage', 'Raftec (Germany)',           '',              'kn.u.p.d'),  # Raftec труби
    ('sewage', 'XV Plast/MTB',              '',              'kn.u.p.g'),

    # Внутрішня — фітинги
    ('sewage', 'Фитинг',                    'ASG',           'kn.u.f.a'),
    ('sewage', 'Фитинг',                    '',              'kn.u.f'),    # фітинги загально
    ('sewage', 'ASG',                       'Фитинг',        'kn.u.f.a'),  # ASG фітинги
    ('sewage', 'ASG',                       '',              'kn'),         # ASG загальний
    ('sewage', 'OSTENDORF',                 '',              'kn.u.f.o'),
    ('sewage', 'VALROM',                    '',              'kn.u.f.v'),
    ('sewage', 'ИНСТАЛПЛАСТ',              '',              'kn.u.f.i'),
    ('sewage', 'НЕСТАНДАРТ',               '',              'kn.u.f.x'),
    ('sewage', 'Прочее',                    '',              'kn.u.f.x'),
    ('sewage', 'под заказ',                 '',              'kn.u.f.x'),

    # Інше (ємності, лотки)
    ('sewage', 'Гофра в бухтах',            '',              'kn.o.g'),
    ('sewage', r'Ёмкост',                   '',              'kn.o.t'),
    ('sewage', 'Сепараторы и дождеприемники','',             'kn.o.t'),
    ('sewage', 'ERA ПВХ',                   '',              'kn.o.e'),
    ('sewage', 'Канализация ПРОЧЕЕ',        '',              'kn.o.c'),
    ('sewage', 'Набор подставок',           '',              'kn.o'),
    ('sewage', 'Фитинг для соединения',     '',              'kn.o'),
    ('sewage', r'ЛЮК',                      '',              'kn.o.t'),
    ('sewage', 'КАНАЛІЗАЦІЯ',               '',              'kn.u'),      # fallback внутрішня

    # ══════════════════════════════════════════════════════════════════════════
    # PPR ПЛАСТИК (plastic_ppr) pp
    # Ієрархія: ВИРОБНИК → ТИП ТОВАРУ
    # ══════════════════════════════════════════════════════════════════════════

    # ASG
    ('plastic_ppr', 'ПЛАСТИК',              'ASG труба',     'pp.a.p'),
    ('plastic_ppr', 'ПЛАСТИК',              'ASG',           'pp.a.f'),  # SG ASG = зразки
    ('plastic_ppr', 'ASG фитинг',           '',              'pp.a.f'),
    ('plastic_ppr', 'WHITE',                '',              'pp.a.w'),

    # Ekoplastik
    ('plastic_ppr', 'EKOPLASTIK',           'Ekoplastik труба', 'pp.e.p'),
    ('plastic_ppr', 'Ekoplastik фитинг',    '',              'pp.e.f'),

    # OVI / EVCI
    ('plastic_ppr', 'OVI/EVCI Pipe',        '',              'pp.o.p'),
    ('plastic_ppr', 'OVI PREMIUM',          'OVI PREMIUM PIPE', 'pp.o.x'),
    ('plastic_ppr', 'OVI PREMIUM',          '',              'pp.o.x'),
    ('plastic_ppr', 'EVCI',                 '',              'pp.o.p'),

    # FV PLAST
    ('plastic_ppr', 'FV PLAST',             'FV PLAST Труба','pp.f.p'),
    ('plastic_ppr', 'FV PLAST',             '',              'pp.f.f'),
    ('plastic_ppr', 'FV PLAST Фитинг',     '',              'pp.f.f'),
    ('plastic_ppr', 'FV труба',             '',              'pp.f.t'),
    ('plastic_ppr', 'FV Фитинг',            '',              'pp.f.t'),

    # KAN
    ('plastic_ppr', 'KAN',                  'Труба KAN',     'pp.k.p'),
    ('plastic_ppr', 'Фитинг KAN',           '',              'pp.k.f'),

    # PLM
    ('plastic_ppr', 'PLM',                  'PLM Труба',     'pp.p.p'),
    ('plastic_ppr', 'PLM Фитинг',           '',              'pp.p.f'),

    # RAFTEC PPR
    ('plastic_ppr', 'RAFTEC',               'RAFTEC Труба',  'pp.r.p'),
    ('plastic_ppr', 'RAFTEC Фитинг',        '',              'pp.r.f'),
    ('plastic_ppr', 'RAFTEC',               '',              'pp.r'),   # загальна RAFTEC група
    # Індивідуальні raftec группи (коліна, муфти як окремі G)
    ('plastic_ppr', r'Коліно PPR',          '',              'pp.r.f'),
    ('plastic_ppr', r'Муфта PPR',           '',              'pp.r.f'),
    ('plastic_ppr', r'Трійник.*PPR',        '',              'pp.r.f'),
    ('plastic_ppr', r'Кран.*PPR',           '',              'pp.r.f'),

    # ECO PPR (загальна серія)
    ('plastic_ppr', 'ECO PPR',              '',              'pp.c.f'),
    ('plastic_ppr', 'Фитинг PPR',           '',              'pp.c.f'),
    ('plastic_ppr', r'Американка PPR',      '',              'pp.c.f'),

    # Інші фітинги / аксесуари
    ('plastic_ppr', 'ПРОЧЕЕ',               'Blue Ocean',    'pp.b'),
    ('plastic_ppr', 'ПРОЧЕЕ',               'BLUE OCEAN 2 (фитинг)', 'pp.b'),
    ('plastic_ppr', 'Разное',               '',              'pp.b'),
    ('plastic_ppr', 'Образцы',              '',              'pp.b'),
    ('plastic_ppr', 'Аксесcуары',           '',              'pp.x'),
    ('plastic_ppr', r'Насадка',             '',              'pp.x'),
    ('plastic_ppr', r'Паяльник',            '',              'pp.x'),

    # ПНТ/ПНД
    ('plastic_ppr', 'ПНТ АКВА (Технічна)',  '',              'pp.n.a'),
    ('plastic_ppr', r'Труба ПНТ ф',         '',              'pp.n.a'),
    ('plastic_ppr', 'ПНТ BLUE LABEL (Питна)','',            'pp.n.b'),
    ('plastic_ppr', 'ПНТ GREEN LABEL (Питна)','',           'pp.n.g'),
    ('plastic_ppr', 'ПНТ PLM (Питна)',       '',             'pp.n.p'),
    ('plastic_ppr', 'ПНТ VALROM (Питна)',    '',             'pp.n.v'),
    ('plastic_ppr', 'ПНТ фітінг',           '',              'pp.n.f'),
    ('plastic_ppr', 'Unidelta (под заказ)',  '',              'pp.n.f'),
    ('plastic_ppr', 'Стыковой фитинг',      '',              'pp.n.f'),
    ('plastic_ppr', 'Терморезисторный фитинг FOX','',        'pp.n.f'),
    ('plastic_ppr', 'Фітинг різне',         '',              'pp.n.f'),
    ('plastic_ppr', 'ПНТ AКВА',             '',              'pp.n.a'),
    ('plastic_ppr', r'ПНТ',                 '',              'pp.n'),

    # Полив
    ('plastic_ppr', 'Крапельний полив',      '',              'pp.w.k'),
    ('plastic_ppr', 'Крапельна трубка + фітинг','',          'pp.w.t'),
    ('plastic_ppr', 'Крапельна трубка + фітінг','',          'pp.w.t'),
    ('plastic_ppr', 'Шланги для полива',    '',              'pp.w.s'),
    ('plastic_ppr', 'Коннекторы к шлангам', '',              'pp.w.c'),
    ('plastic_ppr', 'Полив',                '',              'pp.w'),

    # Службова номенклатура
    ('plastic_ppr', 'Служебная номенклатура','',             'pp.b'),
    ('plastic_ppr', r'Товар в ассортименте','',              'pp.b'),

    # ══════════════════════════════════════════════════════════════════════════
    # PUSH/PEX (push_systems) ps
    # Ієрархія: ВИРОБНИК → ТИП ТОВАРУ
    # ══════════════════════════════════════════════════════════════════════════

    ('push_systems', 'СИСТЕМЫ  PUSH',       'Aquapex',       'ps.a.g'),
    ('push_systems', 'FADO',                'Натяжной фитинг','ps.f.g'),
    ('push_systems', 'FADO',                '',              'ps.f.p'),  # труба FADO
    ('push_systems', 'Труба',               'КУСКИ',         'ps.e.p'),  # REHAU куски
    ('push_systems', 'Труба',               '',              'ps.f.p'),
    ('push_systems', 'General Fittings',    '',              'ps.g.g'),
    ('push_systems', 'HEAT-PEX',            '',              'ps.h.g'),
    ('push_systems', 'KAN',                 'KAN-Therm PUSH (под заказ)', 'ps.k.p'),
    ('push_systems', 'KAN-Therm ultraLINE (под заказ)','',  'ps.k.u'),
    ('push_systems', 'KAN',                 '',              'ps.k'),
    ('push_systems', 'RAFTEC',              'RAFTEC PPSU PUSH','ps.r.s'),
    ('push_systems', 'RAFTEC PUSH',         '',              'ps.r.g'),
    ('push_systems', 'RAFTEC',              '',              'ps.r.g'),  # загальні гільзи RAFTEC
    ('push_systems', 'RAFTEC запчасти к инструменту','Механичекий инструмент','ps.r.i'),
    ('push_systems', 'RAFTEC запчасти к инструменту','',    'ps.r.i'),
    ('push_systems', 'Электрический инструмент','',          'ps.r.i'),
    ('push_systems', 'RAFTEC Інструмент PUSH','',            'ps.r.i'),
    ('push_systems', r'RAFTEC Труба',        '',              'ps.r.p'),
    ('push_systems', 'Зразки труба',        '',              'ps.r.p'),
    ('push_systems', 'REHAU',               'RAUBASIC',      'ps.e.b'),
    ('push_systems', 'REHAU',               'RAUTITAN',      'ps.e.r'),
    ('push_systems', 'RAUPEX',              'RAUTITAN',      'ps.e.r'),
    ('push_systems', 'RAUVITHERM',          '',              'ps.e.v'),
    ('push_systems', 'REHAU',               '',              'ps.e'),
    ('push_systems', 'РАUPEX',              '',              'ps.e'),
    ('push_systems', 'ІНСТРУМЕНТ',          '',              'ps.e.i'),
    ('push_systems', 'TECE',                '',              'ps.t.f'),
    ('push_systems', 'Uponor',              '',              'ps.u.f'),
    ('push_systems', 'Прочее',              'Предизолированные трубы AUSTROISOL', 'ps.z.a'),
    ('push_systems', 'Прочее',              'Труба',         'ps.z.a'),
    ('push_systems', 'Фитинг AUSTROISOL',   '',              'ps.z.a'),
    ('push_systems', r'Предізольовані трубу', '',            'ps.z.t'),
    ('push_systems', r'ЗРАЗОК! Євроконус',  '',              'ps.z.k'),
    ('push_systems', r'Коліно настінне',    '',              'ps.g.f'),
    ('push_systems', 'Прочее',              '',              'ps.z'),
    ('push_systems', 'СИСТЕМЫ  PUSH',       '',              'ps'),

    # ══════════════════════════════════════════════════════════════════════════
    # МЕТАЛОПЛАСТИК (metal_plastic) mp
    # Ієрархія: ТИП ТОВАРУ → ВИРОБНИК
    # ══════════════════════════════════════════════════════════════════════════

    ('metal_plastic', 'М/П всё',            'М/П Коллектор', 'mp.c'),
    ('metal_plastic', 'М/П всё',            'FADO',          'mp.c.f'),
    ('metal_plastic', 'HLV',                '',              'mp.c.h'),
    ('metal_plastic', 'RAFTEС',             'RAFTEC Коллектор под 35*', 'mp.c.r'),
    ('metal_plastic', 'RAFTEC Коллектор под 90*','',         'mp.c.q'),
    ('metal_plastic', 'VALTEK',             '',              'mp.c.v'),
    ('metal_plastic', 'Коллектора BUGATTI', '',              'mp.c.v'),
    ('metal_plastic', 'Прочее',             '',              'mp.c.v'),  # колектори прочее
    ('metal_plastic', 'М/П Кран шаровый',   '',              'mp.k'),
    ('metal_plastic', 'М/П Пресс-фитинг',   'Bonomi S.P.A', 'mp.p.b'),
    ('metal_plastic', 'М/П Пресс-фитинг',   '',              'mp.p'),
    ('metal_plastic', 'FADO Пресс фитинг',  '',              'mp.p.f'),
    ('metal_plastic', 'HERZ',               '',              'mp.p.h'),
    ('metal_plastic', 'HLV/ICMA пресс фитинг','',           'mp.p.i'),
    ('metal_plastic', 'KAN-Therm Press',    '',              'mp.p.k'),
    ('metal_plastic', 'Rifeng',             '',              'mp.p.r'),
    ('metal_plastic', 'TWEETOP',            '',              'mp.p.r'),
    ('metal_plastic', 'VALTEC пресс',       '',              'mp.p.r'),
    ('metal_plastic', 'Інструмент',         '',              'mp.p.r'),  # інструмент для прес
    ('metal_plastic', 'М/П Труба',          'Труба Ekoplastik','mp.t.e'),
    ('metal_plastic', 'Труба HERZ',         '',              'mp.t.h'),
    ('metal_plastic', 'Труба RAFTEC',       '',              'mp.t.r'),
    ('metal_plastic', 'Труба прочее',       '',              'mp.t.x'),
    ('metal_plastic', 'Труба ЭКО',          '',              'mp.t.x'),
    ('metal_plastic', 'М/П Труба',          '',              'mp.t'),
    ('metal_plastic', 'М/П Фитинг',         'APE',           'mp.f.a'),
    ('metal_plastic', 'М/П Фитинг',         'FADO',          'mp.f.f'),
    ('metal_plastic', 'FADO',               '',              'mp.f.f'),
    ('metal_plastic', 'Giacomini',          '',              'mp.f.g'),
    ('metal_plastic', 'GROSS',              '',              'mp.f.o'),
    ('metal_plastic', 'HERZ',               '',              'mp.f.h'),  # вже вище для прес
    ('metal_plastic', 'HLV',                '',              'mp.f.l'),
    ('metal_plastic', 'LTM',                '',              'mp.f.m'),
    ('metal_plastic', 'RAFTEC',             '',              'mp.f.r'),
    ('metal_plastic', 'VALTEC',             '',              'mp.f.v'),
    ('metal_plastic', 'Аксессуары',         '',              'mp.f.x'),
    ('metal_plastic', r'Муфта прес',        '',              'mp.p.i'),
    ('metal_plastic', r'Трійник проміжний', '',              'mp.f.x'),
    ('metal_plastic', 'М/П Фитинг',         '',              'mp.f'),

    # ══════════════════════════════════════════════════════════════════════════
    # ЗАПІРНА АРМАТУРА (shutoff_valves) sv
    # Ієрархія: ТИП КРАНУ → ВИРОБНИК → СЕРІЯ
    # ══════════════════════════════════════════════════════════════════════════

    # Американки
    ('shutoff_valves', 'ЗАПІРНА АРМАТУРА',  'ASG',           'sv.a.a'),
    ('shutoff_valves', 'ЗАПІРНА АРМАТУРА',  'Американки(сгоны)', 'sv.a'),
    ('shutoff_valves', 'FADO ( под заказ)', '',              'sv.a.f'),
    ('shutoff_valves', 'Giacomini',         '',              'sv.a.g'),
    ('shutoff_valves', 'HLV',               '',              'sv.a.h'),
    ('shutoff_valves', 'LEXLINE',           '',              'sv.a.l'),
    ('shutoff_valves', 'RAFTEC',            '',              'sv.a.r'),
    ('shutoff_valves', 'ЭКО американки',    '',              'sv.a.e'),
    ('shutoff_valves', r'Розбірне з.єднання', '',            'sv.a.e'),

    # Засувки/батерфляї
    ('shutoff_valves', 'Задвижки, батерфляй, вентиля','RAFTEC','sv.b.r'),
    ('shutoff_valves', 'Задвижки, батерфляй, вентиля','',   'sv.b'),
    ('shutoff_valves', 'Батерфляй',         '',              'sv.b.b'),
    ('shutoff_valves', 'Вентиль чугун',     '',              'sv.b.v'),
    ('shutoff_valves', 'Задвижки латунь',   '',              'sv.b.z'),
    ('shutoff_valves', 'Задвижки чугунные', '',              'sv.b.z'),
    ('shutoff_valves', 'Краны фланцевые (под заказ)','Краны Breeze (Под заказ)','sv.fl.b'),
    ('shutoff_valves', 'Краны фланцевые (под заказ)','',    'sv.fl'),
    ('shutoff_valves', 'Под заказ',         '',              'sv.fl'),

    # Зворотні клапани
    ('shutoff_valves', 'Клапана обр.хода воды','ASG',        'sv.z.a'),
    ('shutoff_valves', 'Bugatti',           '',              'sv.z.b'),
    ('shutoff_valves', 'PLM',               '',              'sv.z.p'),
    ('shutoff_valves', r'Клапана обр.хода воды','',          'sv.z'),
    ('shutoff_valves', r'Клапан пелюстков', '',              'sv.z.x'),
    ('shutoff_valves', r'Лепестковый',      '',              'sv.z.x'),
    ('shutoff_valves', r'Клапана межфланц', '',              'sv.z.x'),
    ('shutoff_valves', 'Сетка клапана',     '',              'sv.z'),
    ('shutoff_valves', 'Акционные клапана', '',              'sv.z.x'),
    ('shutoff_valves', r'ЭКО клапана',      '',              'sv.z.e'),

    # Поливальні
    ('shutoff_valves', 'Кран поливочный',   'ASG',           'sv.po.a'),
    ('shutoff_valves', 'Кран поливочный',   'Meibes',        'sv.po.m'),
    ('shutoff_valves', 'Кран поливочный',   'PLM',           'sv.po.p'),
    ('shutoff_valves', 'Кран поливочный',   'RAFTEC',        'sv.po.r'),
    ('shutoff_valves', 'Кран поливочный',   '',              'sv.po'),

    # Крани з американкою
    ('shutoff_valves', 'Краны с американкой','ASG',          'sv.k.a'),
    ('shutoff_valves', 'Краны с американкой','ASG (Червоний)','sv.k.a'),
    ('shutoff_valves', 'Краны с американкой','ASG + (Сірий)','sv.k.a'),
    ('shutoff_valves', 'ASG + (Сірий)',     '',              'sv.k.a'),
    ('shutoff_valves', 'Краны с американкой','ARIZONA',      'sv.k.b'),
    ('shutoff_valves', 'Краны с американкой','OREGON',       'sv.k.b'),
    ('shutoff_valves', 'Краны с американкой','Giacomini',    'sv.k.g'),
    ('shutoff_valves', 'OREGON',            '',              'sv.k.b'),
    ('shutoff_valves', 'Краны с американкой','PLM',          'sv.k.p'),
    ('shutoff_valves', 'PLM',               'STRONG',        'sv.k.p'),
    ('shutoff_valves', 'PLM',               'ВASE',          'sv.k.p'),
    ('shutoff_valves', 'Краны с американкой','RAFTEC GOLD',  'sv.k.r.g'),
    ('shutoff_valves', 'RAFTEC WHITE',       '',              'sv.k.r.w'),
    ('shutoff_valves', 'RАFTEC BLACK',       '',              'sv.k.r.b'),
    ('shutoff_valves', 'RАFTEC RED',         '',              'sv.k.r.d'),
    ('shutoff_valves', 'Краны с американкой','',             'sv.k'),
    ('shutoff_valves', 'ЭКО(PN20)',         '',              'sv.k.e'),

    # Крани з НГ
    ('shutoff_valves', 'Краны с НГ',        'ASG',           'sv.n.a'),
    ('shutoff_valves', 'Краны с НГ',        'ASG (Червоний)','sv.n.a'),
    ('shutoff_valves', 'Краны с НГ',        'ASG+ (Cірий)', 'sv.n.a'),
    ('shutoff_valves', 'Краны с НГ',        'RAFTEC BLACK',  'sv.n.r'),
    ('shutoff_valves', 'Краны с НГ',        'RAFTEC GOLD',   'sv.n.r'),
    ('shutoff_valves', 'Краны с НГ',        '',              'sv.n'),

    # Дренаж
    ('shutoff_valves', 'Краны с дренажем',  '',              'sv.d'),

    # Триходові
    ('shutoff_valves', 'Краны трехходовые', '',              'sv.3'),

    # Кульові — ВОДА
    ('shutoff_valves', 'Краны шаровые',     'ASG',           'sv.m.a'),
    ('shutoff_valves', 'Краны шаровые',     'ASG (Червоний)','sv.m.a'),
    ('shutoff_valves', 'Краны шаровые',     'ASG + (Сірий)','sv.m.a'),
    ('shutoff_valves', 'ASG + (Сірий)',      '',              'sv.m.a'),  # окрема G
    ('shutoff_valves', 'BUGATTI (Italy)',    'ARIZONA (PN40)','sv.m.b'),
    ('shutoff_valves', 'BUGATTI (Italy)',    'NEW JERSEY (PN50)','sv.m.b'),
    ('shutoff_valves', 'BUGATTI (Italy)',    'OREGON (PN64)', 'sv.m.b'),
    ('shutoff_valves', 'BUGATTI (Italy)',    '',              'sv.m.b'),
    ('shutoff_valves', 'FADO',              'CLASSIC',       'sv.m.f'),
    ('shutoff_valves', 'FADO',              'NEW',           'sv.m.f'),
    ('shutoff_valves', 'FADO',              '',              'sv.m.f'),
    ('shutoff_valves', 'GIACOMINI',         'DADO',          'sv.m.g'),
    ('shutoff_valves', 'GIACOMINI',         '',              'sv.m.g'),
    ('shutoff_valves', 'HLV (PN40)',        '',              'sv.m.h'),
    ('shutoff_valves', 'PLM',               'STRONG',        'sv.m.p'),
    ('shutoff_valves', 'PLM',               'ВASE',          'sv.m.p'),
    ('shutoff_valves', 'PLM',               '',              'sv.m.p'),
    ('shutoff_valves', 'RAFTEC (Germany)',   'RAFTEC GOLD',   'sv.m.r'),
    ('shutoff_valves', 'RAFTEC (Germany)',   'RАFTEC  BLACK', 'sv.m.r'),
    ('shutoff_valves', 'RAFTEC (Germany)',   'RАFTEC RED',    'sv.m.r'),
    ('shutoff_valves', 'RAFTEC (Germany)',   '',              'sv.m.r'),
    ('shutoff_valves', 'Акционные позиции', '',              'sv.m.x'),
    ('shutoff_valves', 'Краны шаровые',     '',              'sv.m'),

    # Кульові — ГАЗ
    ('shutoff_valves', 'Газ',               'BUGATTI',       'sv.g.b'),
    ('shutoff_valves', 'Газ',               'Giacomini',     'sv.g.g'),
    ('shutoff_valves', 'Газ',               'HLV',           'sv.g.h'),
    ('shutoff_valves', 'Газ',               'RAFTEC',        'sv.g.r'),
    ('shutoff_valves', 'Газ',               'ЭКО',           'sv.g.e'),
    ('shutoff_valves', 'Газ',               '',              'sv.g'),

    # Приладові
    ('shutoff_valves', 'Приборные краны(хромированные)','Albetroni','sv.pr.a'),
    ('shutoff_valves', 'Приборные краны(хромированные)','ASG','sv.pr.s'),
    ('shutoff_valves', 'ASG',               '',              'sv.pr.s'),
    ('shutoff_valves', 'Приборные краны(хромированные)','BUGATTI','sv.pr.b'),
    ('shutoff_valves', 'Приборные краны(хромированные)','Grohe','sv.pr.g'),
    ('shutoff_valves', 'Приборные краны(хромированные)','HLV','sv.pr.h'),
    ('shutoff_valves', 'RAFTEC',            'Краны Полотенцесушители LUX','sv.pr.r'),
    ('shutoff_valves', 'Приладові з фільтром','',            'sv.pr.r'),
    ('shutoff_valves', 'Приладові серії Silver','',          'sv.pr.r'),
    ('shutoff_valves', 'МИНИ',              'BUGATTI',       'sv.pr.m'),
    ('shutoff_valves', 'МИНИ разные',       '',              'sv.pr.m'),
    ('shutoff_valves', 'Приборные краны(хромированные)','',  'sv.pr'),

    # Самопромивні фільтри
    ('shutoff_valves', 'Самопромывные фильтра','HERZ',       'sv.sf.h'),
    ('shutoff_valves', 'Самопромывные фильтра','HLV',        'sv.sf.l'),
    ('shutoff_valves', 'Самопромывные фильтра','HONEYWELL',  'sv.sf.w'),
    ('shutoff_valves', 'Самопромывные фильтра','RAFTEC',     'sv.sf.r'),
    ('shutoff_valves', 'Самопромывные фильтра','',           'sv.sf'),

    # Фільтри грубої очистки
    ('shutoff_valves', 'Фильтра грубой очистки','ASG',       'sv.fg.a'),
    ('shutoff_valves', 'Фильтра грубой очистки','Bugatti',   'sv.fg.b'),
    ('shutoff_valves', 'Фильтра грубой очистки','HLV',       'sv.fg.h'),
    ('shutoff_valves', 'Фильтра грубой очистки','LEXLINE',   'sv.fg.l'),
    ('shutoff_valves', 'Фильтра грубой очистки','PLM',       'sv.fg.p'),
    ('shutoff_valves', 'Фильтра грубой очистки','RAFTEC',    'sv.fg.r'),
    ('shutoff_valves', 'Фильтра грубой очистки','',          'sv.fg'),
    ('shutoff_valves', 'Газовые',           '',              'sv.fg.z'),
    ('shutoff_valves', 'Прочие фильтра',    '',              'sv.fg.z'),
    ('shutoff_valves', 'Фильтра фланцевые', '',              'sv.fg.z'),
    ('shutoff_valves', 'ЭКО фильтра',       '',              'sv.fg.z'),

    # Кран з фільтром
    ('shutoff_valves', 'Кран с фильтром',   '',              'sv.fg'),
    ('shutoff_valves', 'Набор образцов General Fittings','', 'sv.m.x'),
    ('shutoff_valves', 'Прочая ЗП',         '',              'sv.m.x'),
    ('shutoff_valves', 'Прочая ЗП',         'FADO',          'sv.m.x'),
    ('shutoff_valves', 'SOLOMON',           '',              'sv.m.x'),
    ('shutoff_valves', r'Кран кульовий Roho','',             'sv.m.x'),

    # ══════════════════════════════════════════════════════════════════════════
    # ПЕРЕХІДНИКИ (adapters_reducers) ar
    # Ієрархія: МАТЕРІАЛ → ВИРОБНИК
    # ══════════════════════════════════════════════════════════════════════════

    ('adapters_reducers', 'ПЕРЕХОДНИКИ',    'LEXLINE желтая','ar.y.l'),
    ('adapters_reducers', 'LEXLINE желтая (п/з)','',         'ar.y.l'),
    ('adapters_reducers', 'RAFTEC GOLD',    '',              'ar.y.r'),
    ('adapters_reducers', 'УЗКМ жовта',     '',              'ar.y.u'),
    ('adapters_reducers', 'ПЕРЕХОДНИКИ',    'Жёлтая латунь', 'ar.y'),
    ('adapters_reducers', 'ПЕРЕХОДНИКИ',    'Prima',         'ar.y'),
    ('adapters_reducers', 'Прочее',         '',              'ar.y.x'),
    ('adapters_reducers', 'Никель',         'ASG',           'ar.n.a'),
    ('adapters_reducers', 'FADO',           '',              'ar.n.f'),
    ('adapters_reducers', 'HLV',            '',              'ar.n.h'),
    ('adapters_reducers', 'LEXLINE никель', '',              'ar.n.l'),
    ('adapters_reducers', 'LEXLINE никель (п/з)','',         'ar.n.l'),
    ('adapters_reducers', 'RAFTEC никель',  '',              'ar.n.r'),
    ('adapters_reducers', 'УЗКМ нікель',    '',              'ar.n.u'),
    ('adapters_reducers', 'Никель прочее',  '',              'ar.n.x'),
    ('adapters_reducers', 'Никель',         '',              'ar.n'),
    ('adapters_reducers', 'Хром',           'Albertoni',     'ar.c.a'),
    ('adapters_reducers', 'Хром',           'FADO',          'ar.c.f'),
    ('adapters_reducers', 'Хром',           'HLV хром',      'ar.c.h'),  # SG HLV хром
    ('adapters_reducers', 'HLV хром',       '',              'ar.c.h'),
    ('adapters_reducers', 'Хром',           'Pattaroni',     'ar.c.p'),
    ('adapters_reducers', 'RAFTEC хром',    '',              'ar.c.r'),
    ('adapters_reducers', 'Хром',           'RAFTEC хром',   'ar.c.r'),
    ('adapters_reducers', 'SD',             '',              'ar.c.s'),
    ('adapters_reducers', 'SOLOMON',        '',              'ar.c.s'),
    ('adapters_reducers', 'УЗКМ хром',      '',              'ar.c.u'),
    ('adapters_reducers', 'Хром',           '',              'ar.c'),
    ('adapters_reducers', 'Медь',           'Медь пресс фитинг','ar.m.p'),
    ('adapters_reducers', 'Труба медная',   '',              'ar.m.t'),
    ('adapters_reducers', 'Фитинг (медь)',  '',              'ar.m.f'),
    ('adapters_reducers', 'Медь',           '',              'ar.m'),
    ('adapters_reducers', 'Сталь',          'ASG INOX',      'ar.s.a'),
    ('adapters_reducers', 'Ущільнюючі кільця ASG','',        'ar.s.a'),
    ('adapters_reducers', 'Фітинг',        '',              'ar.s.a'),  # INOX прес-фітинг
    ('adapters_reducers', 'Труба',          '',              'ar.s.a'),  # INOX труба
    ('adapters_reducers', 'Raccorderie Metalliche','',        'ar.s.r'),
    ('adapters_reducers', r'KAN-Therm Steel','',             'ar.s.k'),
    ('adapters_reducers', 'Нерж',           '',              'ar.s.n'),
    ('adapters_reducers', 'Пресс фитинги из нержавеющей стали','KAN-Therm Steel (под заказ)','ar.s.k'),
    ('adapters_reducers', 'Стальные под приварку и с резьбой','Сталь фітинг ІМПОРТ','ar.s.p'),
    ('adapters_reducers', 'Сталь фітинг УКРАЇНА','',         'ar.s.p'),
    ('adapters_reducers', 'Сталь фітинг(під замовлення)','', 'ar.s.p'),
    ('adapters_reducers', 'Чернуха (сталь труба)','',        'ar.s.c'),
    ('adapters_reducers', r'Труба сталева',  '',              'ar.s.c'),
    ('adapters_reducers', 'Сталь',          '',              'ar.s'),
    ('adapters_reducers', 'Чугунные',       'GEBO ANB (Врізка в трубу)(под заказ)','ar.g.a'),
    ('adapters_reducers', 'GEBO ANB (Врізка в трубу)(под заказ)','','ar.g.a'),
    ('adapters_reducers', 'GEBO DSK (Ремонтний затискач)(под заказ)','','ar.g.d'),
    ('adapters_reducers', 'GEBO Supervario (под заказ)','',  'ar.g.s'),
    ('adapters_reducers', 'GEBO UNIFIX (Хомут) (под заказ)','GEBO UNIFIX MAXI 1','ar.g.u'),
    ('adapters_reducers', 'GEBO UNIFIX MINI','',             'ar.g.u'),
    ('adapters_reducers', 'Чугун оцинковка','',              'ar.g.o'),
    ('adapters_reducers', r'Корок Gebo',    '',              'ar.g.o'),
    ('adapters_reducers', 'Чугун черный',   '',              'ar.g.b'),
    ('adapters_reducers', 'GEBO Fittings(ПЕРЕХОДНИКИ)','',   'ar.g.o'),
    ('adapters_reducers', r'Ремонтний хомут','',             'ar.g.u'),
    ('adapters_reducers', 'Чугунные',       '',              'ar.g'),
    ('adapters_reducers', 'Под заказ',      '',              'ar.g'),
    ('adapters_reducers', 'ПЕРЕХОДНИКИ',    '',              'ar.y'),  # fallback жовта

    # ══════════════════════════════════════════════════════════════════════════
    # ТЕПЛА ПІДЛОГА (underfloor_heating) uf
    # Реальні групи з xlsx каталогу
    # ══════════════════════════════════════════════════════════════════════════

    # Колектори
    ('underfloor_heating', r'Колектор.*RAFTEC',  '',   'uf.c'),
    ('underfloor_heating', r'Коллектора RAFTEC', '',   'uf.c'),
    ('underfloor_heating', r'Коллектора.*PLM',   '',   'uf.c'),
    ('underfloor_heating', r'Колектор.*PLM',     '',   'uf.c'),
    ('underfloor_heating', r'Коллектора FADO',   '',   'uf.c'),
    ('underfloor_heating', r'Коллектора GROSS',  '',   'uf.c'),
    ('underfloor_heating', r'Коллектора ICMA',   '',   'uf.c'),
    ('underfloor_heating', r'Коллектора Luxor',  '',   'uf.c'),
    ('underfloor_heating', r'Коллектора',        '',   'uf.c'),   # fallback колектори
    ('underfloor_heating', r'KAN-Therm',         '',   'uf.c'),

    # Терморегулятори
    ('underfloor_heating', 'Danfoss',            '',   'uf.r'),
    ('underfloor_heating', 'DEVI',               '',   'uf.r'),
    ('underfloor_heating', 'PROFI THERM',        '',   'uf.r'),
    ('underfloor_heating', r'HERZ',              '',   'uf.r'),

    # Монтажні матеріали (плівка, скоби, такер)
    ('underfloor_heating', 'Плити',              'Плівка', 'uf.s'),
    ('underfloor_heating', r'Монтажна стрічка',  '',   'uf.s'),
    ('underfloor_heating', r'Демпфер',           '',   'uf.s'),

    # RAFTEC аксесуари ТП
    ('underfloor_heating', 'RAFTEC',             '',   'uf.x'),

    # Шафи колекторні
    ('underfloor_heating', r'Коллекторные шкафы','',   'uf.sh'),
    ('underfloor_heating', r'Шафа',              '',   'uf.sh'),
    ('underfloor_heating', 'Скобы',              '',   'uf.sc'),
    ('underfloor_heating', 'RAUTHERM S',         '',   'uf.w.t.re'),
    ('underfloor_heating', 'RAUVITHERM',         '',   'uf.w.t.re'),
    ('underfloor_heating', 'ICMA',               '',   'uf.w.th'),
    ('underfloor_heating', r'SF247W25',          '',   'uf.w.3w'),
    ('underfloor_heating', 'Аксессуары',         '',   'uf.w.c'),
    ('underfloor_heating', 'Теплый пол',         '',   'uf.w'),
    ('underfloor_heating', 'Мати',               '',   'uf.mat'),
    ('underfloor_heating', 'Манометры',          '',   'uf.ma'),

    # ══════════════════════════════════════════════════════════════════════════
    # ІЗОЛЯЦІЯ (insulation) ins  — ТИП → ВИРОБНИК
    # ══════════════════════════════════════════════════════════════════════════
    ('insulation', 'K-FLEX',                     '',   'ins.k'),
    ('insulation', 'Утеплювач ф',               '',   'ins.k'),
    ('insulation', 'Фольгированные цилиндры',    '',   'ins.x'),
    ('insulation', 'Теплоизол',                  '',   'ins.l.t'),
    ('insulation', 'SANFLEX',                    '',   'ins.l.s'),
    ('insulation', 'Thermaflex',                 '',   'ins.x'),
    ('insulation', 'PLM',                        '',   'ins.l.p'),
    ('insulation', '*ПОЛОТНО',                   '',   'ins.po'),
    ('insulation', 'УТЕПЛИТЕЛЬ',                 '',   'ins.s.t'),
    ('insulation', 'трубный утеплитель',         '',   'ins.x'),

    # ══════════════════════════════════════════════════════════════════════════
    # КРІПЛЕННЯ/ГЕРМЕТИКИ (fasteners_sealants) fs  — ТИП → ВИРОБНИК
    # ══════════════════════════════════════════════════════════════════════════
    ('fasteners_sealants', 'Walraven',           '',   'fs.k.w'),
    ('fasteners_sealants', 'Монтажні системи',   '',   'fs.k.x'),
    ('fasteners_sealants', 'KVADO',              '',   'fs.k.kv'),
    ('fasteners_sealants', 'хомути RAFTEC',      '',   'fs.k.r'),
    ('fasteners_sealants', 'хомути ЕCО',         '',   'fs.k.e'),
    ('fasteners_sealants', 'Хомут авто.',        '',   'fs.au'),
    ('fasteners_sealants', 'Стрічка HPX',        '',   'fs.t'),
    ('fasteners_sealants', 'UNIPAK',             '',   'fs.u.u'),
    ('fasteners_sealants', 'Ущільнювачі',        '',   'fs.u.pr'),
    ('fasteners_sealants', 'BUDMONSTER',         '',   'fs.u.b'),
    ('fasteners_sealants', 'Піна, герметики',    '',   'fs.u.g'),
    ('fasteners_sealants', 'Фум лента',          '',   'fs.u.f'),
    ('fasteners_sealants', 'Фум,клей,нитка',     '',   'fs.u.f'),
    ('fasteners_sealants', 'Паста,пакля',        '',   'fs.u.p'),
    ('fasteners_sealants', 'Силікон',            '',   'fs.u.s'),
    ('fasteners_sealants', 'PLM',                '',   'fs.k.p'),
    ('fasteners_sealants', 'Під замовлення',     '',   'fs.k.x'),
    ('fasteners_sealants', 'Прочие',             '',   'fs.k.x'),

    # ══════════════════════════════════════════════════════════════════════════
    # ЗАПІРНА АРМАТУРА (shutoff_valves) sv  — ТИП → БРЕНД → СЕРІЯ
    # ══════════════════════════════════════════════════════════════════════════
    ('shutoff_valves', 'Кран с фильтром',        '',   'sv.fg'),
    ('shutoff_valves', 'Краны фланцевые (под заказ)', '', 'sv.fl.b'),
    ('shutoff_valves', 'Задвижки чугунные',      '',   'sv.b.z'),
    ('shutoff_valves', 'Батерфляй',              '',   'sv.b.b'),
    ('shutoff_valves', 'ASG + (Сірий)',          '',   'sv.k.a'),
    ('shutoff_valves', 'ASG',                    'ASG (Червоний)', 'sv.k.a'),
    ('shutoff_valves', 'ЭКО(PN20)',              '',   'sv.k.e'),
    ('shutoff_valves', 'ЭКО',                    '',   'sv.k.e'),
    ('shutoff_valves', 'RАFTEC RED',             '',   'sv.k.r.d'),
    ('shutoff_valves', 'RАFTEC  BLACK',          '',   'sv.k.r.b'),
    ('shutoff_valves', 'STRONG',                 '',   'sv.k.p'),
    ('shutoff_valves', 'ВASE',                   '',   'sv.k.p'),
    ('shutoff_valves', 'Приладові серії Silver', '',   'sv.pr.r'),
    ('shutoff_valves', 'ASG',                    '',   'sv.pr.s'),
    ('shutoff_valves', 'RAFTEC',                 '',   'sv.a.r'),
    ('shutoff_valves', 'HLV',                    '',   'sv.a.h'),
    ('shutoff_valves', 'LEXLINE',                '',   'sv.a.l'),
    ('shutoff_valves', 'BUGATTI',                '',   'sv.z.b'),
    ('shutoff_valves', 'Кран кульовий Roho 1/2"','',   'sv.m.x'),
    ('shutoff_valves', 'Под заказ',              '',   'sv.fl'),

    # ══════════════════════════════════════════════════════════════════════════
    # ОПАЛЕННЯ (heating) ht  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('heating', 'ESBE',                          '',   'ht.v.e'),
    ('heating', 'CALEFFI',                       '',   'ht.v.c'),
    ('heating', 'HONEYWELL',                     '',   'ht.v.hw'),
    ('heating', 'Термоэлектрические приводы',    '',   'ht.v.d'),
    ('heating', 'DANFOSS',                       '',   'ht.v.d'),
    ('heating', 'Herz (все)',                    '',   'ht.h'),
    ('heating', 'AFRISO',                        '',   'ht.m'),
    ('heating', 'Измерительные приборы',         '',   'ht.m'),
    ('heating', 'Насосные группы',               '',   'ht.col.tj'),
    ('heating', 'TERMOJET',                      '',   'ht.col.tj'),
    ('heating', 'Коллектор в теплоизоляции',     '',   'ht.col'),
    ('heating', 'Коллектора',                    '',   'ht.col'),
    ('heating', 'AW',                            '',   'ht.col.g'),
    ('heating', 'OLE-PRO',                       '',   'ht.v.o'),
    ('heating', 'Теплый пол',                    '',   'ht.uf'),

    # ══════════════════════════════════════════════════════════════════════════
    # РАДІАТОРИ (radiators_radiatorsvalve) rd  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('radiators_radiatorsvalve', 'Радіатор сталевий, тип 11', '', 'rd.s.hi'),
    ('radiators_radiatorsvalve', 'Радіатор сталевий, тип 22', '', 'rd.s.hi'),
    ('radiators_radiatorsvalve', 'Радіатор сталевий, тип 33', '', 'rd.s.hi'),
    ('radiators_radiatorsvalve', '11 тип',       '',   'rd.s.hi'),
    ('radiators_radiatorsvalve', '11 тип ( п/з)','',   'rd.s.hi'),
    ('radiators_radiatorsvalve', '22 тип',       '',   'rd.s.hi'),
    ('radiators_radiatorsvalve', '33 тип',       '',   'rd.s.hi'),
    ('radiators_radiatorsvalve', '33 тип (п/з)', '',   'rd.s.hi'),
    ('radiators_radiatorsvalve', '**СТАЛЬ',      '',   'rd.s'),
    ('radiators_radiatorsvalve', 'PURMO',        '',   'rd.s.ke'),
    ('radiators_radiatorsvalve', 'KORAD',        '',   'rd.s.ke'),
    ('radiators_radiatorsvalve', 'KORADO',       '',   'rd.s.ke'),
    ('radiators_radiatorsvalve', 'IDEALE',       '',   'rd.s.x'),
    ('radiators_radiatorsvalve', 'IDMAR',        '',   'rd.s.id'),
    ('radiators_radiatorsvalve', 'MIRADO',       '',   'rd.b.mi'),
    ('radiators_radiatorsvalve', 'АЛЮМИНИЙ',     '',   'rd.al'),
    ('radiators_radiatorsvalve', 'Алюминий',     '',   'rd.al'),
    ('radiators_radiatorsvalve', 'Консоль',      '',   'rd.ac'),
    ('radiators_radiatorsvalve', 'Арматура',     '',   'rd.v'),
    ('radiators_radiatorsvalve', 'HERZ',         '',   'rd.v.h'),

    # ══════════════════════════════════════════════════════════════════════════
    # НАСОСИ (pumps) pm  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('pumps', 'WILO',                            '',   'pm.c.l'),
    ('pumps', 'Циркуляционные насосы',           '',   'pm.c'),
    ('pumps', 'Циркуляционные Lider',            '',   'pm.c.r'),
    ('pumps', 'Насос циркуляційний',             '',   'pm.c'),
    ('pumps', 'Насос підвищення тиску',          '',   'pm.pv.l'),
    ('pumps', 'Насосные станции',                '',   'pm.st'),
    ('pumps', 'Поверхностные насосы',            '',   'pm.s'),
    ('pumps', 'Насосы',                          '',   'pm.s'),
    ('pumps', 'Sprut-NPO',                       '',   'pm.s.x'),
    ('pumps', 'IMERA',                           '',   'pm.s.x'),
    ('pumps', 'Свердловинні насоси Pedrollo',    '',   'pm.sc.p'),
    ('pumps', 'Свердловинні насоси',             '',   'pm.sc'),
    ('pumps', 'Дренажные насосы',                '',   'pm.dr'),
    ('pumps', 'SOLOLIFT',                        '',   'pm.dr.kn'),
    ('pumps', 'Reflex баки',                     '',   'pm.b.op'),
    ('pumps', 'ZILMET',                          '',   'pm.h.p'),
    ('pumps', 'Комплектующие',                   '',   'pm.ac'),
    ('pumps', 'Аксесуари до насосної техніки',   '',   'pm.ac'),

    # ══════════════════════════════════════════════════════════════════════════
    # ФІЛЬТРАЦІЯ (filtration) fl  — БРЕНД → ТИП
    # ══════════════════════════════════════════════════════════════════════════
    ('filtration', 'Комплектующие для систем ECOSOFT', '', 'fl.ec.a'),
    ('filtration', 'Filtrons',                   '',   'fl.ec.f'),
    ('filtration', 'ECOSOFT',                    '',   'fl.ec.s'),
    ('filtration', 'BWT',                        '',   'fl.bw'),
    ('filtration', 'Картриджи Bio+',             '',   'fl.ec.c'),
    ('filtration', 'Картриджі для питних',       '',   'fl.ec.c'),
    ('filtration', 'Картриджі для  механичної',  '',   'fl.ef.c'),
    ('filtration', 'Магістральний фільтр',       '',   'fl.x'),
    ('filtration', 'Фільтри механічної очистки', '',   'fl.ef'),
    ('filtration', 'Kолба',                      '',   'fl.ef'),
    ('filtration', 'Голова для колба',           '',   'fl.ef'),
    ('filtration', 'Система зворотнього осмосу', '',   'fl.ec.m'),
    ('filtration', 'Organic',                    '',   'fl.or'),

    # ══════════════════════════════════════════════════════════════════════════
    # МЕТАЛОПЛАСТИК (metal_plastic) mp  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('metal_plastic', 'KAN-Therm Press',         '',   'mp.p.k'),
    ('metal_plastic', 'TWEETOP',                 '',   'mp.p.r'),
    ('metal_plastic', 'FADO Пресс фитинг',       '',   'mp.p.f'),
    ('metal_plastic', 'VALTEC пресс',            '',   'mp.p.r'),
    ('metal_plastic', 'HERZ',                    '',   'mp.p.h'),
    ('metal_plastic', 'Rifeng',                  '',   'mp.p.r'),
    ('metal_plastic', 'М/П Пресс-фитинг',        '',   'mp.p.b'),
    ('metal_plastic', 'HLV/ICMA пресс фитинг',  '',   'mp.p.i'),
    ('metal_plastic', 'FADO',                    '',   'mp.f.f'),
    ('metal_plastic', 'RAFTEC',                  '',   'mp.f.r'),
    ('metal_plastic', 'VALTEC',                  '',   'mp.f.v'),
    ('metal_plastic', 'Giacomini',               '',   'mp.f.g'),
    ('metal_plastic', 'GROSS',                   '',   'mp.f.o'),
    ('metal_plastic', 'LTM',                     '',   'mp.f.m'),
    ('metal_plastic', 'HLV',                     '',   'mp.c.h'),
    ('metal_plastic', 'Труба ЭКО',               '',   'mp.t.x'),
    ('metal_plastic', 'Труба прочее',            '',   'mp.t.x'),

    # ══════════════════════════════════════════════════════════════════════════
    # PUSH СИСТЕМИ (push_systems) ps  — БРЕНД → ТИП
    # REHAU і RAFTEC обов'язково
    # ══════════════════════════════════════════════════════════════════════════
    ('push_systems', 'RAUTITAN',                 '',   'ps.e.r'),
    ('push_systems', 'RAUBASIC',                 '',   'ps.e.b'),
    ('push_systems', 'RAUTHERM S',               '',   'ps.e.p'),
    ('push_systems', 'RAUVITHERM',               '',   'ps.e.v'),
    ('push_systems', 'RAFTEC PUSH',              '',   'ps.r.g'),
    ('push_systems', 'RAFTEC PPSU PUSH',         '',   'ps.r.s'),
    ('push_systems', 'RAFTEC Труба',             '',   'ps.r.p'),   # RAFTEC труба PEX
    ('push_systems', 'RAFTEC запчасти к инструменту', '', 'ps.r.i'),
    ('push_systems', 'Электрический инструмент', '',   'ps.r.i'),
    ('push_systems', 'FADO',                     'Натяжной фитинг', 'ps.f.g'),
    ('push_systems', 'FADO',                     '',   'ps.f.g'),
    ('push_systems', 'Труба',                    '',   'ps.f.p'),
    ('push_systems', 'Зразки труба',             '',   'ps.f.p'),
    ('push_systems', 'KAN',                      'KAN-Therm PUSH (под заказ)', 'ps.k.p'),
    ('push_systems', 'KAN-Therm ultraLINE',      '',   'ps.k.u'),
    ('push_systems', 'Uponor',                   '',   'ps.u.f'),
    ('push_systems', 'HEAT-PEX',                 '',   'ps.h.g'),
    ('push_systems', 'TECE',                     '',   'ps.t.f'),
    ('push_systems', 'СИСТЕМЫ  PUSH',            '',   'ps.a.g'),
    ('push_systems', 'Фитинг AUSTROISOL',        '',   'ps.z.a'),
    ('push_systems', 'Предізольовані трубу Термоізол', '', 'ps.z.t'),
    ('push_systems', 'General Fittings',         '',   'ps.g.g'),

    # ══════════════════════════════════════════════════════════════════════════
    # АВТОМАТИКА (automation) at  — СФЕРА → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('automation', 'AJAX',                       '',   'at.ap.a'),
    ('automation', '*MASTINO',                   '',   'at.ap.m'),
    ('automation', 'GIDROLOCK',                  '',   'at.ap.g'),
    ('automation', 'TERVIX',                     '',   'at.k.te'),
    ('automation', 'SALUS/ ENGO',               '',   'at.k.su'),
    ('automation', 'DANFOSS',                    '',   'at.tf.d'),
    ('automation', 'REHAU',                      '',   'at.tf.re'),
    ('automation', 'MEIBES',                     '',   'at.k.m'),
    ('automation', 'COMPUTHERM',                 '',   'at.pr.c'),
    ('automation', 'Стабилизаторы',              '',   'at.rv.s'),
    ('automation', 'ИБП',                        '',   'at.rv.i'),
    ('automation', 'PROFI THERM',                '',   'at.el.pt'),
    ('automation', 'Водяна тепла підлога',       '',   'at.tf'),
    ('automation', 'Для систем отопления',       '',   'at.k'),

    # ══════════════════════════════════════════════════════════════════════════
    # АРМАТУРА БЕЗПЕКИ (safety_valves) sav  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('safety_valves', 'Редуктора давления',      'Caleffi', 'sav.rd.c'),
    ('safety_valves', 'Редуктора давления',      '',        'sav.rd'),
    ('safety_valves', 'HERZ',                    '',        'sav.kp.h'),
    ('safety_valves', 'RAFTEC',                  '',        'sav.kp.r'),
    ('safety_valves', 'Flamco',                  '',        'sav.kp.f'),
    ('safety_valves', 'AFRISO',                  '',        'sav.m'),
    ('safety_valves', 'BUGATTI',                 '',        'sav.kp.b'),
    ('safety_valves', 'GIACOMINI',               '',        'sav.v.g'),
    ('safety_valves', 'PLM',                     '',        'sav.pk.p'),
    ('safety_valves', 'ICMA',                    '',        'sav.kp.x'),
    ('safety_valves', 'Дешевые',                 '',        'sav.pk.e'),
    ('safety_valves', 'ASG',                     '',        'sav.v.a'),
    ('safety_valves', 'Raftec',                  '',        'sav.kp.r'),

    # ══════════════════════════════════════════════════════════════════════════
    # РУШНИКОСУШКИ (towel_warmers) tw  — ТИП → СЕРІЯ
    # ══════════════════════════════════════════════════════════════════════════
    ('towel_warmers', 'Электрические',           '',   'tw.e'),
    ('towel_warmers', 'Електричні',              '',   'tw.e'),
    ('towel_warmers', 'ЛАРИС',                   '',   'tw.w.la'),
    ('towel_warmers', 'ВAVARIA',                 '',   'tw.w.pw'),
    ('towel_warmers', 'Genesis Aqua',            '',   'tw.ac.d'),
    ('towel_warmers', 'ТЭНы',                   '',   'tw.ten'),
    ('towel_warmers', 'HLV',                     '',   'tw.w.hl'),
    ('towel_warmers', 'TRINNITY',                '',   'tw.w.tr'),
    ('towel_warmers', 'МАРИО',                   '',   'tw.e.ma'),
    ('towel_warmers', 'Sunny',                   '',   'tw.w.ar'),
    ('towel_warmers', 'Авангард',                '',   'tw.w.nv'),
    ('towel_warmers', 'Блюз',                    '',   'tw.w.nv'),
    ('towel_warmers', 'Камелия',                 '',   'tw.e.nv'),
    ('towel_warmers', 'Омега',                   '',   'tw.w.nv'),
    ('towel_warmers', 'Optima',                  '',   'tw.w.ar'),
    ('towel_warmers', 'Sora',                    '',   'tw.w.ar'),

    # ══════════════════════════════════════════════════════════════════════════
    # ШЛАНГИ (hoses) hs  — ТИП РІДИНИ → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('hoses', 'MATEU В НЕРЖАВЕЮЩЕЙ ОПЛЕТКЕ',    '',   'hs.w.m'),
    ('hoses', 'MATEU В ПОЛИАМИДНОЙ ОПЛЕТКЕ',    '',   'hs.w.m'),
    ('hoses', 'Raftec Rhein',                    '',   'hs.w.r'),
    ('hoses', 'FAS Flex',                        '',   'hs.w.fa'),
    ('hoses', 'HLV flex',                        '',   'hs.w.o'),
    ('hoses', 'ДТМ Flex',                        '',   'hs.w.o'),
    ('hoses', 'ШЛАНГИ',                          '',   'hs.w'),
    ('hoses', 'ВОДА СТАНДАРТ',                   '',   'hs.w.o'),
    ('hoses', 'ВОДА СУПЕР',                      '',   'hs.w.o'),
    ('hoses', 'ГАЗ СТАНДАРТ',                    '',   'hs.g.r'),
    ('hoses', 'ГАЗ СУПЕР',                       '',   'hs.g.r'),
    ('hoses', 'ГАЗ ЕВРО',                        '',   'hs.g.e'),
    ('hoses', 'Никифоров',                       '',   'hs.g.r'),
    ('hoses', 'Шланги для стир.маш.',            '',   'hs.sm'),
    ('hoses', 'Для Конвекторов',                 '',   'hs.e'),

    # ══════════════════════════════════════════════════════════════════════════
    # ВОДОМІРИ (water_meters) wm
    # ══════════════════════════════════════════════════════════════════════════
    ('water_meters', 'ECOSTAR',                  '',   'wm.e'),
    ('water_meters', 'GIDROTEK',                 '',   'wm.g'),

    # ══════════════════════════════════════════════════════════════════════════
    # СИФОНИ (siphons_fittings) sf  — БРЕНД → ТИП
    # ══════════════════════════════════════════════════════════════════════════
    ('siphons_fittings', 'АРМАТУРА АНИ-ПЛАСТ',  '',   'sf.an.a'),
    ('siphons_fittings', 'Трапы',                '',   'sf.an.t'),
    ('siphons_fittings', 'ТРАПЫ/ВОДОСТОЧНЫЕ',    '',   'sf.an.t'),
    ('siphons_fittings', 'ГОФРОСИФОНЫ',          '',   'sf.an.g'),
    ('siphons_fittings', 'Гибкие и фановые',     '',   'sf.an.g'),
    ('siphons_fittings', 'Ванна',                '',   'sf.an.v'),
    ('siphons_fittings', 'Умывальник',           '',   'sf.an.u'),
    ('siphons_fittings', 'VOLLE',                '',   'sf.vn'),
    ('siphons_fittings', 'KK Poll',              '',   'sf.kk'),
    ('siphons_fittings', 'ASG',                  '',   'sf.sp.a'),
    ('siphons_fittings', 'СИФОНЫ',               '',   'sf.an'),
    # ══════════════════════════════════════════════════════════════════════════
    # КОТЛИ (boilers) bl  — ТИП ПАЛИВА → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    # Димоходи — найбільша група
    ('boilers', 'Труба для димаря',              '',   'bl.d.t'),
    ('boilers', 'Труба с термоизоляцией',        '',   'bl.d.t'),
    ('boilers', 'Труба без термоизоляции',       '',   'bl.d.t'),
    ('boilers', 'Труба',                         '',   'bl.d.t'),
    ('boilers', 'Тройник',                       '',   'bl.d.r'),
    ('boilers', 'Колено',                        '',   'bl.d.k'),
    ('boilers', 'Переход',                       '',   'bl.d.k'),
    ('boilers', 'Конус',                         '',   'bl.d.g'),
    ('boilers', 'Хомут',                         '',   'bl.d.t'),
    ('boilers', 'Дымоходы',                      '',   'bl.d'),
    # Тверде паливо
    ('boilers', 'Feniks',                        '',   'bl.t.f'),
    ('boilers', 'ALTEP',                         '',   'bl.t.a'),
    ('boilers', 'Kalvis',                        '',   'bl.t.k'),
    # Газові
    ('boilers', 'BAXI',                          '',   'bl.g.bx'),
    ('boilers', 'BOSCH',                         '',   'bl.g.bs'),
    ('boilers', 'Vaillant',                      '',   'bl.g.va'),
    ('boilers', 'Viessmann',                     '',   'bl.g.vi'),
    # Автоматика ТТК
    ('boilers', 'Блок керування',                '',   'bl.c'),

    # ══════════════════════════════════════════════════════════════════════════
    # ВОДОНАГРІВАЧІ (water_heaters) wh  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('water_heaters', 'Електричні накопичувальні','',  'wh.e'),
    ('water_heaters', 'Сухой тен',               '',   'wh.e'),
    ('water_heaters', 'Мокрый тен',              '',   'wh.e'),
    ('water_heaters', 'GORENJE',                 '',   'wh.e.go'),
    ('water_heaters', 'ARISTON',                 '',   'wh.e.ar'),
    ('water_heaters', 'BOSCH',                   '',   'wh.e.bs'),
    ('water_heaters', 'MIDEA',                   '',   'wh.e.md'),
    ('water_heaters', 'Hi-therm',                '',   'wh.e.ht'),
    ('water_heaters', 'RENS',                    '',   'wh.e.re'),
    ('water_heaters', 'Косвенные',               '',   'wh.k'),
    ('water_heaters', 'Теплоаккумуляторы',       '',   'wh.k'),
    ('water_heaters', 'Комбинированные',         '',   'wh.k'),
    ('water_heaters', 'Тены Drazice',            '',   'wh.x'),

    # ══════════════════════════════════════════════════════════════════════════
    # ЗМІШУВАЧІ (mixers_faucets) mx  — БРЕНД → ТИП
    # ══════════════════════════════════════════════════════════════════════════
    ('mixers_faucets', 'VOLLE',                  '',   'mx.kl'),
    ('mixers_faucets', 'Q-Tab / Lidz',           '',   'mx.kl'),
    ('mixers_faucets', 'Globus Lux',             '',   'mx.kl'),
    ('mixers_faucets', 'Paffoni',                '',   'mx.kl'),
    ('mixers_faucets', 'Kludi',                  '',   'mx.kl'),
    ('mixers_faucets', 'Franke',                 '',   'mx.kl'),
    ('mixers_faucets', 'Grohe',                  '',   'mx.gr'),
    ('mixers_faucets', 'Душ',                    '',   'mx.g.d'),
    ('mixers_faucets', 'Умывальник',             '',   'mx.g.u'),
    ('mixers_faucets', 'Кухня',                  '',   'mx.g.k'),
    ('mixers_faucets', 'Ванна',                  '',   'mx.g.v'),
    ('mixers_faucets', 'Умивальник',             '',   'mx.g.u'),
    ('mixers_faucets', 'Набор',                  '',   'mx.c'),
    ('mixers_faucets', 'Наборы',                 '',   'mx.c'),
    ('mixers_faucets', 'Лейки для душа Venta',   '',   'mx.vn.ac'),
    ('mixers_faucets', 'Комплектующие',          '',   'mx.c'),

    # ══════════════════════════════════════════════════════════════════════════
    # САНФАЯНС (sanitary_ware) sw  — ТИП → БРЕНД
    # ══════════════════════════════════════════════════════════════════════════
    ('sanitary_ware', 'Умывальники',             '',   'sw.ker'),
    ('sanitary_ware', 'MIRAGGIO умывальники',    '',   'sw.ker.mr'),
    ('sanitary_ware', 'FANCY MARBLE умывальники','',   'sw.ker.fm'),
    ('sanitary_ware', 'Унитазы подвесные',       '',   'sw.ker'),
    ('sanitary_ware', 'Поддон душевой стальной', '',   'sw.dc.pd'),
    ('sanitary_ware', 'LIDZ / QTAP',             '',   'sw.ker.qt'),
    ('sanitary_ware', 'Walk IN',                 '',   'sw.dc'),
    ('sanitary_ware', 'BESCO',                   '',   'sw.va.ac.bs'),
    ('sanitary_ware', 'Liberta',                 '',   'sw.dc.lb'),
    ('sanitary_ware', 'Мойдодыр',               '',   'sw.me'),
    ('sanitary_ware', 'Тумба + умывальник',      '',   'sw.me'),
    ('sanitary_ware', 'Зеркала Liberta',         '',   'sw.me'),
    ('sanitary_ware', 'FRANKE',                  '',   'sw.mo.fr'),
    ('sanitary_ware', 'Хасека',                  '',   'sw.ker'),
    ('sanitary_ware', 'SIGMA',                   '',   'sw.in'),
    ('sanitary_ware', 'GROHE',                   '',   'sw.ker.gr'),
    ('sanitary_ware', 'RAVAK',                   '',   'sw.dc.rv'),
    ('sanitary_ware', 'WESTON',                  '',   'sw.dc'),
    ('sanitary_ware', 'Liberta',                 '',   'sw.dc.lb'),

    # ══════════════════════════════════════════════════════════════════════════
    # КОТЛИ (boilers) bl  — димоходи і комплектуючі
    # ══════════════════════════════════════════════════════════════════════════
    ('boilers', 'Труба для димаря',              '',   'bl.d.t'),
    ('boilers', 'Труба с термоизоляцией',        '',   'bl.d.t'),
    ('boilers', 'Труба без термоизоляции',       '',   'bl.d.t'),
    ('boilers', 'Тройник',                       '',   'bl.d.r'),
    ('boilers', 'Колено',                        '',   'bl.d.k'),
    ('boilers', 'Переход',                       '',   'bl.d.k'),
    ('boilers', 'Конус',                         '',   'bl.d.g'),
    ('boilers', 'Хомут',                         '',   'bl.d.t'),
    ('boilers', 'Дымоходы',                      '',   'bl.d'),
    ('boilers', 'Feniks',                        '',   'bl.t.f'),
    ('boilers', 'ALTEP',                         '',   'bl.t.a'),
    ('boilers', 'Kalvis',                        '',   'bl.t.k'),
    ('boilers', 'Термобар',                      '',   'bl.g.at'),
    ('boilers', 'Житомир',                       '',   'bl.g.at'),
    ('boilers', 'BAXI',                          '',   'bl.g.bx'),
    ('boilers', 'BOSCH',                         '',   'bl.g.bs'),
    ('boilers', 'BIASI',                         '',   'bl.g.bi'),
    ('boilers', 'Vaillant',                      '',   'bl.g.va'),
    ('boilers', 'Viessmann',                     '',   'bl.g.vi'),
    ('boilers', 'Блок керування',                '',   'bl.c'),
    ('boilers', 'Комплектующие',                 '',   'bl.c'),
    ('boilers', 'Теплоаккумулятор',              '',   'bl.ld'),

    # ══════════════════════════════════════════════════════════════════════════
    # ВОДОНАГРІВАЧІ (water_heaters) wh
    # ══════════════════════════════════════════════════════════════════════════
    ('water_heaters', 'Електричні накопичувальні','',  'wh.e'),
    ('water_heaters', 'Сухой тен',               '',   'wh.e'),
    ('water_heaters', 'Мокрый тен',              '',   'wh.e'),
    ('water_heaters', 'GORENJE',                 '',   'wh.e.go'),
    ('water_heaters', 'ARISTON',                 '',   'wh.e.ar'),
    ('water_heaters', 'BOSCH',                   '',   'wh.e.bs'),
    ('water_heaters', 'MIDEA',                   '',   'wh.e.md'),
    ('water_heaters', 'Hi-therm',                '',   'wh.e.ht'),
    ('water_heaters', 'RENS',                    '',   'wh.e.re'),
    ('water_heaters', 'KOSPEL',                  '',   'wh.p.ks'),
    ('water_heaters', 'ELDOM',                   '',   'wh.k.el'),
    ('water_heaters', 'NovaTeс',                 '',   'wh.e.nt'),
    ('water_heaters', 'Thermo Alliance',         '',   'wh.g.th'),
    ('water_heaters', 'Косвенные',               '',   'wh.k'),
    ('water_heaters', 'Теплоаккумуляторы',       '',   'wh.k'),
    ('water_heaters', 'Комбинированные',         '',   'wh.k'),
    ('water_heaters', 'Запчастини',              '',   'wh.x'),

    # ══════════════════════════════════════════════════════════════════════════
    # РАДІАТОРИ — додаткові підвузли
    # ══════════════════════════════════════════════════════════════════════════
    ('radiators_radiatorsvalve', 'Прочее ПОД заказ',  '', 'rd.s.x'),
    ('radiators_radiatorsvalve', '20 тип (п/з)',       '', 'rd.s.hi'),
    ('radiators_radiatorsvalve', '21 тип (п/з)',       '', 'rd.s.hi'),
    ('radiators_radiatorsvalve', '*Rens/TERRA Teknik', '', 'rd.s.re'),
    ('radiators_radiatorsvalve', 'VARIO TERM',         '', 'rd.s.x'),
    ('radiators_radiatorsvalve', 'АКСЕССУАРЫ',         '', 'rd.ac'),
    ('radiators_radiatorsvalve', 'Стінове кріплення',  '', 'rd.ac'),

    # ══════════════════════════════════════════════════════════════════════════
    # РУШНИКОСУШКИ — під замовлення і серії
    # ══════════════════════════════════════════════════════════════════════════
    ('towel_warmers', 'Комплект прихованого підключення', '', 'tw.ac'),
    ('towel_warmers', 'Під замовлення',          '',   'tw.pz'),
    ('towel_warmers', 'ТЕРМА',                   '',   'tw.w'),
    ('towel_warmers', 'Sirius',                  '',   'tw.w.ar'),
    ('towel_warmers', 'Ava',                     '',   'tw.w.ar'),
    ('towel_warmers', 'Largo',                   '',   'tw.w.nv'),
    ('towel_warmers', 'Водяні',                  '',   'tw.w'),

    # ══════════════════════════════════════════════════════════════════════════
    # ЗМІШУВАЧІ (mixers_faucets) mx
    # ══════════════════════════════════════════════════════════════════════════
    ('mixers_faucets', 'VOLLE',                  '',   'mx.kl'),
    ('mixers_faucets', 'Q-Tab / Lidz',           '',   'mx.kl'),
    ('mixers_faucets', 'Globus Lux',             '',   'mx.kl'),
    ('mixers_faucets', 'Paffoni',                '',   'mx.kl'),
    ('mixers_faucets', 'Kludi',                  '',   'mx.kl'),
    ('mixers_faucets', 'Franke',                 '',   'mx.kl'),
    ('mixers_faucets', 'GROHE',                  '',   'mx.gr'),
    ('mixers_faucets', 'Grohe',                  '',   'mx.gr'),
    ('mixers_faucets', 'WESTON',                 '',   'mx.kl'),
    ('mixers_faucets', 'RAVAK',                  '',   'mx.kl'),
    ('mixers_faucets', 'Lidz',                   '',   'mx.kl'),
    ('mixers_faucets', 'Душ',                    '',   'mx.g.d'),
    ('mixers_faucets', 'Умывальник',             '',   'mx.g.u'),
    ('mixers_faucets', 'Умивальник',             '',   'mx.g.u'),
    ('mixers_faucets', 'Кухня',                  '',   'mx.g.k'),
    ('mixers_faucets', 'Ванна',                  '',   'mx.g.v'),
    ('mixers_faucets', 'Набор',                  '',   'mx.c'),
    ('mixers_faucets', 'Наборы',                 '',   'mx.c'),
    ('mixers_faucets', 'Лейки для душа Venta',   '',   'mx.vn.ac'),
    ('mixers_faucets', 'Комплектующие',          '',   'mx.c'),

    # ══════════════════════════════════════════════════════════════════════════
    # СИФОНИ — додаткові
    # ══════════════════════════════════════════════════════════════════════════
    ('siphons_fittings', 'Viega',                '',   'sf.rv'),
    ('siphons_fittings', 'Комплектуючі',         '',   'sf.pl.k'),
    ('siphons_fittings', 'Водосливы',            '',   'sf.an'),
    ('siphons_fittings', 'Мойка',                '',   'sf.an.k'),

]


# ─── Компіляція паттернів ─────────────────────────────────────────────────────

def _compile(s: str):
    """Компілює паттерн: якщо починається з r: — regex, інакше точне включення."""
    if not s:
        return None  # None = будь-який (пропускаємо перевірку)
    if s.startswith('r:'):
        return re.compile(s[2:], re.IGNORECASE)
    return s.lower()  # порівнюємо через 'in'


_COMPILED: list[tuple] = []
for _cat, _gp, _sp, _nid in _RULES:
    _COMPILED.append((_cat, _compile(_gp), _compile(_sp), _nid))


# ─── Основна функція ──────────────────────────────────────────────────────────

def assign_node_id(category: str, group: str, subgroup: str) -> str:
    """
    Повертає node_id для товару за (category, group, subgroup).

    Алгоритм:
      1. Перебираємо _COMPILED по порядку (специфічніші вище).
      2. Категорія повинна збігатись точно.
      3. group_pattern: None = будь-який, str = 'in group.lower()', regex = re.search.
      4. subgroup_pattern: аналогічно.
      5. Перший збіг → повертаємо node_id.
      6. Якщо нічого — повертаємо кореневий вузол категорії.
    """
    g_lower = group.lower()
    s_lower = subgroup.lower()

    for cat, gp, sp, nid in _COMPILED:
        if cat != category:
            continue
        # Перевірка group: None=будь-який, regex=re.search, str=підрядок (in)
        if gp is None:
            g_ok = True
        elif isinstance(gp, re.Pattern):
            g_ok = bool(gp.search(group))
        else:
            g_ok = (gp in g_lower)   # підрядок: 'ekoplastik' in 'ekoplastik труба'
        if not g_ok:
            continue
        # Перевірка subgroup: аналогічно
        if sp is None:
            s_ok = True
        elif isinstance(sp, re.Pattern):
            s_ok = bool(sp.search(subgroup))
        else:
            s_ok = (sp in s_lower)   # підрядок: 'труба' in 'ekoplastik труба'
        if not s_ok:
            continue
        return nid

    # Fallback: кореневий вузол категорії
    return CAT_ROOT.get(category, 'xx')


def node_pool(catalog: list[dict], node_id: str) -> list[dict]:
    """
    Повертає список товарів що належать вузлу або його підвузлам.
    Використовує node_id.startswith() — О(n), але n невеликий.
    Якщо node_id = 'xx' або порожній → повертає весь каталог.
    """
    if not node_id or node_id == 'xx':
        return catalog
    return [it for it in catalog if it.get('_node_id', '').startswith(node_id)]
