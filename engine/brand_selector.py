"""
engine/brand_selector.py — Покроковий вибір виробника по групах товарів.

Флоу:
  1. process_batch викликає ask_brand_selection() після OCR
  2. Менеджер бачить 2 кнопки: [✅ Дефолтний пошук] / [🏭 Вибрати виробників]
  3. При виборі "вибрати" — покроково питає виробника для кожної групи
  4. Після вибору — позиції доповнюються brand_map і запускається find_items

Групи та їх виробники визначені в CATEGORY_BRANDS нижче.
"""

from __future__ import annotations
import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ─── Групи товарів → виробники (кнопки) ───────────────────────────────────────

CATEGORY_BRANDS: dict[str, dict] = {
    'plastic_ppr': {
        'label': '🔧 PPR пайка',
        'brands': [
            ('Ekoplastik',  'ekoplastik'),
            ('ASG',         'asg'),
            ('RAFTEC',      'raftec'),
            ('PLM',         'plm'),
            ('ECO PPR',     'eco'),
            ('FV Plast',    'fv plast'),
            ('KAN',         'kan'),
            ('Дефолт',      ''),
        ],
    },
    'sewage': {
        'label': '🚿 Каналізація',
        'brands': [
            ('OSTENDORF',   'ostendorf'),
            ('ASG',         'asg'),
            ('VALROM',      'valrom'),
            ('PLM',         'plm'),
            ('Дефолт',      ''),
        ],
    },
    'push_systems': {
        'label': '⚡ PUSH/PEX',
        'brands': [
            ('RAFTEC',      'raftec'),
            ('REHAU',       'rehau'),
            ('FADO',        'fado'),
            ('KAN',         'kan'),
            ('Uponor',      'uponor'),
            ('Дефолт',      ''),
        ],
    },
    'shutoff_valves': {
        'label': '🔴 Запірна арматура',
        'brands': [
            ('RAFTEC',      'raftec'),
            ('ASG',         'asg'),
            ('HLV',         'hlv'),
            ('LEXLINE',     'lexline'),
            ('ECO',         'eco'),
            ('Дефолт',      ''),
        ],
    },
    'adapters_reducers': {
        'label': '🔩 Перехідники',
        'brands': [
            ('RAFTEC GOLD', 'raftec'),
            ('LEXLINE',     'lexline'),
            ('УЗКМ',        'узкм'),
            ('HLV',         'hlv'),
            ('Мідь',        'мідь'),
            ('Дефолт',      ''),
        ],
    },
    'radiators_radiatorsvalve': {
        'label': '🌡️ Радіатори',
        'brands': [
            ('MIRADO',      'mirado'),
            ('PURMO',       'purmo'),
            ('KORAD',       'korad'),
            ('IDMAR',       'idmar'),
            ('HIDROS',      'hidros'),
            ('Дефолт',      ''),
        ],
    },
    'underfloor_heating': {
        'label': '🏠 Тепла підлога',
        'brands': [
            ('RAFTEC',      'raftec'),
            ('PLM',         'plm'),
            ('REHAU',       'rehau'),
            ('KAN',         'kan'),
            ('Danfoss',     'danfoss'),
            ('Дефолт',      ''),
        ],
    },
    'insulation': {
        'label': '🧱 Утеплювач',
        'brands': [
            ('K-FLEX',      'k-flex'),
            ('PLM',         'plm'),
            ('Теплоізол',   'теплоізол'),
            ('SANFLEX',     'sanflex'),
            ('Дефолт',      ''),
        ],
    },
    'heating': {
        'label': '🔥 Опалення',
        'brands': [
            ('ESBE',        'esbe'),
            ('CALEFFI',     'caleffi'),
            ('HONEYWELL',   'honeywell'),
            ('HERZ',        'herz'),
            ('AFRISO',      'afriso'),
            ('Дефолт',      ''),
        ],
    },
    'pumps': {
        'label': '💧 Насоси',
        'brands': [
            ('Lider',       'lider'),
            ('WILO',        'wilo'),
            ('TATRA',       'tatra'),
            ('Grundfos',    'grundfos'),
            ('Дефолт',      ''),
        ],
    },
    'filtration': {
        'label': '🔬 Фільтрація',
        'brands': [
            ('ECOSOFT',     'ecosoft'),
            ('Filtrons',    'filtrons'),
            ('BWT',         'bwt'),
            ('Дефолт',      ''),
        ],
    },
    'metal_plastic': {
        'label': '🔧 Металопластик',
        'brands': [
            ('RAFTEC',      'raftec'),
            ('FADO',        'fado'),
            ('KAN',         'kan'),
            ('HLV',         'hlv'),
            ('Дефолт',      ''),
        ],
    },
    'water_meters': {
        'label': '📊 Лічильники',
        'brands': [
            ('Ecostar',     'ecostar'),
            ('GIDROTEK',    'gidrotek'),
            ('Дефолт',      ''),
        ],
    },
    'safety_valves': {
        'label': '⚙️ Арматура безпеки',
        'brands': [
            ('RAFTEC',      'raftec'),
            ('HERZ',        'herz'),
            ('Flamco',      'flamco'),
            ('CALEFFI',     'caleffi'),
            ('PLM',         'plm'),
            ('Дефолт',      ''),
        ],
    },
    'fasteners_sealants': {
        'label': '🔨 Кріплення/Герметики',
        'brands': [
            ('Walraven',    'walraven'),
            ('RAFTEC',      'raftec'),
            ('UNIPAK',      'unipak'),
            ('PLM',         'plm'),
            ('Дефолт',      ''),
        ],
    },
}

