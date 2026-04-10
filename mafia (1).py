"""
╔══════════════════════════════════════════════════════════════╗
║          MAFIYA BOT  —  Kuchaytirilgan versiya v2.2          ║
║  Barcha xatolar tuzatildi, logika kuchaytirildi              ║
║  Qo'shimcha: /add_balance, /remove_balance,                 ║
║  /add_premium, /remove_premium, /premium_list, /daily       ║
╚══════════════════════════════════════════════════════════════╝
"""

import logging
import random
import json
import os
import time
import threading
from datetime import datetime
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# =====================================================================
#  SOZLAMALAR  —  faqat shu joyni o'zgartiring
# =====================================================================
API_TOKEN      = "8166885443:AAGBvsNW5guy66GjRyNmLWvMkL4mbi9a8kw"
BOT_USERNAME   = 'Mafiyo_bot'
ADMIN_IDS_INIT = {8172404961}
# =====================================================================

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ── Bot ──────────────────────────────────────────────────────────────
bot = telebot.TeleBot(API_TOKEN, parse_mode=None)

# ── Ma'lumot fayli ───────────────────────────────────────────────────
DATA_FILE = 'data.json'
data_lock = threading.Lock()

def _load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Ma'lumot yuklashda xato: {e}")
    return {}

_raw = _load_data()

admin_ids:     set  = set(_raw.get('admin_ids',  list(ADMIN_IDS_INIT)))
subscribers:   set  = set(_raw.get('subscribers', []))
user_profiles: dict = _raw.get('user_profiles', {})
chat_list:     dict = _raw.get('chat_list', {})

active_games:  dict = {}   # chat_id -> Game
user_game_map: dict = {}   # user_id -> chat_id
game_lock = threading.Lock()

SHOP_ITEMS = {
    'Kuchaytirish':  50,
    'Omad toshchasi': 80,
    'Ikki ovoz':     120,
    'Maxfiy qalqon': 150,
}
bonus_settings     = _raw.get('bonus_settings', {"villager": 10, "mafia": 10})
SUBSCRIPTION_PRICE = 100
MIN_PLAYERS        = 5

# Kunlik bonus olganlarni saqlash
daily_bonus_taken = set()


# =====================================================================
#  Ma'lumot yordamchilari
# =====================================================================

def save_data():
    with data_lock:
        try:
            tmp = DATA_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({
                    'admin_ids':      list(admin_ids),
                    'user_profiles':  user_profiles,
                    'chat_list':      chat_list,
                    'subscribers':    list(subscribers),
                    'bonus_settings': bonus_settings,
                }, f, ensure_ascii=False, indent=4)
            os.replace(tmp, DATA_FILE)
        except IOError as e:
            logger.error(f"Ma'lumot saqlashda xato: {e}")


def get_profile(user_id, username=None):
    uid = str(user_id)
    with data_lock:
        if uid not in user_profiles:
            user_profiles[uid] = {
                "username":    username or f"user{user_id}",
                "balance":     0,
                "points":      0,
                "roses":       0,
                "donation":    0,
                "games_played": 0,
                "games_won":   0,
            }
        elif username:
            user_profiles[uid]["username"] = username
    save_data()
    return user_profiles[str(user_id)]


def find_profile(identifier: str):
    """ID yoki username bo'yicha profil topish."""
    ident = identifier.lstrip('@')
    for uid, p in user_profiles.items():
        if uid == ident or p.get("username", "").lstrip('@') == ident:
            return uid, p
    return None, None


def update_chat_list(chat):
    cid = str(chat.id)
    if cid not in chat_list:
        chat_list[cid] = {
            "title":  chat.title or "Nomsiz",
            "link":   None,
            "status": "tekshirilmagan",
            "score":  0,
        }
        save_data()


def safe_send(chat_id, text, **kwargs):
    """Xavfsiz xabar yuborish — xato bo'lsa log yozadi."""
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except telebot.apihelper.ApiTelegramException as e:
        logger.warning(f"Xabar yuborilmadi {chat_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Kutilmagan xato {chat_id}: {e}")
        return None


def safe_edit(chat_id, msg_id, text, **kwargs):
    """Xavfsiz xabarni tahrirlash."""
    try:
        return bot.edit_message_text(text, chat_id, msg_id, **kwargs)
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Tahrirlash xatosi {chat_id}/{msg_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Kutilmagan xato tahrirlashda: {e}")
        return None


def answer_cb(call, text="", alert=False):
    """Callback so'roviga javob berish."""
    try:
        bot.answer_callback_query(call.id, text, show_alert=alert)
    except Exception:
        pass


# =====================================================================
#  Rollar
# =====================================================================

class Role:
    def __init__(self, name, description, abilities):
        self.name        = name
        self.description = description
        self.abilities   = abilities

    def has_night_ability(self):
        return any(ab for ab in self.abilities if ab != 'lead')


STANDARD_ROLES = {
    'Tinch fuqaro': Role('Tinch fuqaro', "Maxsus qobiliyati yo'q. Ovoz berib mafiyani toping!", {}),
    'Mafiya':       Role('Mafiya',       "Kechasi bir kishini o'ldiring.",                      {'kill': True}),
    'Mafiya doni':  Role('Mafiya doni',  "Mafiyani boshqaring va o'ldiring.",                   {'kill': True, 'lead': True}),
    'Doktor':       Role('Doktor',       "Kechasi bir kishini o'limdan qutqaring.",              {'save': True}),
    'Komissar':     Role('Komissar',     "Kechasi biror kishining rolini bilib oling.",          {'check': True}),
}

ABILITY_META = {
    'kill':  ('O\'ldirish',   'kill'),
    'save':  ('Qutqarish',    'save'),
    'check': ('Tekshirish',   'check'),
    'block': ('Bloklash',     'block'),
    'boost': ('Kuchaytirish', 'boost'),
    'spy':   ('Josuslik',     'spy'),
    'lead':  ('Boshqarish',   'lead'),
}

ABILITY_EMOJIS = {
    'kill':  '🔪',
    'save':  '💉',
    'check': '🔍',
    'block': '🚫',
    'boost': '💪',
    'spy':   '🕵',
    'lead':  '👑',
}

ROLE_EMOJIS = {
    'Tinch fuqaro': '👤',
    'Mafiya':       '🕶',
    'Mafiya doni':  '🎩',
    'Doktor':       '👨‍⚕️',
    'Komissar':     '🕵',
}


# =====================================================================
#  O'yin klassi
# =====================================================================

class Game:
    def __init__(self, chat_id):
        self.chat_id       = chat_id
        self.players       = {}
        self.phase         = 'waiting'
        self.votes         = {}
        self.night_actions = {}
        self.started       = False
        self.round         = 0
        self._lobby_msg_id = None
        self._voted_this_round = set()
        self._acted_this_night = set()
        self.created_at    = time.time()

    def add_player(self, uid: int, username: str) -> bool:
        if uid in self.players:
            return False
        self.players[uid] = {
            'username': username,
            'role':     None,
            'alive':    True,
            'currency': 0,
        }
        return True

    def remove_player(self, uid: int) -> bool:
        if uid in self.players:
            del self.players[uid]
            return True
        return False

    def alive_list(self):
        return [(uid, d) for uid, d in self.players.items() if d['alive']]

    def alive_count(self) -> int:
        return sum(1 for d in self.players.values() if d['alive'])

    def dead_list(self):
        return [(uid, d) for uid, d in self.players.items() if not d['alive']]

    def assign_roles(self):
        ids = list(self.players)
        random.shuffle(ids)
        n = len(ids)

        if n >= 9:
            pool = ['Mafiya doni', 'Mafiya', 'Mafiya', 'Doktor', 'Komissar'] + \
                   ['Tinch fuqaro'] * (n - 5)
        elif n >= 7:
            pool = ['Mafiya doni', 'Mafiya', 'Doktor', 'Komissar'] + \
                   ['Tinch fuqaro'] * (n - 4)
        elif n >= 5:
            pool = ['Mafiya doni', 'Doktor', 'Komissar'] + \
                   ['Tinch fuqaro'] * (n - 3)
        elif n >= 3:
            pool = ['Mafiya'] + ['Tinch fuqaro'] * (n - 1)
        else:
            pool = ['Tinch fuqaro'] * n

        random.shuffle(pool)
        for uid, rname in zip(ids, pool):
            self.players[uid]['role'] = STANDARD_ROLES[rname]

        self.started = True
        self.phase   = 'day'
        self.round   = 1

    def tally_votes(self):
        count = {}
        for _, target in self.votes.items():
            count[target] = count.get(target, 0) + 1
        if not count:
            return None
        max_votes = max(count.values())
        tops = [u for u, c in count.items() if c == max_votes]
        return tops[0] if len(tops) == 1 else None

    def vote_progress(self) -> str:
        alive = self.alive_count()
        voted = len(self.votes)
        return f"{voted}/{alive}"

    def process_night(self) -> dict:
        eff = dict(self.night_actions)

        blocked = {a['target'] for a in self.night_actions.values() if a['action'] == 'block'}
        for actor in list(eff):
            if actor in blocked:
                del eff[actor]

        kills = [a['target'] for a in eff.values() if a['action'] == 'kill']
        kill_tgt = max(set(kills), key=kills.count) if kills else None

        saved = next((a['target'] for a in eff.values() if a['action'] == 'save'), None)

        checks = {}
        for actor, a in eff.items():
            if a['action'] == 'check':
                tid = a['target']
                checks[actor] = (tid, self.players[tid]['role'].name)

        spy_info = {}
        for actor, a in eff.items():
            if a['action'] == 'spy':
                tid = a['target']
                t_act = self.night_actions.get(tid, {}).get('action', 'hech narsa')
                spy_info[actor] = (tid, t_act)

        boosted = [a['target'] for a in eff.values() if a['action'] == 'boost']

        result = {
            'killed':   None,
            'saved':    saved,
            'checks':   checks,
            'boosted':  boosted,
            'spy_info': spy_info,
        }

        if kill_tgt is not None and kill_tgt != saved:
            self.players[kill_tgt]['alive'] = False
            result['killed'] = kill_tgt

        self.night_actions = {}
        self._acted_this_night = set()
        self.round += 1
        return result

    def check_winner(self):
        mafia_count = sum(1 for p in self.players.values()
                          if p['alive'] and p['role'].name in ('Mafiya', 'Mafiya doni'))
        others_count = sum(1 for p in self.players.values()
                           if p['alive'] and p['role'].name not in ('Mafiya', 'Mafiya doni'))

        if mafia_count == 0:
            return 'Tinch fuqarolar'
        if mafia_count >= others_count:
            return 'Mafiya'
        return None

    def give_bonuses(self, winner: str):
        for uid, d in self.players.items():
            is_mafia = d['role'].name in ('Mafiya', 'Mafiya doni')
            won = (winner == 'Tinch fuqarolar' and not is_mafia) or \
                  (winner == 'Mafiya' and is_mafia)
            p2 = user_profiles.get(str(uid))
            if p2:
                p2['games_played'] = p2.get('games_played', 0) + 1
                if won:
                    key = "mafia" if is_mafia else "villager"
                    amount = bonus_settings.get(key, 10)
                    d['currency'] += amount
                    p2['balance'] += amount
                    p2['games_won'] = p2.get('games_won', 0) + 1
        save_data()


# =====================================================================
#  O'yinni tozalash
# =====================================================================

def cleanup_game(chat_id: int):
    with game_lock:
        if chat_id in active_games:
            for uid in list(active_games[chat_id].players):
                user_game_map.pop(uid, None)
            del active_games[chat_id]
    logger.info(f"O'yin tozalandi: {chat_id}")


# =====================================================================
#  Keyboard fabrikasi
# =====================================================================

def kb_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎮 O'yin",     callback_data="menu_game"),
        InlineKeyboardButton("👤 Profil",    callback_data="menu_profile"),
        InlineKeyboardButton("🛒 Do'kon",    callback_data="menu_shop"),
        InlineKeyboardButton("🏆 Reyting",   callback_data="menu_top"),
        InlineKeyboardButton("📋 Yordam",    callback_data="menu_help"),
        InlineKeyboardButton("⚙️ Sozlamalar", callback_data="menu_settings"),
    )
    return kb


