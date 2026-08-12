"""services/batch_service.py — Розгортання позицій замовлення (PUSH, ізоляція, гільзи)."""
import re
import math


# Маппінг діаметру труби → діаметр утеплювача PLM
INSUL_DIA_MAP = {
    '16': 18, '20': 22, '25': 28, '32': 35,
    '40': 42, '50': 54, '63': 64, '75': 76,
    '90': 90, '110': 114,
}


def _qty_num(qty_str) -> float:
    """Витягує число з рядка кількості ('10 шт' → 10.0)."""
    m = re.search(r'(\d+(?:[.,]\d+)?)', str(qty_str or ''))
    return float(m.group(1).replace(',', '.')) if m else 0


def expand_push_marker(позиції: list[dict]) -> list[dict]:
    """Якщо в списку є 'гільзи' — переводить PPR/metal_plastic у push_systems."""
    has_sleeve = any(
        'гільз' in ((п.get('original') or '') + (п.get('normalized') or '')).lower()
        for п in позиції
    )
    if not has_sleeve:
        return позиції
    for п in позиції:
        if п.get('category') in ('plastic_ppr', 'metal_plastic', 'other'):
            п['category'] = 'push_systems'
            n = п.get('normalized') or ''
            if 'push' not in n.lower() and 'натяжн' not in n.lower():
                п['normalized'] = (n + ' натяжний PUSH').strip()
    return позиції


def expand_insulation(позиції: list[dict], build_qa_fn) -> list[dict]:
    """Розгортає '+ізол' в окремі позиції утеплювача PLM (синій + червоний)."""
    out = []
    for п in позиції:
        out.append(п)
        orig = п.get('original', '').lower()
        norm = п.get('normalized', '').lower()
        qa   = п.get('_qa') or build_qa_fn(п)
        п['_qa'] = qa
        if qa.get('type') != 'труба':
            continue
        if not re.search(r'ізол|изол|утепл', orig):
            continue

        dia     = (qa.get('dia') or [None])[0]
        ins_dia = INSUL_DIA_MAP.get(str(dia) if dia else '')
        if not ins_dia:
            continue

        m_total = 0.0
        lm = re.search(r'l=(\d+(?:[.,]\d+)?)м', norm)
        if lm:
            m_total = float(lm.group(1).replace(',', '.'))
        if not m_total:
            lm = re.search(r'(\d+(?:[.,]\d+)?)\s*м', orig)
            if lm:
                v = float(lm.group(1).replace(',', '.'))
                if v > 1:
                    m_total = v
        if not m_total:
            m_total = _qty_num(п.get('qty'))

        if m_total <= 0:
            continue

        half_raw = m_total / 2
        half     = math.ceil(half_raw / 2) * 2 if half_raw > 0 else 0
        half_s   = str(int(half)) if half == int(half) else f"{half:.1f}"

        for color in ('синій', 'червоний'):
            out.append({
                'original':   f"(авто +ізол) утеплювач ф{ins_dia} {color}",
                'normalized': f"Утеплювач ламін. для труб ф {ins_dia}х6 {color} PLM",
                'qty':        f"{half_s} м",
                'category':   'insulation',
                'type':       'утеплювач',
                'dia':        [ins_dia],
                'section':    п.get('section', ''),
            })
    return out


def _push_outlets(п: dict, build_qa_fn) -> list:
    """Скільки трубних виходів має PUSH-фітинг."""
    qa  = п.get('_qa') or build_qa_fn(п)
    п['_qa'] = qa
    typ  = qa.get('type')
    text = f"{п.get('normalized', '')} {п.get('original', '')}"
    g    = re.search(r'(\d{2})\s*[хx×]\s*(\d{2})(?:\s*[хx×]\s*(\d{2}))?', text)
    dims = [int(x) for x in g.groups() if x] if g else list(qa.get('dia') or [])
    has_thread = bool(qa.get('thread')) or bool(
        re.search(r'мрз|мрв|рз|вр|різьб', text.lower()))

    if typ == 'трійник':
        outs = dims if len(dims) == 3 else (dims * 3)[:3] if dims else []
    elif typ == 'коліно':
        outs = dims if len(dims) == 2 else (dims * 2)[:2] if dims else []
    elif typ in ('муфта', 'перехід'):
        outs = dims[:1] if has_thread else (
            dims if len(dims) == 2 else (dims * 2)[:2] if dims else [])
    elif typ == 'заглушка':
        outs = dims[:1]
    else:
        outs = []
    return outs


def expand_push_sleeves(позиції: list[dict], build_qa_fn) -> list[dict]:
    """Автоматично додає гільзи до PUSH-фітингів."""
    if any(
        'гільз' in (п.get('original', '') + п.get('normalized', '')).lower()
        and (п.get('_qa') or build_qa_fn(п)).get('type') == 'гільза'
        for п in позиції
    ):
        return позиції

    sleeves: dict[int, int] = {}
    for п in позиції:
        if п.get('category') != 'push_systems':
            continue
        outs = _push_outlets(п, build_qa_fn)
        if not outs:
            continue
        n_fit = int(_qty_num(п.get('qty')) or 1)
        for d in outs:
            sleeves[d] = sleeves.get(d, 0) + n_fit

    for d in sorted(sleeves):
        позиції.append({
            'original':   f"(авто) гільзи ф{d} до PUSH-фітингів",
            'normalized': f"Гільза натяжна ф {d} PUSH",
            'qty':        f"{sleeves[d]} шт",
            'category':   'push_systems',
            'type':       'гільза',
            'dia':        [d],
        })
    return позиції
