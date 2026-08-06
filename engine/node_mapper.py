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

    # Зовнішня — VALROM (G='*VALROM' в xlsx)
    ('sewage', '*VALROM',                   '',              'kn.e.f.v'),

    # Внутрішня — Эко (G='Эко' в xlsx)
    ('sewage', 'Эко',                       '',              'kn.u.f.x'),

    # Інше (ємності, лотки)
    ('sewage', 'Гофра в бухтах',            '',              'kn.o.g'),
    ('sewage', r'r:Ёмкост',                 '',              'kn.o.t'),
    ('sewage', 'Ёмкости (под заказ)',        '',              'kn.o.t'),
    ('sewage', 'Сепараторы и дождеприемники','',             'kn.o.t'),
    ('sewage', 'ERA ПВХ',                   '',              'kn.o.e'),
    ('sewage', 'Канализация ПРОЧЕЕ',        '',              'kn.o.c'),
    ('sewage', r'r:Набор подставок',        '',              'kn.o'),
    ('sewage', 'Фитинг для соединения',     '',              'kn.o'),
    ('sewage', r'r:ЛЮК',                    '',              'kn.o.t'),
    ('sewage', 'КАНАЛІЗАЦІЯ',               '',              'kn.u'),      # fallback внутрішня

    # ══════════════════════════════════════════════════════════════════════════
    # PPR ПЛАСТИК (plastic_ppr) pp
    # Ієрархія: ВИРОБНИК → ТИП ТОВАРУ
    # ══════════════════════════════════════════════════════════════════════════

    # ASG — реальні G в xlsx: G='ASG', SG='ASG труба' або G='ASG фитинг'
    ('plastic_ppr', 'ASG',                  'ASG труба',     'pp.a.p'),
    ('plastic_ppr', 'ПЛАСТИК',              'ASG труба',     'pp.a.p'),
    ('plastic_ppr', 'ПЛАСТИК',              'ASG',           'pp.a.f'),
    ('plastic_ppr', 'ASG фитинг',           '',              'pp.a.f'),
    ('plastic_ppr', 'WHITE',                '',              'pp.a.w'),

    # Ekoplastik
    ('plastic_ppr', 'EKOPLASTIK',           'Ekoplastik труба', 'pp.e.p'),
    ('plastic_ppr', 'Ekoplastik фитинг',    '',              'pp.e.f'),

    # OVI / EVCI
    ('plastic_ppr', 'OVI/EVCI Pipe',        '',              'pp.o.p'),
    ('plastic_ppr', 'OVI PREMIUM PIPE',     '',              'pp.o.x'),  # реальна G в xlsx
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

    # PLM — G='PLM', SG='Зразки' або SG='' (фітинг), G='PLM Фитинг'
    ('plastic_ppr', 'PLM',                  'PLM Труба',     'pp.p.p'),
    ('plastic_ppr', 'PLM',                  'Зразки',        'pp.p.p'),
    ('plastic_ppr', 'PLM Фитинг',           '',              'pp.p.f'),

    # RAFTEC PPR — індивідуальні товари як G (одиночні зразки без артикулу)
    ('plastic_ppr', 'RAFTEC',               'RAFTEC Труба',  'pp.r.p'),
    ('plastic_ppr', 'RAFTEC Фитинг',        '',              'pp.r.f'),
    ('plastic_ppr', r'r:Коліно PPR',        '',              'pp.r.f'),
    ('plastic_ppr', r'r:Муфта PPR',         '',              'pp.r.f'),
    ('plastic_ppr', r'r:Трійник.*PPR',      '',              'pp.r.f'),
    ('plastic_ppr', r'r:Кран.*PPR',         '',              'pp.r.f'),
    ('plastic_ppr', r'r:Муфта PPR МРЗ',     '',              'pp.r.f'),
    ('plastic_ppr', r'r:Обрезки трубы RAFTEC', '',           'pp.r.p'),
    ('plastic_ppr', r'r:Комплект зразків.*PPR', '',          'pp.r.p'),

    # ECO PPR — G='ECO PPR', або G='Американка PPR...' (одиночний товар як G)
    ('plastic_ppr', 'ECO PPR',              '',              'pp.c.f'),
    ('plastic_ppr', 'Фитинг PPR',           '',              'pp.c.f'),
    ('plastic_ppr', r'r:Американка PPR',    '',              'pp.c.f'),

    # Інші фітинги / аксесуари
    ('plastic_ppr', 'ПРОЧЕЕ',               'Blue Ocean',    'pp.b'),
    ('plastic_ppr', 'ПРОЧЕЕ',               'BLUE OCEAN 2 (фитинг)', 'pp.b'),
    ('plastic_ppr', 'Разное',               '',              'pp.b'),
    ('plastic_ppr', 'Образцы',              '',              'pp.b'),
    ('plastic_ppr', 'ЗРАЗКИ',              '',               'pp.b'),
    ('plastic_ppr', 'ЗРАЗКИ!',             '',               'pp.b'),
    ('plastic_ppr', 'Аксесcуары',           '',              'pp.x'),
    ('plastic_ppr', r'r:Насадка',           '',              'pp.x'),
    ('plastic_ppr', r'r:Паяльник',          '',              'pp.x'),

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

    # ПНТ труби — G='Труба ПНТ ф...' або G='ПНТ трубы' SG='ПНТ AКВА'
    ('plastic_ppr', r'r:^Труба ПНТ ф',     '',              'pp.n.a'),
    ('plastic_ppr', 'ПНТ трубы',            'ПНТ AКВА (Технічна)', 'pp.n.a'),
    ('plastic_ppr', r'r:Заглушка стикова.*FOX', '',         'pp.n.f'),

    # Фітинги загальні / шланги / адаптори — у fallback pp.b
    ('plastic_ppr', 'Фітинг',              '',               'pp.b'),
    ('plastic_ppr', 'Фитинг для шланга',   '',               'pp.b'),
    ('plastic_ppr', 'Образцы Reiger',      '',               'pp.b'),
    ('plastic_ppr', 'Фланцевий адаптор',   '',               'pp.b'),  # не PPR — misc
    ('plastic_ppr', r'r:^Шланг арм',       '',               'pp.w.s'),  # шланги
    ('plastic_ppr', 'AVCI',                '',               'pp.w.s'),

    # Служебна
    ('plastic_ppr', 'Служебная номенклатура','',             'pp.b'),
    ('plastic_ppr', r'r:Товар в ассортименте','',            'pp.b'),

    # ══════════════════════════════════════════════════════════════════════════
    # PUSH/PEX (push_systems) ps
    # Ієрархія: ВИРОБНИК → ТИП ТОВАРУ
    # ══════════════════════════════════════════════════════════════════════════

    ('push_systems', 'СИСТЕМЫ  PUSH',       'Aquapex',       'ps.a.g'),
    ('push_systems', 'FADO',                'Натяжной фитинг','ps.f.g'),
    ('push_systems', 'FADO',                '',              'ps.f.p'),
    ('push_systems', 'Труба',               'КУСКИ',         'ps.e.p'),  # REHAU куски
    ('push_systems', 'Труба',               '',              'ps.f.p'),
    ('push_systems', 'General Fittings',    '',              'ps.g.g'),
    ('push_systems', 'HEAT-PEX',            '',              'ps.h.g'),
    ('push_systems', 'KAN',                 'KAN-Therm PUSH (под заказ)', 'ps.k.p'),
    ('push_systems', 'KAN-Therm ultraLINE (под заказ)','',  'ps.k.u'),
    ('push_systems', 'KAN',                 '',              'ps.k'),
    # RAFTEC PPSU — G='RAFTEC PPSU PUSH' (напряму в xlsx)
    ('push_systems', 'RAFTEC PPSU PUSH',    '',              'ps.r.s'),
    ('push_systems', 'RAFTEC',              'RAFTEC PPSU PUSH','ps.r.s'),
    ('push_systems', 'RAFTEC PUSH',         '',              'ps.r.g'),
    ('push_systems', 'RAFTEC',              '',              'ps.r.g'),
    ('push_systems', 'RAFTEC запчасти к инструменту','Механичекий инструмент','ps.r.i'),
    ('push_systems', 'RAFTEC запчасти к инструменту','',    'ps.r.i'),
    ('push_systems', 'Электрический инструмент','',          'ps.r.i'),
    ('push_systems', 'RAFTEC Інструмент PUSH','',            'ps.r.i'),
    ('push_systems', r'r:RAFTEC Труба',     '',              'ps.r.p'),
    ('push_systems', 'Зразки труба',        '',              'ps.r.p'),
    # REHAU — G='RAUTITAN', G='RAUBASIC', G='КУСКИ' (самостійні groups в xlsx)
    ('push_systems', 'RAUTITAN',            '',              'ps.e.r'),
    ('push_systems', 'RAUBASIC',            '',              'ps.e.b'),
    ('push_systems', 'КУСКИ',               '',              'ps.e.p'),
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
    ('push_systems', r'r:Предізольовані трубу', '',          'ps.z.t'),
    ('push_systems', r'r:ЗРАЗОК! Євроконус',  '',            'ps.z.k'),
    ('push_systems', r'r:Коліно настінне',  '',              'ps.g.f'),
    ('push_systems', r'r:Фитинг \(натяжная гильза\)', '',   'ps.f.g'),
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
    # Реальна структура xlsx: G = бренд або тип, SG = серія або бренд
    # ══════════════════════════════════════════════════════════════════════════

    # ─── Крани з американкою ────────────────────────────────────────────────
    # G є бренд серії; контекст визначається по серії або попередній групі
    ('shutoff_valves', 'ЭКО американки',    '',              'sv.a.e'),
    ('shutoff_valves', 'ЭКО(PN20)',         '',              'sv.k.e'),  # ЕКО кран з американкою
    ('shutoff_valves', 'ASG',               'ASG (Червоний)','sv.k.a'),  # G=ASG, SG=ASG (Червоний)
    ('shutoff_valves', 'ASG + (Сірий)',     '',              'sv.k.a'),  # G=ASG + (Сірий)
    ('shutoff_valves', 'ASG+ (Cірий)',      '',              'sv.k.a'),  # G=ASG+ (Cірий) — інша орфографія
    ('shutoff_valves', 'ARIZONA',           '',              'sv.k.b'),
    ('shutoff_valves', 'ARIZONA (PN40)',    '',              'sv.m.b'),  # Bugatti ARIZONA PN40 — кульовий
    ('shutoff_valves', 'OREGON',            '',              'sv.k.b'),
    ('shutoff_valves', 'OREGON (PN64)',     '',              'sv.m.b'),  # Bugatti OREGON PN64 — кульовий
    ('shutoff_valves', 'STRONG',            '',              'sv.k.p'),  # PLM STRONG — кран з американкою
    ('shutoff_valves', 'ВASE',              '',              'sv.k.p'),  # PLM BASE — кран з американкою
    ('shutoff_valves', 'RAFTEC GOLD',       '',              'sv.k.r.g'),
    ('shutoff_valves', 'RAFTEC WHITE',      '',              'sv.k.r.w'),
    ('shutoff_valves', 'RАFTEC BLACK',      '',              'sv.k.r.b'),  # кирилична А
    ('shutoff_valves', 'RAFTEC BLACK',      '',              'sv.k.r.b'),  # латинська A
    ('shutoff_valves', 'RАFTEC RED',        '',              'sv.k.r.d'),  # кирилична А
    ('shutoff_valves', 'RАFTEC  BLACK',     '',              'sv.k.r.b'),  # подвійний пробіл

    # ─── Фланцеві / під замовлення ──────────────────────────────────────────
    ('shutoff_valves', 'Краны фланцевые (под заказ)', 'Краны Breeze (Под заказ)', 'sv.fl.b'),
    ('shutoff_valves', 'Краны фланцевые (под заказ)', '',    'sv.fl'),
    ('shutoff_valves', 'Под заказ',         '',              'sv.fl'),

    # ─── Засувки / батерфляї / вентилі ──────────────────────────────────────
    ('shutoff_valves', 'Батерфляй',         '',              'sv.b.b'),
    ('shutoff_valves', 'Вентиль чугун',     '',              'sv.b.v'),
    ('shutoff_valves', 'Задвижки чугунные', '',              'sv.b.z'),
    ('shutoff_valves', 'Задвижки латунь',   '',              'sv.b.z'),

    # ─── Зворотні клапани ───────────────────────────────────────────────────
    ('shutoff_valves', 'Bugatti',           '',              'sv.z.b'),
    ('shutoff_valves', 'Акционные клапана', '',              'sv.z.x'),
    ('shutoff_valves', 'Клапана межфланц (под заказ)', '',   'sv.z.x'),
    ('shutoff_valves', 'Лепестковый обратный клапан (хлопушка)', '', 'sv.z.x'),
    ('shutoff_valves', r'r:Клапан пелюстков',  '',           'sv.z.x'),  # regex: Клапан пелюстков*
    ('shutoff_valves', 'Сетка клапана',     '',              'sv.z'),
    ('shutoff_valves', 'ЭКО клапана',       '',              'sv.z.e'),
    # PLM зворотній — нижче конфліктує з кульовим, тому точна SG
    # (PLM без SG = кульовий, тому зворотній не додаємо без SG)

    # ─── Поливальні ─────────────────────────────────────────────────────────
    ('shutoff_valves', 'Meibes',            '',              'sv.po.m'),

    # ─── Кульові — ВОДА ─────────────────────────────────────────────────────
    # Спочатку специфічні бренди+серії, потім загальні
    ('shutoff_valves', 'ASG',               '',              'sv.m.a'),   # G=ASG (без SG = кульовий вода)
    ('shutoff_valves', 'BUGATTI',           '',              'sv.m.b'),   # G=BUGATTI
    ('shutoff_valves', 'CLASSIC',           '',              'sv.m.f'),   # FADO CLASSIC
    ('shutoff_valves', 'NEW',               '',              'sv.m.f'),   # FADO NEW
    ('shutoff_valves', 'NEW JERSEY (PN50)', '',              'sv.m.b'),   # Bugatti
    ('shutoff_valves', 'DADO',              '',              'sv.m.g'),   # Giacomini DADO
    ('shutoff_valves', 'FADO',              '',              'sv.m.f'),
    ('shutoff_valves', 'Giacomini',         '',              'sv.m.g'),
    ('shutoff_valves', 'HLV (PN40)',        '',              'sv.m.h'),
    ('shutoff_valves', 'HLV',              '',               'sv.m.h'),
    ('shutoff_valves', 'PLM',               '',              'sv.m.p'),
    ('shutoff_valves', 'RAFTEC',            'Краны Полотенцесушители LUX', 'sv.pr.r'),
    ('shutoff_valves', 'RAFTEC',            '',              'sv.m.r'),   # G=RAFTEC (без SG = кульовий)
    ('shutoff_valves', 'Акционные позиции', '',              'sv.m.x'),
    ('shutoff_valves', 'Гросс',             '',              'sv.m.x'),
    ('shutoff_valves', r'r:Кран кульовий Roho', '',          'sv.m.x'),
    ('shutoff_valves', r'r:кран кульовий R\d+', '',          'sv.m.x'),
    ('shutoff_valves', r'r:Розбірне з.єднання', '',          'sv.a.e'),

    # ─── Кульові — ГАЗ ──────────────────────────────────────────────────────
    ('shutoff_valves', 'Газовые',           '',              'sv.g.z'),
    ('shutoff_valves', 'ЭКО',              '',               'sv.g.e'),  # G=ЭКО (газ)

    # ─── Приладові (хромовані) ───────────────────────────────────────────────
    ('shutoff_valves', 'Приборные краны(хромированные)', 'Albetroni', 'sv.pr.a'),
    ('shutoff_valves', 'Приборные краны(хромированные)', 'ASG',       'sv.pr.s'),
    ('shutoff_valves', 'Приборные краны(хромированные)', 'BUGATTI',   'sv.pr.b'),
    ('shutoff_valves', 'Приборные краны(хромированные)', 'Grohe',     'sv.pr.g'),
    ('shutoff_valves', 'Приборные краны(хромированные)', 'HLV',       'sv.pr.h'),
    ('shutoff_valves', 'Приборные краны(хромированные)', '',          'sv.pr'),
    ('shutoff_valves', 'Grohe',             '',              'sv.pr.g'),
    ('shutoff_valves', 'Приладові з фільтром', '',           'sv.pr.r'),
    ('shutoff_valves', 'Приладові серії Silver', '',         'sv.pr.r'),
    ('shutoff_valves', 'МИНИ разные',       '',              'sv.pr.m'),

    # ─── Самопромивні фільтри ────────────────────────────────────────────────
    ('shutoff_valves', 'Самопромывные фильтра', 'HERZ',      'sv.sf.h'),
    ('shutoff_valves', 'Самопромывные фильтра', 'HLV',       'sv.sf.l'),
    ('shutoff_valves', 'Самопромывные фильтра', 'HONEYWELL', 'sv.sf.w'),
    ('shutoff_valves', 'Самопромывные фильтра', 'RAFTEC',    'sv.sf.r'),
    ('shutoff_valves', 'Самопромывные фильтра', '',          'sv.sf'),
    ('shutoff_valves', 'HERZ',              '',              'sv.sf.h'),
    ('shutoff_valves', 'HONEYWELL',         '',              'sv.sf.w'),

    # ─── Фільтри грубої очистки ──────────────────────────────────────────────
    ('shutoff_valves', 'Фильтра грубой очистки', 'ASG',      'sv.fg.a'),
    ('shutoff_valves', 'Фильтра грубой очистки', 'Bugatti',  'sv.fg.b'),
    ('shutoff_valves', 'Фильтра грубой очистки', 'HLV',      'sv.fg.h'),
    ('shutoff_valves', 'Фильтра грубой очистки', 'LEXLINE',  'sv.fg.l'),
    ('shutoff_valves', 'Фильтра грубой очистки', 'PLM',      'sv.fg.p'),
    ('shutoff_valves', 'Фильтра грубой очистки', 'RAFTEC',   'sv.fg.r'),
    ('shutoff_valves', 'Фильтра грубой очистки', '',         'sv.fg'),
    ('shutoff_valves', 'Прочие фильтра',    '',              'sv.fg.z'),
    ('shutoff_valves', 'Фильтра фланцевые', '',              'sv.fg.z'),
    ('shutoff_valves', 'ЭКО фильтра',       '',              'sv.fg.z'),
    ('shutoff_valves', 'LEXLINE',           '',              'sv.fg.l'),

    # ─── Кран з фільтром / різне ─────────────────────────────────────────────
    ('shutoff_valves', 'Кран с фильтром',   '',              'sv.fg'),
    ('shutoff_valves', 'FADO ( под заказ)', '',              'sv.a.f'),
    ('shutoff_valves', 'SOLOMON',           '',              'sv.m.x'),
    ('shutoff_valves', 'Краны трехходовые', '',              'sv.3'),
    ('shutoff_valves', r'r:Набор образцов', '',              'sv.m.x'),
    ('shutoff_valves', 'Прочая ЗП',         '',              'sv.m.x'),
    ('shutoff_valves', 'Прочее',            '',              'sv.m.x'),

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