def kb_game_lobby(chat_id: int, count: int) -> InlineKeyboardMarkup:
    join_link = f"https://t.me/{BOT_USERNAME}?start=join_{chat_id}"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton(f"✅ Qo'shilish  ({count} kishi)", url=join_link))
    kb.add(
        InlineKeyboardButton("🚀 O'yinni boshlash", callback_data=f"startgame_{chat_id}"),
        InlineKeyboardButton("❌ Bekor qilish",      callback_data=f"cancelgame_{chat_id}"),
    )
    return kb


def kb_vote(game: Game, voter_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    for uid, d in game.alive_list():
        if uid == voter_id:
            continue
        kb.add(InlineKeyboardButton(f"👤 {d['username']}", callback_data=f"vote_{uid}"))
    kb.add(InlineKeyboardButton("⏭ O'tkazib yuborish", callback_data="vote_skip"))
    return kb


def kb_night_actions(game: Game, actor_id: int) -> InlineKeyboardMarkup:
    role = game.players[actor_id]['role']
    kb = InlineKeyboardMarkup(row_width=2)
    for ab in role.abilities:
        if ab == 'lead':
            continue
        label = ABILITY_META[ab][0]
        emoji = ABILITY_EMOJIS.get(ab, '▶')
        kb.add(InlineKeyboardButton(f"{emoji} {label}", callback_data=f"pick_action_{ab}"))
    kb.add(InlineKeyboardButton("💤 O'tkazib yuborish", callback_data="action_skip"))
    return kb


def kb_pick_target(game: Game, actor_id: int, action: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    for uid, d in game.alive_list():
        if uid == actor_id and action not in ('boost', 'save'):
            continue
        kb.add(InlineKeyboardButton(f"👤 {d['username']}", callback_data=f"do_{action}_{uid}"))
    kb.add(InlineKeyboardButton("◀ Orqaga", callback_data="night_back"))
    return kb


def kb_shop() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for item, price in SHOP_ITEMS.items():
        kb.add(InlineKeyboardButton(f"{item}  —  {price} 💰", callback_data=f"buyitem_{item}"))
    kb.add(InlineKeyboardButton("◀ Ortga", callback_data="back_main"))
    return kb


def kb_top_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⭐ Ballar",    callback_data="top_points"),
        InlineKeyboardButton("💰 Balans",    callback_data="top_balance"),
        InlineKeyboardButton("🏅 G'alabalar", callback_data="top_wins"),
        InlineKeyboardButton("🌹 Atirgullar", callback_data="top_roses"),
        InlineKeyboardButton("◀ Ortga",      callback_data="back_main"),
    )
    return kb


def kb_game_control(chat_id: int, phase: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    if phase == 'day':
        kb.add(
            InlineKeyboardButton("🗳 Ovoz berish",      callback_data=f"open_vote_{chat_id}"),
            InlineKeyboardButton("⏭ Ovozni yakunlash", callback_data=f"endvote_{chat_id}"),
        )
    else:
        kb.add(
            InlineKeyboardButton("🌙 Tungi harakat",    callback_data=f"open_night_{chat_id}"),
            InlineKeyboardButton("⏭ Tunni yakunlash",  callback_data=f"endnight_{chat_id}"),
        )
    kb.add(InlineKeyboardButton("🔄 Yangilash", callback_data=f"refresh_game_{chat_id}"))
    return kb


# =====================================================================
#  Bot buyruqlarini o'rnatish
# =====================================================================

def set_bot_commands():
    cmds = [
        telebot.types.BotCommand("start",      "Botni ishga tushirish / bosh menyu"),
        telebot.types.BotCommand("help",       "Yordam va buyruqlar"),
        telebot.types.BotCommand("newgame",    "Yangi o'yin yaratish (guruhda)"),
        telebot.types.BotCommand("join",       "O'yinga qo'shilish (guruhda)"),
        telebot.types.BotCommand("startgame",  "O'yinni boshlash"),
        telebot.types.BotCommand("vote",       "Ovoz berish menyusi"),
        telebot.types.BotCommand("endvote",    "Ovozni yakunlash"),
        telebot.types.BotCommand("action",     "Tungi harakat menyusi"),
        telebot.types.BotCommand("endnight",   "Tungi raundni tugatish"),
        telebot.types.BotCommand("game",       "Joriy o'yin holati"),
        telebot.types.BotCommand("leave",      "O'yindan chiqish"),
        telebot.types.BotCommand("profile",    "Profilni ko'rish"),
        telebot.types.BotCommand("balance",    "Balansni tekshirish"),
        telebot.types.BotCommand("shop",       "Do'kon"),
        telebot.types.BotCommand("top",        "Reyting"),
        telebot.types.BotCommand("case",       "Kesh ochish"),
        telebot.types.BotCommand("rose",       "Atirgul yuborish"),
        telebot.types.BotCommand("give",       "Valyuta o'tkazish"),
        telebot.types.BotCommand("feedback",   "Fikr bildirish"),
        telebot.types.BotCommand("id",         "Chat yoki user ID"),
        telebot.types.BotCommand("daily",      "Kunlik bonus (premium)"),
        telebot.types.BotCommand("add_balance", "Admin: balans qo'shish"),
        telebot.types.BotCommand("remove_balance", "Admin: balans yechish"),
        telebot.types.BotCommand("add_premium", "Admin: premium berish"),
        telebot.types.BotCommand("remove_premium", "Admin: premium olib tashlash"),
        telebot.types.BotCommand("premium_list", "Admin: premium ro'yxati"),
    ]
    try:
        bot.set_my_commands(cmds)
        logger.info("Bot buyruqlari o'rnatildi.")
    except Exception as e:
        logger.error(f"Buyruqlarni o'rnatishda xato: {e}")


# =====================================================================
#  /start
# =====================================================================

@bot.message_handler(commands=['start'])
def cmd_start(message):
    try:
        args = message.text.split()
        payload = args[1] if len(args) > 1 else ""
        uid = message.from_user.id
        uname = message.from_user.username or message.from_user.first_name
        get_profile(uid, uname)

        if payload.startswith("join_") and message.chat.type == "private":
            try:
                chat_id = int(payload.split("_", 1)[1])
            except (ValueError, IndexError):
                bot.reply_to(message, "❌ Noto'g'ri havola.")
                return

            with game_lock:
                game = active_games.get(chat_id)

            if not game:
                bot.reply_to(message, "❌ Bu chatda aktiv o'yin topilmadi.")
                return
            if game.started:
                bot.reply_to(message, "❌ O'yin allaqachon boshlangan.")
                return

            with game_lock:
                added = game.add_player(uid, uname)
                if added:
                    user_game_map[uid] = chat_id

            if added:
                count = len(game.players)
                safe_send(chat_id, f"✅ *{uname}* o'yinga qo'shildi! ({count} kishi)", parse_mode="Markdown")
                _refresh_lobby(chat_id)
                bot.reply_to(message, "✅ Muvaffaqiyatli qo'shildingiz!\nO'yin guruhda boshlanishini kuting.")
            else:
                bot.reply_to(message, "ℹ️ Siz allaqachon bu o'yindasisiz.")
            return

        if message.chat.type in ("group", "supergroup"):
            update_chat_list(message.chat)
            bot.reply_to(message, "🎲 Mafiya Botga xush kelibsiz!\nYangi o'yin: /newgame")
            return

        _send_main_menu(message.chat.id, uid)

    except Exception as e:
        logger.error(f"cmd_start xatosi: {e}", exc_info=True)


def _send_main_menu(chat_id: int, uid: int):
    p = get_profile(uid)
    is_sub = "💎 Premium" if uid in subscribers else "👤 Oddiy"
    text = (f"👋 Salom, *{p['username']}*!\n\n"
            f"💰 Balans: *{p['balance']}*  |  ⭐ Bal: *{p['points']}*\n"
            f"🌹 Atirgul: *{p['roses']}*  |  {is_sub}\n\n"
            "Menyudan tanlang 👇")
    safe_send(chat_id, text, reply_markup=kb_main_menu(), parse_mode="Markdown")


# =====================================================================
#  O'yin buyruqlari
# =====================================================================

@bot.message_handler(commands=['newgame'])
def cmd_newgame(message):
    try:
        if message.chat.type == "private":
            bot.reply_to(message, "❌ O'yin faqat guruh chatlarida yaratiladi.")
            return

        chat_id = message.chat.id
        update_chat_list(message.chat)

        with game_lock:
            if chat_id in active_games and not active_games[chat_id].started:
                bot.reply_to(message, "⚠️ O'yin allaqachon yaratilgan!\nKo'proq o'yinchi kuting yoki /stop bilan bekor qiling.")
                return
            active_games[chat_id] = Game(chat_id)

        join_link = f"https://t.me/{BOT_USERNAME}?start=join_{chat_id}"
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("✅ Qo'shilish (0 kishi)", url=join_link))
        kb.add(
            InlineKeyboardButton("🚀 O'yinni boshlash", callback_data=f"startgame_{chat_id}"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data=f"cancelgame_{chat_id}"),
        )
        sent = safe_send(chat_id,
                         "🎲 *Yangi Mafiya o'yini yaratildi!*\n\n"
                         f"👥 O'yinchilar: *0*\n"
                         f"Qo'shilish uchun tugmani bosing.\n"
                         f"Boshlash uchun kamida *{MIN_PLAYERS}* kishi kerak.",
                         reply_markup=kb, parse_mode="Markdown")
        if sent:
            active_games[chat_id]._lobby_msg_id = sent.message_id

    except Exception as e:
        logger.error(f"cmd_newgame xatosi: {e}", exc_info=True)


def _refresh_lobby(chat_id: int):
    with game_lock:
        game = active_games.get(chat_id)
    if not game:
        return

    count = len(game.players)
    names = "\n".join(f"  {i+1}. {d['username']}" for i, (_, d) in enumerate(game.players.items())) or "  _(hali hech kim)_"

    text = (f"🎲 *Mafiya o'yini — Kutish xonasi*\n\n"
            f"👥 O'yinchilar ({count}/{MIN_PLAYERS} min):\n{names}\n\n"
            f"{'✅ Boshlash mumkin!' if count >= MIN_PLAYERS else f'⏳ Yana {MIN_PLAYERS - count} kishi kerak'}")
    if game._lobby_msg_id:
        safe_edit(chat_id, game._lobby_msg_id, text,
                  reply_markup=kb_game_lobby(chat_id, count), parse_mode="Markdown")


@bot.message_handler(commands=['join'])
def cmd_join(message):
    try:
        if message.chat.type == "private":
            bot.reply_to(message, "Guruh chatida /join yozing yoki taklif havolasidan foydalaning.")
            return

        chat_id = message.chat.id
        uid = message.from_user.id
        uname = message.from_user.username or message.from_user.first_name

        with game_lock:
            game = active_games.get(chat_id)

        if not game:
            bot.reply_to(message, "❌ Aktiv o'yin yo'q. /newgame bilan yangi yarating.")
            return
        if game.started:
            bot.reply_to(message, "❌ O'yin boshlangan, qo'shilish mumkin emas.")
            return

        get_profile(uid, uname)

        with game_lock:
            added = game.add_player(uid, uname)
            if added:
                user_game_map[uid] = chat_id

        if added:
            _refresh_lobby(chat_id)
        else:
            bot.reply_to(message, "ℹ️ Siz allaqachon o'yindasiz.")

    except Exception as e:
        logger.error(f"cmd_join xatosi: {e}", exc_info=True)


@bot.message_handler(commands=['startgame'])
def cmd_startgame(message):
    try:
        if message.chat.type == "private":
            bot.reply_to(message, "Guruh chatida /startgame yozing.")
            return
        _do_startgame(message.chat.id)
    except Exception as e:
        logger.error(f"cmd_startgame xatosi: {e}", exc_info=True)


def _do_startgame(chat_id: int):
    with game_lock:
        game = active_games.get(chat_id)

    if not game:
        safe_send(chat_id, "❌ Aktiv o'yin yo'q.")
        return
    if game.started:
        safe_send(chat_id, "⚠️ O'yin allaqachon boshlangan.")
        return
    if len(game.players) < MIN_PLAYERS:
        safe_send(chat_id, f"⚠️ Kamida {MIN_PLAYERS} o'yinchi kerak.\nHozir: {len(game.players)} kishi")
        return

    game.assign_roles()

    failed = []
    for uid, d in game.players.items():
        role = d['role']
        emoji = ROLE_EMOJIS.get(role.name, '🎭')
        text = (f"{emoji} *Sizning rolingiz: {role.name}*\n"
                f"📖 _{role.description}_\n")
        if role.abilities:
            text += "\n🔧 *Qobiliyatlar:*\n"
            for ab in role.abilities:
                if ab == 'lead':
                    continue
                ae = ABILITY_EMOJIS.get(ab, '▶')
                lbl = ABILITY_META[ab][0]
                text += f"  {ae} {lbl}\n"
            text += "\n💡 Tunda /action orqali harakat qiling."
        else:
            text += "\n_(Maxsus qobiliyat yo'q — ovoz berish bilan g'alaba qozonish!)_"

        sent = safe_send(uid, text, parse_mode="Markdown")
        if not sent:
            failed.append(d['username'])

    mafia_ids = [uid for uid, d in game.players.items() if d['role'].name in ('Mafiya', 'Mafiya doni')]
    if len(mafia_ids) > 1:
        mnames = ", ".join(game.players[m]['username'] for m in mafia_ids)
        for m in mafia_ids:
            safe_send(m, f"🕶 *Sizning mafiya a'zolaringiz:*\n{mnames}", parse_mode="Markdown")

    player_list = "\n".join(f"  {i+1}. {d['username']}" for i, (_, d) in enumerate(game.players.items()))
    txt = (f"🎮 *O'yin boshlandi! ({len(game.players)} o'yinchi)*\n\n"
           f"👥 Ishtirokchilar:\n{player_list}\n\n"
           f"☀️ *1-kun. Muhokama qiling va ovoz bering!*")
    if failed:
        txt += f"\n\n⚠️ Quyidagilarga rol yetkazilmadi (bot bloklangan):\n" + ", ".join(failed)

    safe_send(chat_id, txt, reply_markup=kb_game_control(chat_id, 'day'), parse_mode="Markdown")
    logger.info(f"O'yin boshlandi: chat={chat_id}, o'yinchilar={len(game.players)}")


@bot.message_handler(commands=['vote'])
def cmd_vote(message):
    try:
        uid = message.from_user.id
        chat_id = (message.chat.id if message.chat.type != "private" else user_game_map.get(uid))

        if not chat_id:
            bot.reply_to(message, "❌ Siz hech qanday o'yinda emassiz.")
            return

        with game_lock:
            game = active_games.get(chat_id)

        if not game or not game.started:
            bot.reply_to(message, "❌ Aktiv o'yin yo'q.")
            return
        if game.phase != 'day':
            bot.reply_to(message, "⚠️ Ovoz berish faqat kunduz mumkin.")
            return
        if uid not in game.players or not game.players[uid]['alive']:
            bot.reply_to(message, "❌ Siz o'yinda yo'q yoki o'ldirilgansiz.")
            return
        if uid in game.votes:
            bot.reply_to(message, "ℹ️ Siz allaqachon ovoz berdingiz.")
            return

        msg = safe_send(uid, "🗳 *Kim haydalsin? Tanlang:*", reply_markup=kb_vote(game, uid), parse_mode="Markdown")
        if not msg:
            bot.reply_to(message, "❌ Botni shaxsiy chatda boshlang: @" + BOT_USERNAME)
        elif message.chat.type != "private":
            bot.reply_to(message, "✉️ Shaxsiy chatga ovoz berish menyusi yuborildi!")

    except Exception as e:
        logger.error(f"cmd_vote xatosi: {e}", exc_info=True)


@bot.message_handler(commands=['endvote'])
def cmd_endvote(message):
    try:
        if message.chat.type == "private":
            bot.reply_to(message, "/endvote faqat guruh chatida.")
            return
        uid = message.from_user.id
        if uid not in admin_ids:
            bot.reply_to(message, "❌ Faqat admin.")
            return
        _do_endvote(message.chat.id)
    except Exception as e:
        logger.error(f"cmd_endvote xatosi: {e}", exc_info=True)


def _do_endvote(chat_id: int):
    with game_lock:
        game = active_games.get(chat_id)

    if not game:
        safe_send(chat_id, "❌ Aktiv o'yin yo'q.")
        return
    if game.phase != 'day':
        safe_send(chat_id, "⚠️ Ovoz berish faol emas.")
        return

    elim = game.tally_votes()
    votes_snapshot = dict(game.votes)
    game.votes = {}
    game._voted_this_round = set()

    if votes_snapshot:
        result_lines = []
        for voter, target in votes_snapshot.items():
            vname = game.players.get(voter, {}).get('username', '?')
            tname = game.players.get(target, {}).get('username', '?')
            result_lines.append(f"  {vname} ➜ {tname}")
        vote_text = "\n".join(result_lines)
    else:
        vote_text = "  Hech kim ovoz bermadi."

    if elim:
        game.players[elim]['alive'] = False
        ename = game.players[elim]['username']
        erole = game.players[elim]['role'].name
        eemoji = ROLE_EMOJIS.get(erole, '🎭')
        msg = (f"🗳 *Ovoz berish yakunlandi!*\n\n"
               f"📊 Ovozlar:\n{vote_text}\n\n"
               f"❌ *{ename}* haydaldi.\n"
               f"{eemoji} Roli: *{erole}*")
        safe_send(elim, f"❌ Siz haydaldingiz! Sizning rolingiz: *{erole}*", parse_mode="Markdown")
    else:
        msg = (f"🗳 *Ovoz berish yakunlandi!*\n\n"
               f"📊 Ovozlar:\n{vote_text}\n\n"
               "⚖️ Tenglik — hech kim haydalmadi.")

    safe_send(chat_id, msg, parse_mode="Markdown")

    winner = game.check_winner()
    if winner:
        _finish_game(chat_id, game, winner)
    else:
        game.phase = 'night'
        safe_send(chat_id,
                  f"🌙 *{game.round}-tun! Maxsus rollar — shaxsiy chatda /action*\n"
                  f"Kimda qobiliyat yo'q — uxlang 😴",
                  reply_markup=kb_game_control(chat_id, 'night'),
                  parse_mode="Markdown")


@bot.message_handler(commands=['action'])
def cmd_action(message):
    try:
        uid = message.from_user.id
        chat_id = (user_game_map.get(uid) if message.chat.type == "private" else message.chat.id)

        if not chat_id:
            bot.reply_to(message, "❌ Siz hech qanday o'yinda emassiz.")
            return

        with game_lock:
            game = active_games.get(chat_id)

        if not game or not game.started:
            bot.reply_to(message, "❌ Aktiv o'yin yo'q.")
            return
        if game.phase != 'night':
            bot.reply_to(message, "⚠️ Tungi harakatlar faqat tunda.")
            return
        if uid not in game.players or not game.players[uid]['alive']:
            bot.reply_to(message, "❌ Siz o'yinda yo'q yoki o'ldirilgansiz.")
            return

        role = game.players[uid]['role']
        if not role.has_night_ability():
            bot.reply_to(message, "ℹ️ Sizning rolda tungi qobiliyat yo'q. Uxlang! 😴")
            return

        if uid in game.night_actions:
            bot.reply_to(message, "ℹ️ Siz allaqachon bu tunda harakat qildingiz.")
            return

        msg = safe_send(uid, f"🌙 *Tungi harakat — {role.name}*\nNima qilmoqchisiz?",
                        reply_markup=kb_night_actions(game, uid), parse_mode="Markdown")
        if not msg:
            bot.reply_to(message, "❌ Botni shaxsiy chatda boshlang: @" + BOT_USERNAME)
        elif message.chat.type != "private":
            bot.reply_to(message, "✉️ Shaxsiy chatga tungi harakat menyusi yuborildi!")

    except Exception as e:
        logger.error(f"cmd_action xatosi: {e}", exc_info=True)


@bot.message_handler(commands=['endnight'])
def cmd_endnight(message):
    try:
        if message.chat.type == "private":
            bot.reply_to(message, "/endnight faqat guruh chatida.")
            return
        uid = message.from_user.id
        if uid not in admin_ids:
            bot.reply_to(message, "❌ Faqat admin.")
            return
        _do_endnight(message.chat.id)
    except Exception as e:
        logger.error(f"cmd_endnight xatosi: {e}", exc_info=True)


def _do_endnight(chat_id: int):
    with game_lock:
        game = active_games.get(chat_id)

    if not game:
        safe_send(chat_id, "❌ Aktiv o'yin yo'q.")
        return
    if game.phase != 'night':
        safe_send(chat_id, "⚠️ Tungi raund faol emas.")
        return

    results = game.process_night()
    text = f"🌅 *Tun tugadi!*\n\n"

    if results['killed']:
        v = results['killed']
        vrole = game.players[v]['role'].name
        emoji = ROLE_EMOJIS.get(vrole, '🎭')
        text += f"🔪 *{game.players[v]['username']}* kechasi o'ldirildi.\n"
        text += f"   Roli: {emoji} *{vrole}*\n"
        safe_send(v, "💀 Kechasi siz o'ldirildingiz. O'yin davom etadi.", parse_mode="Markdown")
    else:
        if results.get('saved'):
            sv = results['saved']
            svname = game.players[sv]['username']
            text += f"💉 *{svname}* doktor tomonidan qutqarildi!\n"
        else:
            text += "😌 Tunda hech kim o'ldirilmadi.\n"

    for k_id, (tid, rname) in results['checks'].items():
        safe_send(k_id, f"🔍 *{game.players[tid]['username']}* roli:\n"
                        f"{ROLE_EMOJIS.get(rname,'🎭')} *{rname}*", parse_mode="Markdown")

    for buid in results['boosted']:
        safe_send(buid, "💪 Sizning kuchingiz oshirildi!")

    for spy_id, (tid, act) in results['spy_info'].items():
        safe_send(spy_id, f"🕵 *{game.players[tid]['username']}* kechasi `{act}` qildi.", parse_mode="Markdown")

    alive_names = ", ".join(d['username'] for _, d in game.alive_list())
    text += f"\n✅ Tirik ({game.alive_count()}): {alive_names}"

    winner = game.check_winner()
    if winner:
        _finish_game(chat_id, game, winner, extra=text)
    else:
        game.phase = 'day'
        text += f"\n\n☀️ *{game.round}-kun. Muhokama va ovoz berish!*"
        safe_send(chat_id, text, reply_markup=kb_game_control(chat_id, 'day'), parse_mode="Markdown")


def _finish_game(chat_id: int, game: Game, winner: str, extra: str = ""):
    game.give_bonuses(winner)
    game.phase = 'finished'

    emoji = "🏆" if winner == 'Tinch fuqarolar' else "🕶"
    text = (extra + "\n" if extra else "") + f"\n{emoji} *G'olib: {winner}!*\n\n"
    text += "👥 *Barcha rollar:*\n"

    for uid, d in game.players.items():
        rname = d['role'].name
        status = "✅" if d['alive'] else "💀"
        bonus = f" (+{d['currency']}💰)" if d['currency'] > 0 else ""
        text += f"  {status} {d['username']} — {ROLE_EMOJIS.get(rname,'🎭')} {rname}{bonus}\n"

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎮 Yangi o'yin", callback_data=f"newgame_request_{chat_id}"))

    safe_send(chat_id, text, reply_markup=kb, parse_mode="Markdown")
    cleanup_game(chat_id)
    logger.info(f"O'yin tugadi: chat={chat_id}, g'olib={winner}")


@bot.message_handler(commands=['game'])
def cmd_game(message):
    try:
        uid = message.from_user.id
        chat_id = (message.chat.id if message.chat.type != "private" else user_game_map.get(uid))

        if not chat_id:
            bot.reply_to(message, "❌ Siz hech qanday o'yinda emassiz.")
            return

        with game_lock:
            game = active_games.get(chat_id)

        if not game or not game.started:
            bot.reply_to(message, "❌ Aktiv o'yin yo'q.")
            return

        alive = game.alive_list()
        dead = game.dead_list()

        phase_text = {"day": "☀️ Kunduz", "night": "🌙 Tun"}.get(game.phase, game.phase)
        text = (f"🎮 *Raund {game.round} — {phase_text}*\n\n"
                f"✅ Tirik ({len(alive)}): " + ", ".join(d['username'] for _, d in alive))
        if dead:
            text += f"\n💀 O'lgan ({len(dead)}): " + ", ".join(d['username'] for _, d in dead)
        if game.phase == 'day' and game.votes:
            text += f"\n\n🗳 Ovozlar: {game.vote_progress()}"

        bot.reply_to(message, text, reply_markup=kb_game_control(chat_id, game.phase), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_game xatosi: {e}", exc_info=True)


@bot.message_handler(commands=['leave', 'exit'])
def cmd_leave(message):
    try:
        uid = message.from_user.id
        chat_id = (message.chat.id if message.chat.type != "private" else user_game_map.get(uid))

        if not chat_id:
            bot.reply_to(message, "❌ Siz hech qanday o'yinda emassiz.")
            return

        with game_lock:
            game = active_games.get(chat_id)

        if not game:
            bot.reply_to(message, "❌ Aktiv o'yin yo'q.")
            return
        if game.started:
            bot.reply_to(message, "⚠️ O'yin boshlangan — chiqib ketib bo'lmaydi.")
            return

        with game_lock:
            removed = game.remove_player(uid)
            if removed:
                user_game_map.pop(uid, None)

        if removed:
            _refresh_lobby(chat_id)
            bot.reply_to(message, "✅ O'yindan chiqdingiz.")
        else:
            bot.reply_to(message, "ℹ️ Siz o'yinda emassiz.")

    except Exception as e:
        logger.error(f"cmd_leave xatosi: {e}", exc_info=True)


# =====================================================================
#  ADMIN FUNKSIYALARI: BALANS QO'SHISH / YECHISH
# =====================================================================

@bot.message_handler(commands=['add_balance'])
def cmd_add_balance(message):
    uid = message.from_user.id
    if uid not in admin_ids:
        bot.reply_to(message, "❌ Bu buyruq faqat adminlar uchun!")
        return

    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "❌ Ishlatish: /add_balance <user_id/username> <summa>\n\n"
                              "Misol: /add_balance @username 1000\n"
                              "Misol: /add_balance 123456789 5000")
        return

    try:
        amount = int(args[2])
        if amount <= 0:
            bot.reply_to(message, "❌ Summa musbat son bo'lishi kerak!")
            return
    except ValueError:
        bot.reply_to(message, "❌ Summa noto'g'ri! Son kiriting.")
        return

    user_id, profile = find_profile(args[1])
    if not profile:
        bot.reply_to(message, f"❌ Foydalanuvchi topilmadi: {args[1]}")
        return

    profile['balance'] += amount
    save_data()

    bot.reply_to(message,
                 f"✅ *{profile['username']}* (ID: {user_id}) balansiga *{amount} 💰* qo'shildi!\n"
                 f"📊 Yangi balans: *{profile['balance']} 💰*",
                 parse_mode="Markdown")

    safe_send(int(user_id),
              f"🎉 Admin tomonidan balansingizga *{amount} 💰* qo'shildi!\n"
              f"📊 Yangi balansingiz: *{profile['balance']} 💰*",
              parse_mode="Markdown")


@bot.message_handler(commands=['remove_balance'])
def cmd_remove_balance(message):
    uid = message.from_user.id
    if uid not in admin_ids:
        bot.reply_to(message, "❌ Bu buyruq faqat adminlar uchun!")
        return

    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "❌ Ishlatish: /remove_balance <user_id/username> <summa>\n\n"
                              "Misol: /remove_balance @username 1000\n"
                              "Misol: /remove_balance 123456789 5000")
        return

    try:
        amount = int(args[2])
        if amount <= 0:
            bot.reply_to(message, "❌ Summa musbat son bo'lishi kerak!")
            return
    except ValueError:
        bot.reply_to(message, "❌ Summa noto'g'ri! Son kiriting.")
        return

    user_id, profile = find_profile(args[1])
    if not profile:
        bot.reply_to(message, f"❌ Foydalanuvchi topilmadi: {args[1]}")
        return

    if profile['balance'] < amount:
        bot.reply_to(message,
                     f"❌ *{profile['username']}* balansida yetarli mablag' yo'q!\n"
                     f"📊 Hozirgi balans: *{profile['balance']} 💰*\n"
                     f"Kerak: *{amount} 💰*",
                     parse_mode="Markdown")
        return

    profile['balance'] -= amount
    save_data()

    bot.reply_to(message,
                 f"✅ *{profile['username']}* (ID: {user_id}) balansidan *{amount} 💰* yechildi!\n"
                 f"📊 Yangi balans: *{profile['balance']} 💰*",
                 parse_mode="Markdown")

    safe_send(int(user_id),
              f"⚠️ Admin tomonidan balansingizdan *{amount} 💰* yechildi!\n"
              f"📊 Yangi balansingiz: *{profile['balance']} 💰*",
              parse_mode="Markdown")


