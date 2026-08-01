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
    ('sewage', 'ASG',                       '',              'kn.u.f.a'),  # ASG фітинги
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
        # Перевірка group: None=будь-який, regex=re.search, str=точне порівняння (==)
        # Точне == запобігає помилці: 'raftec' in 'raftec труба' = True
        if gp is None:
            g_ok = True
        elif isinstance(gp, re.Pattern):
            g_ok = bool(gp.search(group))
        else:
            g_ok = (gp == g_lower)
        if not g_ok:
            continue
        # Перевірка subgroup: аналогічно
        if sp is None:
            s_ok = True
        elif isinstance(sp, re.Pattern):
            s_ok = bool(sp.search(subgroup))
        else:
            s_ok = (sp == s_lower)
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