# Категорії які НЕ потребують вибору виробника (вибір недоречний)
SKIP_CATEGORIES = {
    'not_ours', 'other', 'sanitary_ware', 'mixers_faucets',
    'boilers', 'water_heaters', 'siphons_fittings', 'hoses',
    'towel_warmers', 'automation',
}

# ─── Стан вибору виробника: chat_id → BrandSelectionState ─────────────────────

_states: dict[int, 'BrandSelectionState'] = {}


class BrandSelectionState:
    """Стан покрокового вибору виробника для одного замовлення."""

    def __init__(self, chat_id: int, позиції: list[dict],
                 items: list[dict], caption: str,
                 callback_fn, status_msg_id: int):
        self.chat_id       = chat_id
        self.позиції       = позиції       # нормалізовані позиції
        self.items         = items         # оригінальні items батчу
        self.caption       = caption
        self.callback_fn   = callback_fn   # функція яку викликати після вибору
        self.status_msg_id = status_msg_id

        # Визначаємо групи що є в замовленні
        self.groups_needed = self._detect_groups()
        self.group_queue   = list(self.groups_needed.keys())  # черга груп
        self.chosen        : dict[str, str] = {}              # cat → brand_key

    def _detect_groups(self) -> dict[str, list[dict]]:
        """Знаходить групи товарів що є в замовленні і мають виробників."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for п in self.позиції:
            cat = п.get('category', 'other')
            if cat in SKIP_CATEGORIES:
                continue
            if cat not in CATEGORY_BRANDS:
                continue
            groups[cat].append(п)
        return dict(groups)

    def current_group(self) -> str | None:
        """Поточна група для вибору."""
        return self.group_queue[0] if self.group_queue else None

    def apply_choice(self, cat: str, brand_key: str):
        """Застосовує вибір виробника для групи."""
        self.chosen[cat] = brand_key
        if cat in self.group_queue:
            self.group_queue.remove(cat)

    def is_done(self) -> bool:
        return len(self.group_queue) == 0

    def build_brand_map(self) -> dict[str, list[str]]:
        """Будує brand_map для find_items з вибраних виробників."""
        from engine.search import BRAND_TOKENS
        brand_map: dict[str, list[str]] = {}
        for cat, brand_key in self.chosen.items():
            if not brand_key:
                continue
            tokens = BRAND_TOKENS.get(brand_key.lower())
            if tokens:
                brand_map[cat] = tokens
            else:
                brand_map[cat] = [brand_key]
        return brand_map

    def count_per_group(self) -> dict[str, int]:
        return {cat: len(items) for cat, items in self.groups_needed.items()}


# ─── Публічний API ──────────────────────────────────────────────────────────────

def start_brand_selection(chat_id: int, позиції: list[dict],
                           items: list[dict], caption: str,
                           callback_fn, status_msg_id: int,
                           bot, _state: dict = None) -> None:
    """
    Запускає діалог вибору виробника.
    Якщо в замовленні немає груп з виробниками — одразу викликає callback_fn.
    """
    bs = BrandSelectionState(
        chat_id, позиції, items, caption, callback_fn, status_msg_id)
    bs._bot   = bot
    bs._state = _state or {}

    if not bs.groups_needed:
        # Немає груп для вибору — одразу пускаємо з порожнім brand_map
        _call_callback(bs, {})
        return

    _states[chat_id] = bs
    _send_initial_question(chat_id, bs, bot)


def handle_callback(chat_id: int, data: str, bot) -> bool:
    """
    Обробляє callback від кнопок вибору виробника.
    Повертає True якщо callback належить цьому модулю.
    """
    if not data.startswith('bs_'):
        return False

    state = _states.get(chat_id)
    if not state:
        return True

    if data == 'bs_default':
        # Дефолтний пошук — без вибору виробників
        _finish(chat_id, state, bot)
        return True

    if data == 'bs_pick':
        # Починаємо покроковий вибір
        _send_next_group(chat_id, state, bot)
        return True

    if data.startswith('bs_brand_'):
        # bs_brand_CATEGORY__brandkey
        payload = data[9:]   # CATEGORY__brandkey
        if '__' in payload:
            cat, brand_key = payload.split('__', 1)
            state.apply_choice(cat, brand_key)
            if state.is_done():
                _finish(chat_id, state, bot)
            else:
                _send_next_group(chat_id, state, bot)
        return True

    return False


def cancel(chat_id: int) -> None:
    """Скасовує активну сесію вибору (при /стоп)."""
    _states.pop(chat_id, None)


def has_active(chat_id: int) -> bool:
    return chat_id in _states


# ─── Внутрішні функції ──────────────────────────────────────────────────────────

def _send_initial_question(chat_id: int, state: BrandSelectionState, bot) -> None:
    """Надсилає перше питання: дефолт чи вибрати виробників."""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    counts   = state.count_per_group()
    n_groups = len(counts)
    n_items  = sum(counts.values())

    lines = [f"📋 *Замовлення: {n_items} позицій, {n_groups} груп*\n"]
    for cat, cnt in counts.items():
        info = CATEGORY_BRANDS.get(cat, {})
        label = info.get('label', cat)
        lines.append(f"  • {label} — {cnt} шт.")

    lines.append("\n*Оберіть режим пошуку:*")

    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(
        InlineKeyboardButton(
            "✅ Дефолтний пошук (пріоритетний виробник)",
            callback_data="bs_default"),
        InlineKeyboardButton(
            "🏭 Вибрати виробника для кожної групи",
            callback_data="bs_pick"),
    )

    try:
        bot.edit_message_text(
            '\n'.join(lines), chat_id, state.status_msg_id,
            parse_mode="Markdown", reply_markup=mk)
    except Exception:
        bot.send_message(
            chat_id, '\n'.join(lines),
            parse_mode="Markdown", reply_markup=mk)


def _send_next_group(chat_id: int, state: BrandSelectionState, bot) -> None:
    """Надсилає кнопки вибору виробника для наступної групи."""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    cat = state.current_group()
    if not cat:
        _finish(chat_id, state, bot)
        return

    info   = CATEGORY_BRANDS[cat]
    label  = info['label']
    brands = info['brands']
    n_items = len(state.groups_needed[cat])
    done    = len(state.chosen)
    total   = len(state.groups_needed)

    # Приклади товарів цієї групи
    examples = [п.get('normalized', п.get('original', ''))[:40]
                for п in state.groups_needed[cat][:3]]
    ex_text = '\n'.join(f"  _{e}_" for e in examples)

    text = (
        f"*Крок {done+1}/{total}:* {label}\n"
        f"Позицій: {n_items}\n{ex_text}\n\n"
        f"*Оберіть виробника:*"
    )

    mk = InlineKeyboardMarkup(row_width=2)
    for brand_label, brand_key in brands:
        cb = f"bs_brand_{cat}__{brand_key}"
        mk.add(InlineKeyboardButton(brand_label, callback_data=cb))

    try:
        bot.edit_message_text(
            text, chat_id, state.status_msg_id,
            parse_mode="Markdown", reply_markup=mk)
    except Exception:
        bot.send_message(
            chat_id, text,
            parse_mode="Markdown", reply_markup=mk)


def _call_callback(state: BrandSelectionState, brand_map: dict) -> None:
    """Викликає callback з усіма потрібними параметрами."""
    import inspect
    sig = inspect.signature(state.callback_fn)
    params = list(sig.parameters.keys())
    try:
        if 'bot' in params and '_state' in params:
            state.callback_fn(
                state.chat_id, state.позиції, state.items,
                state.caption, brand_map, state.status_msg_id,
                bot=state._bot, _state=state._state)
        elif 'bot' in params:
            state.callback_fn(
                state.chat_id, state.позиції, state.items,
                state.caption, brand_map, state.status_msg_id,
                bot=state._bot)
        else:
            state.callback_fn(
                state.chat_id, state.позиції, state.items,
                state.caption, brand_map, state.status_msg_id)
    except Exception as e:
        print(f"❌ brand_selector callback: {e}", flush=True)
        try:
            state._bot.send_message(state.chat_id, f"❌ Помилка пошуку: {e}")
        except Exception:
            pass


def _finish(chat_id: int, state: BrandSelectionState, bot) -> None:
    """Завершує вибір і запускає пошук."""
    brand_map = state.build_brand_map()

    msg = (
        f"✅ Виробники обрані:\n" +
        "\n".join(
            f"  • {CATEGORY_BRANDS.get(cat, {}).get('label', cat)}: *{bk[0]}*"
            for cat, bk in brand_map.items()
        ) + "\n\n🔍 Шукаю..."
        if brand_map else "🔍 Шукаю з дефолтними виробниками..."
    )

    try:
        bot.edit_message_text(msg, chat_id, state.status_msg_id,
                              parse_mode="Markdown")
    except Exception:
        pass

    _states.pop(chat_id, None)
    _call_callback(state, brand_map)


def inject_brand_map_to_positions(
        позиції: list[dict], brand_map: dict[str, list[str]],
        global_caption_brand_map: dict) -> None:
    """
    Вставляє brand_map в кожну позицію.
    Категорія-специфічний brand_map має пріоритет над глобальним (caption).
    """
    for п in позиції:
        cat = п.get('category', 'other')
        existing = dict(global_caption_brand_map)   # копія глобального

        if cat in brand_map:
            # Вибраний виробник для цієї категорії → жорстке перевизначення
            existing[cat]      = brand_map[cat]
            existing['_global'] = brand_map[cat]    # також глобально для цієї позиції

        п['_brand_map'] = existing