# =====================================================================
#  ADMIN PREMIUM BOSHQARUVI
# =====================================================================

@bot.message_handler(commands=['add_premium'])
def cmd_add_premium(message):
    uid = message.from_user.id
    if uid not in admin_ids:
        bot.reply_to(message, "❌ Bu buyruq faqat adminlar uchun!")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❌ Ishlatish: /add_premium <user_id/username>\n\n"
                              "Misol: /add_premium @username\n"
                              "Misol: /add_premium 123456789")
        return

    user_id, profile = find_profile(args[1])
    if not profile:
        bot.reply_to(message, f"❌ Foydalanuvchi topilmadi: {args[1]}")
        return

    uid_int = int(user_id)
    if uid_int in subscribers:
        bot.reply_to(message, f"ℹ️ *{profile['username']}* allaqachon premium foydalanuvchi!", parse_mode="Markdown")
        return

    subscribers.add(uid_int)
    save_data()

    bot.reply_to(message,
                 f"✅ *{profile['username']}* (ID: {user_id}) ga *PREMIUM* berildi!\n"
                 f"👑 Endi u premium imtiyozlaridan foydalanishi mumkin.",
                 parse_mode="Markdown")

    safe_send(int(user_id),
              f"🎉 *TABRIKLAYMIZ!*\n\n"
              f"Sizga admin tomonidan *PREMIUM STATUS* berildi! 👑\n\n"
              f"✨ Premium imtiyozlari:\n"
              f"• Kunlik bonus olish (/daily)\n"
              f"• Maxsus rollar yaratish\n"
              f"• Do'konda 10% chegirma\n"
              f"• Cheksiz kesh ochish\n"
              f"• Va boshqa ko'plab imtiyozlar!",
              parse_mode="Markdown")


@bot.message_handler(commands=['remove_premium'])
def cmd_remove_premium(message):
    uid = message.from_user.id
    if uid not in admin_ids:
        bot.reply_to(message, "❌ Bu buyruq faqat adminlar uchun!")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❌ Ishlatish: /remove_premium <user_id/username>\n\n"
                              "Misol: /remove_premium @username\n"
                              "Misol: /remove_premium 123456789")
        return

    user_id, profile = find_profile(args[1])
    if not profile:
        bot.reply_to(message, f"❌ Foydalanuvchi topilmadi: {args[1]}")
        return

    uid_int = int(user_id)
    if uid_int not in subscribers:
        bot.reply_to(message, f"ℹ️ *{profile['username']}* premium foydalanuvchi emas!", parse_mode="Markdown")
        return

    subscribers.discard(uid_int)
    save_data()

    bot.reply_to(message,
                 f"✅ *{profile['username']}* (ID: {user_id}) dan *PREMIUM* olib tashlandi!",
                 parse_mode="Markdown")

    safe_send(int(user_id),
              f"⚠️ *PREMIUM STATUS OLIB TASHLANDI*\n\n"
              f"Sizning premium statusingiz admin tomonidan bekor qilindi.\n"
              f"Qayta premium olish uchun admin bilan bog'laning.",
              parse_mode="Markdown")


@bot.message_handler(commands=['premium_list'])
def cmd_premium_list(message):
    uid = message.from_user.id
    if uid not in admin_ids:
        bot.reply_to(message, "❌ Bu buyruq faqat adminlar uchun!")
        return

    if not subscribers:
        bot.reply_to(message, "📭 Hozircha premium foydalanuvchilar yo'q.")
        return

    names = []
    for sub_id in subscribers:
        p = user_profiles.get(str(sub_id))
        if p:
            names.append(f"👑 {p['username']} (ID: {sub_id})")
        else:
            names.append(f"❓ Foydalanuvchi {sub_id}")

    text = f"💎 *PREMIUM FOYDALANUVCHILAR ({len(subscribers)}):*\n\n" + "\n".join(names)

    if len(text) > 4000:
        bot.reply_to(message, f"💎 *Premium foydalanuvchilar:* {len(subscribers)} ta", parse_mode="Markdown")
        for name in names[:50]:
            bot.send_message(message.chat.id, name, parse_mode="Markdown")
    else:
        bot.reply_to(message, text, parse_mode="Markdown")


# =====================================================================
#  PREMIUM KUNLIK BONUS
# =====================================================================

@bot.message_handler(commands=['daily'])
def cmd_daily(message):
    uid = message.from_user.id
    p = get_profile(uid, message.from_user.username or message.from_user.first_name)

    if uid not in subscribers:
        bot.reply_to(message, "❌ Kunlik bonus faqat *PREMIUM* foydalanuvchilar uchun!\n"
                              "/subscribe orqali premium oling yoki admin bilan bog'laning.",
                     parse_mode="Markdown")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    if f"{uid}_{today}" in daily_bonus_taken:
        bot.reply_to(message, "⏰ Siz bugun kunlik bonusni allaqachon oldingiz!\nErtaga qaytib keling.")
        return

    bonus_balance = random.randint(50, 150)
    bonus_points = random.randint(20, 80)

    p['balance'] += bonus_balance
    p['points'] += bonus_points
    daily_bonus_taken.add(f"{uid}_{today}")
    save_data()

    bot.reply_to(message,
                 f"🎁 *KUNLIK BONUS!*\n\n"
                 f"💰 {bonus_balance} valyuta\n"
                 f"⭐ {bonus_points} ball\n\n"
                 f"📊 Yangi balans: *{p['balance']}*\n"
                 f"⭐ Yangi ballar: *{p['points']}*",
                 parse_mode="Markdown")


# =====================================================================
#  Callback handler (qisqartirilgan — asosiy funksiyalar)
# =====================================================================

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    try:
        uid = call.from_user.id
        data = call.data
        uname = call.from_user.username or call.from_user.first_name
        get_profile(uid, uname)

        if data == "menu_game":
            _cb_menu_game(call)
            answer_cb(call)
        elif data == "menu_profile":
            _cb_menu_profile(call)
            answer_cb(call)
        elif data == "menu_shop":
            _cb_menu_shop(call)
            answer_cb(call)
        elif data == "menu_top":
            safe_edit(call.message.chat.id, call.message.message_id,
                      "🏆 *Reyting turini tanlang:*", reply_markup=kb_top_menu(), parse_mode="Markdown")
            answer_cb(call)
        elif data == "menu_help":
            _cb_help_main(call)
            answer_cb(call)
        elif data == "menu_settings":
            _cb_settings(call)
            answer_cb(call)
        elif data == "back_main":
            p = get_profile(uid)
            is_sub = "💎 Premium" if uid in subscribers else "👤 Oddiy"
            text = (f"👤 *{p['username']}*\n\n"
                    f"💰 {p['balance']}  |  ⭐ {p['points']}  |  🌹 {p['roses']}  |  {is_sub}\n\n"
                    "Menyudan tanlang 👇")
            safe_edit(call.message.chat.id, call.message.message_id,
                      text, reply_markup=kb_main_menu(), parse_mode="Markdown")
            answer_cb(call)
        elif data.startswith("top_"):
            _cb_top(call, data[4:])
            answer_cb(call)
        elif data.startswith("buyitem_"):
            item = data[len("buyitem_"):]
            _cb_buy(call, item)
        elif data == "do_case":
            p = get_profile(uid)
            rt = random.choice(["balance", "points", "roses"])
            ra = random.randint(1, 100)
            p[rt] += ra
            save_data()
            icons = {"balance": "💰", "points": "⭐", "roses": "🌹"}
            names = {"balance": "valyuta", "points": "ball", "roses": "atirgul"}
            answer_cb(call, f"🎁 {icons[rt]} {ra} {names[rt]} yutib oldingiz!", alert=True)
            _cb_menu_profile(call)
        elif data == "confirm_subscribe":
            _cb_confirm_subscribe(call)
        elif data.startswith("startgame_"):
            chat_id = int(data[len("startgame_"):])
            answer_cb(call, "O'yin boshlanmoqda...")
            _do_startgame(chat_id)
        elif data.startswith("cancelgame_"):
            chat_id = int(data[len("cancelgame_"):])
            cleanup_game(chat_id)
            safe_edit(call.message.chat.id, call.message.message_id, "❌ O'yin bekor qilindi.")
            answer_cb(call)
        elif data.startswith("endvote_"):
            chat_id = int(data[len("endvote_"):])
            answer_cb(call, "Ovozlar hisoblanmoqda...")
            _do_endvote(chat_id)
        elif data.startswith("endnight_"):
            chat_id = int(data[len("endnight_"):])
            answer_cb(call, "Tun yakunlanmoqda...")
            _do_endnight(chat_id)
        else:
            answer_cb(call)

    except Exception as e:
        logger.error(f"on_callback xatosi (data={call.data}): {e}", exc_info=True)
        try:
            answer_cb(call, "Xato yuz berdi.", alert=True)
        except Exception:
            pass


def _cb_menu_game(call):
    uid = call.from_user.id
    chat_id = user_game_map.get(uid)
    with game_lock:
        game = active_games.get(chat_id) if chat_id else None

    if game and game.started:
        info = (f"Siz o'yindasiz!\n"
                f"Faza: *{'☀️ Kunduz' if game.phase == 'day' else '🌙 Tun'}*\n"
                f"Raund: *{game.round}*  |  Tirik: *{game.alive_count()}*")
        kb = InlineKeyboardMarkup(row_width=2)
        if game.phase == 'day':
            kb.add(InlineKeyboardButton("🗳 Ovoz berish", callback_data=f"open_vote_{chat_id}"))
        elif game.phase == 'night':
            kb.add(InlineKeyboardButton("🌙 Tungi harakat", callback_data=f"open_night_{chat_id}"))
        kb.add(InlineKeyboardButton("◀ Ortga", callback_data="back_main"))
    else:
        info = ("Siz hozir hech qanday o'yinda emassiz.\n"
                "O'yin yaratish uchun guruhga boring va /newgame yozing.")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("◀ Ortga", callback_data="back_main"))

    safe_edit(call.message.chat.id, call.message.message_id,
              f"🎮 *O'yin menyusi*\n\n{info}", reply_markup=kb, parse_mode="Markdown")


def _cb_menu_profile(call):
    uid = call.from_user.id
    p = get_profile(uid)
    gp = p.get('games_played', 0)
    wp = p.get('games_won', 0)
    rate = f"{round(wp/gp*100)}%" if gp > 0 else "—"
    sub = "💎 Premium" if uid in subscribers else "👤 Oddiy"
    text = (f"👤 *{p['username']}*\n"
            f"{'─'*24}\n"
            f"💰 Balans:      *{p['balance']}*\n"
            f"⭐ Ballar:       *{p['points']}*\n"
            f"🌹 Atirgullar:  *{p['roses']}*\n"
            f"💎 Donat:        *{p['donation']}*\n"
            f"🎮 O'yinlar:     *{gp}*\n"
            f"🏆 G'alabalar:   *{wp}* ({rate})\n"
            f"{'─'*24}\n"
            f"{sub}")
    kb = InlineKeyboardMarkup(row_width=2)
    if uid not in subscribers:
        kb.add(InlineKeyboardButton("💎 Premium olish", callback_data="confirm_subscribe"))
    kb.add(InlineKeyboardButton("🎁 Kesh ochish", callback_data="do_case"),
           InlineKeyboardButton("◀ Ortga", callback_data="back_main"))
    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb, parse_mode="Markdown")


def _cb_menu_shop(call):
    uid = call.from_user.id
    p = get_profile(uid)
    safe_edit(call.message.chat.id, call.message.message_id,
              f"🛒 *Do'kon*\n💰 Balansingiz: *{p['balance']}*\n\nTovarni tanlang:",
              reply_markup=kb_shop(), parse_mode="Markdown")


def _cb_buy(call, item_name: str):
    uid = call.from_user.id
    p = get_profile(uid)
    matched = next((k for k in SHOP_ITEMS if k == item_name), None)

    if not matched:
        answer_cb(call, "❌ Tovar topilmadi.", alert=True)
        return

    price = SHOP_ITEMS[matched]
    if p['balance'] < price:
        answer_cb(call, f"❌ Yetarli mablag' yo'q!\nKerak: {price} 💰\nSizda: {p['balance']} 💰", alert=True)
        return

    p['balance'] -= price
    save_data()
    answer_cb(call, f"✅ '{matched}' sotib olindi!", alert=True)
    _cb_menu_shop(call)


def _cb_top(call, key: str):
    mapping = {
        'points': ('⭐ Ballar', 'points', 'ball'),
        'balance': ('💰 Balans', 'balance', 'valyuta'),
        'wins': ("🏆 G'alabalar", 'games_won', "g'alaba"),
        'roses': ('🌹 Atirgullar', 'roses', 'atirgul'),
    }
    if key not in mapping:
        return

    label, field, unit = mapping[key]
    su = sorted(user_profiles.items(), key=lambda x: x[1].get(field, 0), reverse=True)
    meds = ['🥇', '🥈', '🥉']
    text = f"{label} — Top 10\n\n"
    for i, (_, p) in enumerate(su[:10], 1):
        m = meds[i-1] if i <= 3 else f"{i}."
        text += f"{m} *{p['username']}*  —  {p.get(field,0)} {unit}\n"

    if not su:
        text += "_(Ma'lumot yo'q)_"

    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb_top_menu(), parse_mode="Markdown")


def _cb_confirm_subscribe(call):
    uid = call.from_user.id
    p = get_profile(uid)

    if uid in subscribers:
        answer_cb(call, "ℹ️ Siz allaqachon Premium foydalanuvchisiz!", alert=True)
        return
    if p['donation'] < SUBSCRIPTION_PRICE:
        answer_cb(call, f"❌ Yetarli donat yo'q!\nKerak: {SUBSCRIPTION_PRICE} 💎\nSizda: {p['donation']} 💎", alert=True)
        return

    p['donation'] -= SUBSCRIPTION_PRICE
    subscribers.add(uid)
    save_data()
    answer_cb(call, "🎉 Premium muvaffaqiyatli olindi!", alert=True)
    _cb_menu_profile(call)


def _cb_settings(call):
    uid = call.from_user.id
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("◀ Ortga", callback_data="back_main"))
    text = "⚙️ *Sozlamalar*\n\n" + ("👑 Admin paneliga xush kelibsiz." if uid in admin_ids else "ℹ️ Foydalanuvchi sozlamalari mavjud emas.")
    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb, parse_mode="Markdown")


def _cb_help_main(call):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎮 O'yin", callback_data="help_game"),
        InlineKeyboardButton("🤖 Bot", callback_data="help_bot"),
        InlineKeyboardButton("🎭 Rollar", callback_data="help_roles"),
        InlineKeyboardButton("👑 Admin", callback_data="help_admin"),
        InlineKeyboardButton("◀ Ortga", callback_data="back_main"),
    )
    safe_edit(call.message.chat.id, call.message.message_id, "📋 *Yordam* — Bo'lim tanlang:",
              reply_markup=kb, parse_mode="Markdown")


# =====================================================================
#  Admin buyruqlari (qo'shimcha)
# =====================================================================

@bot.message_handler(commands=['add_admin'])
def cmd_add_admin(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Ruxsat yo'q.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Ishlatish: /add_admin <user_id>")
        return
    try:
        new_admin = int(args[1])
        admin_ids.add(new_admin)
        save_data()
        bot.reply_to(message, f"✅ {args[1]} admin qilindi.")
    except ValueError:
        bot.reply_to(message, "❌ ID son bo'lishi kerak.")


@bot.message_handler(commands=['remove_admin'])
def cmd_remove_admin(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Ruxsat yo'q.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Ishlatish: /remove_admin <user_id>")
        return
    try:
        rem_id = int(args[1])
        if rem_id == message.from_user.id:
            bot.reply_to(message, "❌ O'zingizni admin ro'yxatidan o'chira olmaysiz.")
            return
        admin_ids.discard(rem_id)
        save_data()
        bot.reply_to(message, f"✅ {args[1]} admin ro'yxatidan chiqarildi.")
    except ValueError:
        bot.reply_to(message, "❌ ID son bo'lishi kerak.")


@bot.message_handler(commands=['add_donation'])
def cmd_add_donation(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Ruxsat yo'q.")
        return
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Ishlatish: /add_donation <user_id/username> <summa>")
        return
    try:
        amount = int(args[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "❌ Summa musbat son bo'lishi kerak.")
        return

    _, tp = find_profile(args[1])
    if not tp:
        bot.reply_to(message, "❌ Foydalanuvchi topilmadi.")
        return
    tp['donation'] += amount
    save_data()
    bot.reply_to(message, f"✅ *{tp['username']}* ga *{amount}* donat qo'shildi.", parse_mode="Markdown")


@bot.message_handler(commands=['set_bonus'])
def cmd_set_bonus(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Ruxsat yo'q.")
        return
    args = message.text.split()
    if len(args) != 3 or args[1].lower() not in ('villager', 'mafia'):
        bot.reply_to(message, "Ishlatish: /set_bonus <villager/mafia> <summa>")
        return
    try:
        amount = int(args[2])
        if amount < 0:
            raise ValueError
        bonus_settings[args[1].lower()] = amount
        save_data()
        bot.reply_to(message, f"✅ *{args[1]}* bonusi *{amount}* ga o'rnatildi.", parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "❌ Summa manfiy bo'lmasligi kerak.")


@bot.message_handler(commands=['subscribers'])
def cmd_subscribers(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Ruxsat yo'q.")
        return
    if not subscribers:
        bot.reply_to(message, "Obunachilar yo'q.")
        return
    names = []
    for sub_id in subscribers:
        p = user_profiles.get(str(sub_id))
        names.append(p['username'] if p else str(sub_id))
    bot.reply_to(message, f"💎 *Obunachilar ({len(subscribers)}):*\n" + "\n".join(f"  • {n}" for n in names),
                 parse_mode="Markdown")


@bot.message_handler(commands=['stop', 'end'])
def cmd_stop(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Ruxsat yo'q.")
        return
    cid = message.chat.id
    if cid in active_games:
        cleanup_game(cid)
        bot.reply_to(message, "✅ O'yin to'xtatildi.")
    else:
        bot.reply_to(message, "ℹ️ Aktiv o'yin yo'q.")


@bot.message_handler(commands=['profile'])
def cmd_profile(message):
    uid = message.from_user.id
    uname = message.from_user.username or message.from_user.first_name
    p = get_profile(uid, uname)
    gp = p.get('games_played', 0)
    wp = p.get('games_won', 0)
    rate = f"{round(wp/gp*100)}%" if gp > 0 else "—"
    sub = "💎 Premium" if uid in subscribers else "👤 Oddiy"
    text = (f"👤 *{p['username']}*\n"
            f"{'─'*24}\n"
            f"💰 Balans:      *{p['balance']}*\n"
            f"⭐ Ballar:       *{p['points']}*\n"
            f"🌹 Atirgullar:  *{p['roses']}*\n"
            f"💎 Donat:        *{p['donation']}*\n"
            f"🎮 O'yinlar:     *{gp}*  🏆 G'alaba: *{wp}* ({rate})\n"
            f"{'─'*24}\n"
            f"{sub}")
    kb = InlineKeyboardMarkup(row_width=2)
    if uid not in subscribers:
        kb.add(InlineKeyboardButton("💎 Premium", callback_data="confirm_subscribe"))
    kb.add(InlineKeyboardButton("🎁 Kesh ochish", callback_data="do_case"))
    bot.reply_to(message, text, reply_markup=kb, parse_mode="Markdown")


@bot.message_handler(commands=['balance', 'amount'])
def cmd_balance(message):
    uid = message.from_user.id
    p = get_profile(uid, message.from_user.username or message.from_user.first_name)
    bot.reply_to(message, f"💰 Balansingiz: *{p['balance']}*", parse_mode="Markdown")


@bot.message_handler(commands=['give'])
def cmd_give(message):
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Ishlatish: /give <user_id/username> <summa>")
        return
    try:
        amount = int(args[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "❌ Noto'g'ri summa (musbat son bo'lishi kerak).")
        return

    sid = message.from_user.id
    sp = get_profile(sid, message.from_user.username or message.from_user.first_name)

    if sp['balance'] < amount:
        bot.reply_to(message, f"❌ Yetarli mablag' yo'q.\nSizda: {sp['balance']} 💰")
        return

    rid, tp = find_profile(args[1])
    if not tp:
        bot.reply_to(message, "❌ Foydalanuvchi topilmadi.")
        return
    if rid == str(sid):
        bot.reply_to(message, "❌ O'zingizga o'tkaza olmaysiz.")
        return

    sp['balance'] -= amount
    tp['balance'] += amount
    save_data()
    bot.reply_to(message, f"✅ *{amount} 💰* *{tp['username']}* ga o'tkazildi.", parse_mode="Markdown")


@bot.message_handler(commands=['subscribe'])
def cmd_subscribe(message):
    uid = message.from_user.id
    p = get_profile(uid, message.from_user.username or message.from_user.first_name)
    if uid in subscribers:
        bot.reply_to(message, "ℹ️ Siz allaqachon Premium foydalanuvchisiz.")
        return
    if p['donation'] < SUBSCRIPTION_PRICE:
        bot.reply_to(message, f"❌ Premium uchun *{SUBSCRIPTION_PRICE}* donat kerak.\nSizda: *{p['donation']}* 💎",
                     parse_mode="Markdown")
        return
    p['donation'] -= SUBSCRIPTION_PRICE
    subscribers.add(uid)
    save_data()
    bot.reply_to(message, "🎉 *Premium muvaffaqiyatli olindi!*", parse_mode="Markdown")


@bot.message_handler(commands=['shop'])
def cmd_shop(message):
    uid = message.from_user.id
    p = get_profile(uid, message.from_user.username or message.from_user.first_name)
    bot.reply_to(message, f"🛒 *Do'kon*\n💰 Balansingiz: *{p['balance']}*\n\nTovarni tanlang:",
                 reply_markup=kb_shop(), parse_mode="Markdown")


@bot.message_handler(commands=['top'])
def cmd_top(message):
    bot.reply_to(message, "🏆 *Reyting* — Turini tanlang:", reply_markup=kb_top_menu(), parse_mode="Markdown")


@bot.message_handler(commands=['rose'])
def cmd_rose(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Ishlatish: /rose <username/user_id> [miqdor]")
        return
    try:
        amount = int(args[2]) if len(args) > 2 else 1
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "❌ Noto'g'ri miqdor.")
        return

    _, tp = find_profile(args[1])
    if not tp:
        bot.reply_to(message, "❌ Foydalanuvchi topilmadi.")
        return

    tp['roses'] += amount
    save_data()
    bot.reply_to(message, f"🌹 *{amount}* ta atirgul *{tp['username']}* ga yuborildi!", parse_mode="Markdown")


@bot.message_handler(commands=['case'])
def cmd_case(message):
    uid = message.from_user.id
    p = get_profile(uid, message.from_user.username or message.from_user.first_name)
    rt = random.choice(["balance", "points", "roses"])
    ra = random.randint(1, 100)
    p[rt] += ra
    save_data()
    icons = {"balance": "💰", "points": "⭐", "roses": "🌹"}
    names = {"balance": "valyuta", "points": "ball", "roses": "atirgul"}
    bot.reply_to(message, f"🎁 {icons[rt]} *{ra} {names[rt]}* yutib oldingiz!", parse_mode="Markdown")


@bot.message_handler(commands=['feedback'])
def cmd_feedback(message):
    text = message.text.replace('/feedback', '', 1).strip()
    if not text:
        bot.reply_to(message, "Fikringizni yozing: /feedback <matn>")
        return
    sender = message.from_user.username or message.from_user.first_name
    uid = message.from_user.id
    for admin_id in admin_ids:
        safe_send(admin_id, f"💬 *Fikr: {sender}* (ID: {uid})\n\n{text}", parse_mode="Markdown")
    bot.reply_to(message, "✅ Fikringiz uchun rahmat!")


@bot.message_handler(commands=['id'])
def cmd_id(message):
    lines = [f"💬 Chat ID: `{message.chat.id}`"]
    if message.from_user:
        lines.append(f"👤 Sizning ID: `{message.from_user.id}`")
    if message.chat.type in ("group", "supergroup"):
        lines.append(f"💬 Chat nomi: {message.chat.title}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=['help'])
def cmd_help(message):
    uid = message.from_user.id
    get_profile(uid, message.from_user.username or message.from_user.first_name)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎮 O'yin", callback_data="help_game"),
        InlineKeyboardButton("🤖 Bot", callback_data="help_bot"),
        InlineKeyboardButton("🎭 Rollar", callback_data="help_roles"),
        InlineKeyboardButton("👑 Admin", callback_data="help_admin"),
    )
    bot.reply_to(message, "📋 *Yordam* — Bo'lim tanlang:", reply_markup=kb, parse_mode="Markdown")


# =====================================================================
#  Ishga tushirish
# =====================================================================

def main():
    logger.info("Bot ishga tushmoqda...")
    set_bot_commands()
    logger.info("Bot tayyor! Polling boshlanmoqda...")

    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=20,
                allowed_updates=["message", "callback_query"],
                restart_on_change=False,
            )
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Telegram API xatosi: {e}")
            if "Unauthorized" in str(e):
                logger.critical("❌ Noto'g'ri API token! Bot to'xtatildi.")
                break
            time.sleep(5)
        except ConnectionError as e:
            logger.error(f"Ulanish xatosi: {e}. 10 soniyadan keyin qayta ulanish...")
            time.sleep(10)
        except Exception as e:
            logger.error(f"Kutilmagan xato: {e}", exc_info=True)
            time.sleep(5)


if __name__ == '__main__':
    main()