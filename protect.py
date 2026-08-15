#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║                cPROTECT  •  Premium Guard Bot                ║
║   Telegram Premium users ko channel se auto-remove karta hai ║
║   + full subscription / client management system             ║
╚══════════════════════════════════════════════════════════════╝

Single file bot.  Run:  python bot.py
Config:  config.py  (ya .env)
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
import os
import re
import sqlite3
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    Chat,
    ChatMember,
    InlineKeyboardButton as B,
    InlineKeyboardMarkup as M,
    InputFile,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    ExtBot,
    MessageHandler,
    filters,
)

import config

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    level=logging.INFO,
    datefmt="%d-%m %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cprotect")

HTML = ParseMode.HTML


# ═══════════════════════════════════════════════════════════════
#  SECTION 0 ── ✨ PREMIUM (CUSTOM) EMOJI ENGINE
# ═══════════════════════════════════════════════════════════════
#  Bot ke har message ka har emoji automatically premium/animated
#  emoji me convert ho jata hai — <tg-emoji emoji-id="...">✅</tg-emoji>
#
#  Telegram rule: custom emoji sirf tab render hoti hai jab bot ne
#  Fragment se username liya ho, ya bot owner ke paas Premium ho
#  (private/group chats me). Allowed na ho to bot khud plain emoji
#  par fallback kar deta hai — koi message kabhi fail nahi hoga.
# ═══════════════════════════════════════════════════════════════

# runtime state (DB se load hota hai)
EMOJI_MAP: dict[str, str] = dict(config.EMOJI_IDS)
EMOJI_ON: bool = config.PREMIUM_EMOJI
EMOJI_SUPPORTED: bool = True     # False ho jata hai agar Telegram reject kare

# har emoji (variation selector / ZWJ sequence ke sath) match karta hai
_EMOJI_RE = re.compile(
    "(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF"
    "\u2934\u2935\u3030\u303D\u3297\u3299\u00A9\u00AE\u2122]"
    "[\uFE0F\uFE0E\u20E3]?"
    "(?:\u200D[\U0001F000-\U0001FAFF\u2600-\u27BF][\uFE0F]?)*)"
)

# HTML tag, aur <code>/<pre> blocks (inke andar custom emoji allowed nahi hai)
_SKIP_RE = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def _plain_key(e: str) -> str:
    """Variation selector hata ke lookup key banata hai."""
    return e.replace("\uFE0F", "").replace("\uFE0E", "")


def premiumize(text: str) -> str:
    """Text ke saare emoji ko <tg-emoji> me wrap karta hai (tags ke bahar)."""
    if not (EMOJI_ON and EMOJI_SUPPORTED and EMOJI_MAP) or not text:
        return text
    if "<tg-emoji" in text:          # pehle se converted
        return text

    def repl(m: re.Match) -> str:
        e = m.group()
        eid = EMOJI_MAP.get(e) or EMOJI_MAP.get(_plain_key(e))
        return f'<tg-emoji emoji-id="{eid}">{e}</tg-emoji>' if eid else e

    # tags aur <code>/<pre> blocks ko chhod kar sirf plain text me replace
    out, last = [], 0
    for skip in _SKIP_RE.finditer(text):
        out.append(_EMOJI_RE.sub(repl, text[last:skip.start()]))
        out.append(skip.group())
        last = skip.end()
    out.append(_EMOJI_RE.sub(repl, text[last:]))
    return "".join(out)


def _is_emoji_error(err: Exception) -> bool:
    s = str(err).lower()
    return any(x in s for x in (
        "custom emoji", "custom_emoji", "emoji_id", "tg-emoji",
        "unsupported start tag", "not enough rights to send custom emoji",
        "sticker_id_invalid", "premium",
    ))


class PremiumBot(ExtBot):
    """
    ExtBot subclass — har outgoing text/caption ko premium emoji me badal deta
    hai. Telegram reject kare to us call ko plain text ke sath retry karta hai
    aur aage ke liye feature apne aap OFF kar deta hai.
    """

    async def _premium_call(self, fn, kwargs: dict, field: str):
        global EMOJI_SUPPORTED
        original = kwargs.get(field)
        if (EMOJI_ON and EMOJI_SUPPORTED and EMOJI_MAP
                and isinstance(original, str)
                and kwargs.get("parse_mode", HTML) in (HTML, "HTML", None)):
            kwargs[field] = premiumize(original)
            kwargs.setdefault("parse_mode", HTML)
            try:
                return await fn(**kwargs)
            except BadRequest as e:
                if not _is_emoji_error(e):
                    raise
                log.warning("⚠️ Custom emoji rejected by Telegram (%s) → plain fallback", e)
                EMOJI_SUPPORTED = False
                kwargs[field] = original
        return await fn(**kwargs)

    async def send_message(self, *args, **kwargs):
        if args:      # positional -> normalize
            names = ["chat_id", "text"]
            for i, v in enumerate(args):
                if i < len(names):
                    kwargs[names[i]] = v
        return await self._premium_call(super().send_message, kwargs, "text")

    async def edit_message_text(self, *args, **kwargs):
        if args:
            kwargs["text"] = args[0]
        return await self._premium_call(super().edit_message_text, kwargs, "text")

    async def send_document(self, *args, **kwargs):
        names = ["chat_id", "document"]
        for i, v in enumerate(args):
            if i < len(names):
                kwargs[names[i]] = v
        return await self._premium_call(super().send_document, kwargs, "caption")

    async def send_photo(self, *args, **kwargs):
        names = ["chat_id", "photo"]
        for i, v in enumerate(args):
            if i < len(names):
                kwargs[names[i]] = v
        return await self._premium_call(super().send_photo, kwargs, "caption")


async def load_emoji_map():
    """DB se saved premium emoji IDs load karta hai."""
    global EMOJI_MAP, EMOJI_ON
    EMOJI_MAP = dict(config.EMOJI_IDS)
    rows = await q("SELECT emoji, emoji_id FROM emoji_map", (), "all")
    for r in rows:
        EMOJI_MAP[r["emoji"]] = r["emoji_id"]
    EMOJI_ON = (await get_setting("premium_emoji", "1" if config.PREMIUM_EMOJI else "0")) == "1"
    log.info("✨ Premium emoji: %s | %d mapped", "ON" if EMOJI_ON else "OFF", len(EMOJI_MAP))


async def save_emoji(emoji: str, emoji_id: str):
    await q("INSERT INTO emoji_map(emoji,emoji_id) VALUES(?,?) "
            "ON CONFLICT(emoji) DO UPDATE SET emoji_id=excluded.emoji_id", (emoji, emoji_id))
    EMOJI_MAP[emoji] = emoji_id
    EMOJI_MAP[_plain_key(emoji)] = emoji_id


# ═══════════════════════════════════════════════════════════════
#  SECTION 1 ── DATABASE  (SQLite, async-safe)
# ═══════════════════════════════════════════════════════════════
_db_lock = asyncio.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    plan          TEXT    DEFAULT 'p1',
    slots         INTEGER DEFAULT 1,
    expires_at    INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    is_blocked    INTEGER DEFAULT 0,
    total_removed INTEGER DEFAULT 0,
    log_channel   INTEGER,
    notify_dm     INTEGER DEFAULT 1,
    last_reminder INTEGER DEFAULT 0,
    expired_notified INTEGER DEFAULT 0,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS channels (
    chat_id      INTEGER PRIMARY KEY,
    owner_id     INTEGER NOT NULL,
    title        TEXT,
    username     TEXT,
    added_at     INTEGER NOT NULL,
    active       INTEGER DEFAULT 1,
    action       TEXT    DEFAULT 'ban',
    f_premium    INTEGER DEFAULT 1,
    f_nouser     INTEGER DEFAULT 0,
    f_nophoto    INTEGER DEFAULT 0,
    f_newacc     INTEGER DEFAULT 0,
    f_words      INTEGER DEFAULT 0,
    auto_approve INTEGER DEFAULT 0,
    removed      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS removals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    chat_title TEXT,
    user_id    INTEGER,
    username   TEXT,
    full_name  TEXT,
    premium    INTEGER DEFAULT 0,
    reason     TEXT,
    action     TEXT,
    source     TEXT,
    at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rem_owner ON removals(owner_id, at);

CREATE TABLE IF NOT EXISTS whitelist (
    owner_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    label    TEXT,
    at       INTEGER NOT NULL,
    PRIMARY KEY (owner_id, user_id)
);

CREATE TABLE IF NOT EXISTS badwords (
    owner_id INTEGER NOT NULL,
    word     TEXT NOT NULL,
    PRIMARY KEY (owner_id, word)
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    actor  INTEGER, action TEXT, target TEXT, detail TEXT, at INTEGER
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS seen_users (
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, first_seen INTEGER
);

CREATE TABLE IF NOT EXISTS emoji_map (
    emoji    TEXT PRIMARY KEY,
    emoji_id TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def _sync(sql: str, params: Iterable = (), fetch: str | None = None):
    con = _connect()
    cur = con.execute(sql, tuple(params))
    if fetch == "one":
        row = cur.fetchone()
        con.commit()
        return dict(row) if row else None
    if fetch == "all":
        rows = [dict(r) for r in cur.fetchall()]
        con.commit()
        return rows
    con.commit()
    return cur.lastrowid


async def q(sql: str, params: Iterable = (), fetch: str | None = None):
    async with _db_lock:
        return await asyncio.to_thread(_sync, sql, params, fetch)


def now() -> int:
    return int(time.time())


def fdate(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%d %b %Y") if ts else "—"


def fdatetime(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%d %b %Y • %I:%M %p") if ts else "—"


async def init_db():
    async with _db_lock:
        await asyncio.to_thread(lambda: _connect().executescript(SCHEMA))
    # migrations (naye column add hue to)
    for tbl, col, ddl in [
        ("clients", "expired_notified", "INTEGER DEFAULT 0"),
        ("channels", "auto_approve", "INTEGER DEFAULT 0"),
        ("channels", "f_words", "INTEGER DEFAULT 0"),
    ]:
        try:
            await q(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
        except Exception:
            pass
    for k, v in (("maintenance", "0"), ("welcome_note", ""),
                 ("premium_emoji", "1" if config.PREMIUM_EMOJI else "0")):
        await q("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))


# ── settings ──
async def get_setting(k: str, d: str = "") -> str:
    r = await q("SELECT value FROM settings WHERE key=?", (k,), "one")
    return r["value"] if r else d


async def set_setting(k: str, v: Any):
    await q(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (k, str(v)),
    )


# ── clients ──
async def get_client(uid: int) -> Optional[dict]:
    return await q("SELECT * FROM clients WHERE user_id=?", (uid,), "one")


async def all_clients() -> list[dict]:
    return await q("SELECT * FROM clients ORDER BY expires_at DESC", (), "all")


async def add_client(uid: int, days: int, plan: str, username=None, first_name=None) -> dict:
    slots = config.PLANS.get(plan, {}).get("slots", 1)
    ex = await get_client(uid)
    base = max(now(), ex["expires_at"]) if ex else now()
    exp = base + days * 86400
    if ex:
        await q(
            "UPDATE clients SET plan=?,slots=?,expires_at=?,is_blocked=0,last_reminder=0,"
            "expired_notified=0,username=COALESCE(?,username),first_name=COALESCE(?,first_name) "
            "WHERE user_id=?",
            (plan, slots, exp, username, first_name, uid),
        )
    else:
        await q(
            "INSERT INTO clients(user_id,username,first_name,plan,slots,expires_at,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, username, first_name, plan, slots, exp, now()),
        )
    return await get_client(uid)


async def upd_client(uid: int, **f):
    if not f:
        return
    await q(f"UPDATE clients SET {', '.join(k+'=?' for k in f)} WHERE user_id=?", (*f.values(), uid))


async def del_client(uid: int):
    await q("DELETE FROM channels WHERE owner_id=?", (uid,))
    await q("DELETE FROM whitelist WHERE owner_id=?", (uid,))
    await q("DELETE FROM badwords WHERE owner_id=?", (uid,))
    await q("DELETE FROM clients WHERE user_id=?", (uid,))


def is_active(c: dict | None) -> bool:
    return bool(c) and not c["is_blocked"] and c["expires_at"] > now()


def days_left(c: dict) -> float:
    return max(0.0, (c["expires_at"] - now()) / 86400)


# ── channels ──
async def get_channel(cid: int) -> Optional[dict]:
    return await q("SELECT * FROM channels WHERE chat_id=?", (cid,), "one")


async def channels_of(uid: int) -> list[dict]:
    return await q("SELECT * FROM channels WHERE owner_id=? ORDER BY added_at", (uid,), "all")


async def upd_channel(cid: int, **f):
    if not f:
        return
    await q(f"UPDATE channels SET {', '.join(k+'=?' for k in f)} WHERE chat_id=?", (*f.values(), cid))


# ── whitelist / badwords ──
async def wl_list(uid: int) -> list[dict]:
    return await q("SELECT * FROM whitelist WHERE owner_id=? ORDER BY at DESC", (uid,), "all")


async def wl_has(uid: int, target: int) -> bool:
    return bool(await q("SELECT 1 FROM whitelist WHERE owner_id=? AND user_id=?", (uid, target), "one"))


async def bw_list(uid: int) -> list[str]:
    return [r["word"] for r in await q("SELECT word FROM badwords WHERE owner_id=?", (uid,), "all")]


# ── stats ──
async def log_removal(**k):
    await q(
        "INSERT INTO removals(owner_id,chat_id,chat_title,user_id,username,full_name,premium,"
        "reason,action,source,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (k["owner_id"], k["chat_id"], k.get("chat_title"), k.get("user_id"), k.get("username"),
         k.get("full_name"), int(k.get("premium", 0)), k.get("reason"), k.get("action"),
         k.get("source"), now()),
    )
    await q("UPDATE clients SET total_removed=total_removed+1 WHERE user_id=?", (k["owner_id"],))
    await q("UPDATE channels SET removed=removed+1 WHERE chat_id=?", (k["chat_id"],))


async def stats_of(uid: int) -> dict:
    midnight = int(datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    out = {}
    for key, since in (("today", midnight), ("d7", now() - 7 * 86400), ("d30", now() - 30 * 86400)):
        r = await q("SELECT COUNT(*) c FROM removals WHERE owner_id=? AND at>=?", (uid, since), "one")
        out[key] = r["c"]
    out["total"] = (await q("SELECT COUNT(*) c FROM removals WHERE owner_id=?", (uid,), "one"))["c"]
    out["premium"] = (await q("SELECT COUNT(*) c FROM removals WHERE owner_id=? AND premium=1", (uid,), "one"))["c"]
    out["rows7"] = await q("SELECT at FROM removals WHERE owner_id=? AND at>=?", (uid, now() - 7 * 86400), "all")
    out["top"] = await q(
        "SELECT chat_title t, COUNT(*) c FROM removals WHERE owner_id=? GROUP BY chat_id "
        "ORDER BY c DESC LIMIT 5", (uid,), "all")
    return out


async def audit(actor: int, action: str, target: str = "", detail: str = ""):
    await q("INSERT INTO audit(actor,action,target,detail,at) VALUES(?,?,?,?,?)",
            (actor, action, target, detail, now()))


# ═══════════════════════════════════════════════════════════════
#  SECTION 2 ── UI HELPERS
# ═══════════════════════════════════════════════════════════════
def esc(t: Any) -> str:
    return html.escape(str(t if t is not None else ""))


def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def bar(v: int, mx: int, width: int = 10) -> str:
    if mx <= 0:
        return "░" * width
    n = max(0, min(width, round(v / mx * width)))
    return "█" * n + "░" * (width - n)


def plan_name(key: str) -> str:
    p = config.PLANS.get(key)
    return p["name"] if p else key


def user_link(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{esc(name)}</a>'


def kb(rows) -> M:
    return M(rows)


LINE = "━━━━━━━━━━━━━━━━━━━━━━"


async def safe_edit(qy, text: str, markup: M | None = None, preview: bool = False):
    try:
        await qy.edit_message_text(text, reply_markup=markup, parse_mode=HTML,
                                   disable_web_page_preview=not preview)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            try:
                await qy.message.reply_text(text, reply_markup=markup, parse_mode=HTML,
                                            disable_web_page_preview=True)
            except Exception:
                pass


async def dm(ctx, uid: int, text: str, markup: M | None = None) -> bool:
    try:
        await ctx.bot.send_message(uid, text, parse_mode=HTML, reply_markup=markup,
                                   disable_web_page_preview=True)
        return True
    except (Forbidden, BadRequest, TelegramError):
        return False


def st(ctx) -> dict:
    return ctx.user_data.setdefault("st", {})


def clear_st(ctx):
    ctx.user_data.pop("st", None)


# ═══════════════════════════════════════════════════════════════
#  SECTION 3 ── SCREENS  (text + keyboard builders)
# ═══════════════════════════════════════════════════════════════
def scr_guest(name: str) -> tuple[str, M]:
    plans = "\n".join(
        f"  {'🥉' if p['slots']<=2 else '🥈' if p['slots']<=5 else '🥇'} <b>{p['name']}</b>"
        f"  —  ₹{p['price']:,}/month"
        for p in config.PLANS.values()
    )
    t = (
        f"👋 <b>Welcome, {esc(name)}!</b>\n\n"
        f"🛡 <b>{config.BRAND}</b> — <i>Telegram Premium Guard</i>\n{LINE}\n"
        "Ye bot aapke channel ki <b>24×7 guarding</b> karta hai.\n"
        "Jaise hi koi <b>Telegram Premium</b> user aapke channel me\n"
        "join karega ya join-request bhejega — bot use turant\n"
        "<b>ban / kick / decline</b> kar dega aur aapko report bhejega.\n\n"
        "✨ <b>Features</b>\n"
        "  ⚡ Instant premium detection (0-delay)\n"
        "  🎯 Extra filters — no-username / no-DP / new account\n"
        "  ✅ Whitelist — apne logo ko safe rakhein\n"
        "  📊 Live stats + CSV export\n"
        "  🗂 Personal log channel\n"
        "  🔔 Expiry reminders\n\n"
        f"💳 <b>Plans</b> <i>(monthly)</i>\n{LINE}\n{plans}\n{LINE}\n"
        "🔐 Bot use karne ke liye subscription zaroori hai.\n"
        "Neeche button se plan choose karke request bhejein 👇"
    )
    k = kb([
        [B("💳 Buy Subscription", callback_data="g:plans")],
        [B("❓ How it works", callback_data="g:how"), B("💬 Contact Owner", url=config.SUPPORT_LINK)],
        [B("🆔 My Chat ID", callback_data="g:id")],
    ])
    return t, k


def scr_plans() -> tuple[str, M]:
    t = (f"💳 <b>Choose Your Plan</b>\n{LINE}\n"
         "Plan aapke <b>channel slots</b> ke hisaab se hai.\n"
         "Jitne channel protect karne hain, utna plan lein.\n\n"
         "Plan select karte hi request seedha owner ko chali jayegi.\n"
         f"{LINE}")
    rows, cur = [], []
    for key, p in config.PLANS.items():
        cur.append(B(f"{p['name']} • ₹{p['price']:,}", callback_data=f"g:req:{key}"))
        if len(cur) == 2:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    rows.append([B("💬 Custom / Bulk", url=config.SUPPORT_LINK)])
    rows.append([B("‹ Back", callback_data="g:home")])
    return t, kb(rows)


def scr_how() -> tuple[str, M]:
    t = (f"❓ <b>How {config.BRAND} Works</b>\n{LINE}\n"
         "<b>1️⃣ Subscription lein</b>\n"
         "   Plan choose karke request bhejein. Owner approve karega.\n\n"
         "<b>2️⃣ Bot ko channel me admin banayein</b>\n"
         "   Permissions: <code>Ban Users</code> + <code>Invite Users</code>\n"
         "   (join-request handle karne ke liye Invite Users zaroori hai)\n\n"
         "<b>3️⃣ Bas ho gaya ✅</b>\n"
         "   Channel automatically aapke panel me add ho jayega.\n"
         "   Ab har premium joiner turant remove hoga.\n\n"
         "<b>📌 Note</b>\n"
         "  • Channel admins kabhi ban nahi hote\n"
         "  • Whitelist wale users safe rehte hain\n"
         "  • Har action ka log aapke DM / log-channel me\n"
         f"{LINE}")
    return t, kb([[B("💳 Buy Subscription", callback_data="g:plans")],
                  [B("‹ Back", callback_data="g:home")]])


async def scr_client(uid: int, name: str) -> tuple[str, M]:
    c = await get_client(uid)
    chs = await channels_of(uid)
    dl = days_left(c)
    used, slots = len(chs), c["slots"]
    heat = "🟢" if dl > 7 else "🟡" if dl > 3 else "🔴"
    stat = await stats_of(uid)
    t = (
        f"🛡 <b>{config.BRAND} • Control Panel</b>\n{LINE}\n"
        f"👤 <b>{esc(name)}</b>  <code>{uid}</code>\n\n"
        f"💳 Plan     : <b>{plan_name(c['plan'])}</b>\n"
        f"{heat} Expires  : <b>{fdate(c['expires_at'])}</b>  ({dl:.1f} days)\n"
        f"📡 Channels : <b>{used}/{slots}</b>  {bar(used, slots, 8)}\n"
        f"🚫 Removed  : <b>{c['total_removed']}</b>  (today {stat['today']})\n"
        f"{LINE}\n"
    )
    if not chs:
        t += ("⚠️ <b>Abhi koi channel connect nahi hai.</b>\n"
              "Bot ko apne channel me <b>admin</b> banayein\n"
              "(<code>Ban Users</code> + <code>Invite Users</code>) —\n"
              "channel apne aap yahan add ho jayega. ✅")
    else:
        for ch in chs[:5]:
            dot = "🟢" if ch["active"] else "⏸"
            t += f"{dot} <b>{esc(ch['title'])[:26]}</b> — {ch['removed']} removed\n"
        if len(chs) > 5:
            t += f"<i>+{len(chs)-5} more…</i>\n"
    k = kb([
        [B("📡 My Channels", callback_data="c:chans:0"), B("📊 Statistics", callback_data="c:stats")],
        [B("🚫 Removed Log", callback_data="c:rem:0"), B("✅ Whitelist", callback_data="c:wl:0")],
        [B("⚙️ Settings", callback_data="c:set"), B("💳 Subscription", callback_data="c:sub")],
        [B("📥 Export CSV", callback_data="c:csv"), B("❓ Help", callback_data="c:help")],
        [B("🔄 Refresh", callback_data="c:home")],
    ])
    return t, k


async def scr_owner(ctx) -> tuple[str, M]:
    cl = await all_clients()
    act = [c for c in cl if is_active(c)]
    exp = [c for c in cl if c["expires_at"] <= now()]
    blk = [c for c in cl if c["is_blocked"]]
    ch = (await q("SELECT COUNT(*) c FROM channels", (), "one"))["c"]
    rem = (await q("SELECT COUNT(*) c FROM removals", (), "one"))["c"]
    midnight = int(datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today = (await q("SELECT COUNT(*) c FROM removals WHERE at>=?", (midnight,), "one"))["c"]
    users = (await q("SELECT COUNT(*) c FROM seen_users", (), "one"))["c"]
    mrr = sum(config.PLANS.get(c["plan"], {}).get("price", 0) for c in act)
    maint = await get_setting("maintenance", "0") == "1"
    t = (
        f"👑 <b>OWNER CONTROL CENTER</b>\n{LINE}\n"
        f"🏷 Bot   : <b>{config.BRAND}</b>\n"
        f"🔧 Mode  : {'🔴 <b>MAINTENANCE</b>' if maint else '🟢 <b>LIVE</b>'}\n{LINE}\n"
        f"👥 Clients   : <b>{len(cl)}</b>   ✅ {len(act)}  ⌛ {len(exp)}  ⛔ {len(blk)}\n"
        f"📡 Channels  : <b>{ch}</b>\n"
        f"🚫 Removed   : <b>{rem}</b>  (today <b>{today}</b>)\n"
        f"👀 Bot users : <b>{users}</b>\n"
        f"💰 Est. MRR  : <b>₹{mrr:,}</b>\n{LINE}"
    )
    k = kb([
        [B("➕ Add Client", callback_data="o:add"), B("👥 Manage Clients", callback_data="o:list:0")],
        [B("🔍 Search Client", callback_data="o:search"), B("📊 Global Stats", callback_data="o:stats")],
        [B("📣 Broadcast", callback_data="o:bc"), B("📡 All Channels", callback_data="o:chans:0")],
        [B("⌛ Expiring Soon", callback_data="o:exp"), B("📝 Audit Log", callback_data="o:audit")],
        [B(f"✨ Premium Emoji: {'ON' if EMOJI_ON else 'OFF'} ({len(EMOJI_MAP)})",
           callback_data="o:pe")],
        [B(f"🔧 Maintenance: {'ON' if maint else 'OFF'}", callback_data="o:maint")],
        [B("🗄 Backup DB", callback_data="o:backup"), B("🔄 Refresh", callback_data="o:home")],
    ])
    return t, k


# ═══════════════════════════════════════════════════════════════
#  SECTION 4 ── PROTECTION ENGINE
# ═══════════════════════════════════════════════════════════════
async def has_photo(ctx, uid: int) -> bool:
    try:
        p = await ctx.bot.get_user_profile_photos(uid, limit=1)
        return p.total_count > 0
    except Exception:
        return True  # error par safe side


async def evaluate(ctx, ch: dict, user) -> Optional[tuple[str, str]]:
    """Return (reason_code, reason_text) agar user remove hona chahiye."""
    if ch["f_premium"] and getattr(user, "is_premium", False):
        return "premium", "⭐ Telegram Premium user"
    if ch["f_nouser"] and not user.username:
        return "nouser", "🕵️ Username nahi hai"
    if ch["f_newacc"] and user.id >= config.NEW_ACCOUNT_ID:
        return "newacc", "🆕 Bahut naya account"
    if ch["f_words"]:
        words = await bw_list(ch["owner_id"])
        full = f"{user.full_name} {user.username or ''}".lower()
        for w in words:
            if w and w.lower() in full:
                return "word", f"🔤 Blacklisted word: <code>{esc(w)}</code>"
    if ch["f_nophoto"] and not await has_photo(ctx, user.id):
        return "nophoto", "🖼 Profile photo nahi hai"
    return None


async def is_protected(ctx, chat_id: int, owner_id: int, uid: int) -> bool:
    """Channel admin / owner / whitelist / bot-owner => never touch."""
    if uid == owner_id or is_admin(uid) or uid == ctx.bot.id:
        return True
    if await wl_has(owner_id, uid):
        return True
    try:
        m = await ctx.bot.get_chat_member(chat_id, uid)
        if m.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
            return True
    except Exception:
        pass
    return False


async def punish(ctx, ch: dict, user, reason_code: str, reason_txt: str,
                 source: str, join_req: bool = False) -> None:
    """Action lein + log karein + notify karein."""
    cid, owner = ch["chat_id"], ch["owner_id"]
    action = ch["action"] if ch["action"] in config.ACTIONS else config.DEFAULT_ACTION
    done, err = action, None

    try:
        if join_req:
            if action == "report":
                done = "report"
            else:
                try:
                    await ctx.bot.decline_chat_join_request(cid, user.id)
                except BadRequest:
                    pass
                if action == "ban":
                    await ctx.bot.ban_chat_member(cid, user.id)
                    done = "ban"
                elif action == "kick":
                    await ctx.bot.ban_chat_member(cid, user.id)
                    await ctx.bot.unban_chat_member(cid, user.id, only_if_banned=True)
                    done = "kick"
                else:
                    done = "decline"
        else:
            if action == "report":
                done = "report"
            elif action == "kick":
                await ctx.bot.ban_chat_member(cid, user.id)
                await ctx.bot.unban_chat_member(cid, user.id, only_if_banned=True)
                done = "kick"
            else:  # ban / decline (member already inside -> ban)
                await ctx.bot.ban_chat_member(cid, user.id)
                done = "ban"
    except Forbidden:
        err = "Bot ke paas <b>Ban Users</b> permission nahi hai."
    except BadRequest as e:
        err = esc(str(e))
    except TelegramError as e:
        err = esc(str(e))

    if err:
        await dm(ctx, owner,
                 f"⚠️ <b>Action Failed</b>\n{LINE}\n"
                 f"📡 <b>{esc(ch['title'])}</b>\n"
                 f"👤 {user_link(user.id, user.full_name)}\n"
                 f"❗ {err}\n{LINE}\n"
                 "👉 Bot ko <b>Ban Users</b> + <b>Invite Users</b> permission dein.")
        return

    await log_removal(owner_id=owner, chat_id=cid, chat_title=ch["title"], user_id=user.id,
                      username=user.username, full_name=user.full_name,
                      premium=reason_code == "premium", reason=reason_code,
                      action=done, source=source)

    icon = {"ban": "🔨", "kick": "👢", "decline": "🚫", "report": "📝"}.get(done, "🛡")
    verb = {"ban": "BANNED", "kick": "KICKED", "decline": "DECLINED", "report": "REPORTED"}[done]
    total = (await get_client(owner) or {}).get("total_removed", 0)
    card = (
        f"{icon} <b>{verb} — {'PREMIUM USER' if reason_code=='premium' else 'FILTERED USER'}</b>\n{LINE}\n"
        f"👤 Name    : {user_link(user.id, user.full_name)}\n"
        f"🔗 Username: {'@'+user.username if user.username else '—'}\n"
        f"🆔 User ID : <code>{user.id}</code>\n"
        f"⭐ Premium : {'<b>YES</b> ✅' if getattr(user,'is_premium',False) else 'No'}\n"
        f"📡 Channel : <b>{esc(ch['title'])}</b>\n"
        f"📥 Source  : {'Join Request' if source=='request' else 'Direct Join'}\n"
        f"🎯 Reason  : {reason_txt}\n"
        f"🕒 Time    : {fdatetime(now())}\n{LINE}\n"
        f"📊 Total removed: <b>{total}</b>"
    )
    cli = await get_client(owner)
    if cli and cli["notify_dm"]:
        await dm(ctx, owner, card)
    if cli and cli["log_channel"]:
        try:
            await ctx.bot.send_message(cli["log_channel"], card, parse_mode=HTML,
                                       disable_web_page_preview=True)
        except Exception:
            pass


# ── handler: someone joined ──
async def on_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if not cmu:
        return
    old, new = cmu.old_chat_member.status, cmu.new_chat_member.status
    joined = new in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED) and old in (
        ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)
    if not joined:
        return
    ch = await get_channel(cmu.chat.id)
    if not ch or not ch["active"]:
        return
    cli = await get_client(ch["owner_id"])
    if not is_active(cli):
        return
    user = cmu.new_chat_member.user
    if user.is_bot or await is_protected(ctx, ch["chat_id"], ch["owner_id"], user.id):
        return
    hit = await evaluate(ctx, ch, user)
    if hit:
        await punish(ctx, ch, user, hit[0], hit[1], "join")


# ── handler: join request ──
async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    jr = update.chat_join_request
    ch = await get_channel(jr.chat.id)
    if not ch or not ch["active"]:
        return
    cli = await get_client(ch["owner_id"])
    if not is_active(cli):
        return
    user = jr.from_user
    if await is_protected(ctx, ch["chat_id"], ch["owner_id"], user.id):
        if ch["auto_approve"]:
            try:
                await ctx.bot.approve_chat_join_request(jr.chat.id, user.id)
            except Exception:
                pass
        return
    hit = await evaluate(ctx, ch, user)
    if hit:
        await punish(ctx, ch, user, hit[0], hit[1], "request", join_req=True)
    elif ch["auto_approve"]:
        try:
            await ctx.bot.approve_chat_join_request(jr.chat.id, user.id)
        except Exception:
            pass


# ── handler: bot added/removed as admin ──
async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cmu = update.my_chat_member
    chat = cmu.chat
    if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP):
        return
    new = cmu.new_chat_member
    actor = cmu.from_user

    # removed
    if new.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        ch = await get_channel(chat.id)
        if ch:
            await q("DELETE FROM channels WHERE chat_id=?", (chat.id,))
            await dm(ctx, ch["owner_id"],
                     f"⚠️ <b>Channel Disconnected</b>\n{LINE}\n"
                     f"📡 <b>{esc(chat.title)}</b>\n"
                     "Bot ko channel se hata diya gaya hai.\n"
                     "Protection band ho gayi hai. Slot free ho gaya ✅")
        return

    if new.status != ChatMemberStatus.ADMINISTRATOR:
        return

    existing = await get_channel(chat.id)
    if existing:
        await upd_channel(chat.id, title=chat.title, username=chat.username, active=1)
        return

    cli = await get_client(actor.id)
    if not is_active(cli):
        await dm(ctx, actor.id,
                 f"❌ <b>Setup Failed</b>\n{LINE}\n"
                 f"📡 <b>{esc(chat.title)}</b>\n\n"
                 "Aapka koi <b>active subscription</b> nahi hai, isliye ye channel\n"
                 "connect nahi ho paya.\n\n"
                 "Pehle plan lein, phir bot ko dobara admin banayein.",
                 kb([[B("💳 Buy Subscription", callback_data="g:plans")],
                     [B("💬 Contact Owner", url=config.SUPPORT_LINK)]]))
        try:
            await ctx.bot.leave_chat(chat.id)
        except Exception:
            pass
        return

    used = len(await channels_of(actor.id))
    if used >= cli["slots"]:
        await dm(ctx, actor.id,
                 f"❌ <b>Slot Limit Reached</b>\n{LINE}\n"
                 f"📡 <b>{esc(chat.title)}</b>\n\n"
                 f"Aapka plan: <b>{plan_name(cli['plan'])}</b> — {cli['slots']} slot\n"
                 f"Use ho chuke: <b>{used}/{cli['slots']}</b>\n\n"
                 "Naya channel add karne ke liye plan upgrade karein\n"
                 "ya purana channel remove karein.",
                 kb([[B("⬆️ Upgrade Plan", callback_data="c:upgrade")],
                     [B("📡 My Channels", callback_data="c:chans:0")]]))
        try:
            await ctx.bot.leave_chat(chat.id)
        except Exception:
            pass
        return

    await q(
        "INSERT INTO channels(chat_id,owner_id,title,username,added_at,action) VALUES(?,?,?,?,?,?)",
        (chat.id, actor.id, chat.title, chat.username, now(), config.DEFAULT_ACTION))

    can_ban = bool(getattr(new, "can_restrict_members", False))
    can_inv = bool(getattr(new, "can_invite_users", False))
    warn = ""
    if not can_ban:
        warn += "\n⚠️ <b>Ban Users</b> permission missing — ban kaam nahi karega!"
    if not can_inv:
        warn += "\n⚠️ <b>Invite Users</b> permission missing — join-request detect nahi hoga!"

    await dm(ctx, actor.id,
             f"✅ <b>CHANNEL CONNECTED</b>\n{LINE}\n"
             f"📡 <b>{esc(chat.title)}</b>\n"
             f"🆔 <code>{chat.id}</code>\n"
             f"🎯 Action  : {config.ACTIONS[config.DEFAULT_ACTION]}\n"
             f"🛡 Filters : ⭐ Premium <b>ON</b>\n"
             f"📊 Slots   : <b>{used+1}/{cli['slots']}</b>\n{LINE}\n"
             f"🟢 <b>Protection ACTIVE hai!</b>\n"
             f"Ab koi bhi premium user join karega to turant remove ho jayega."
             + (f"\n{LINE}{warn}" if warn else ""),
             kb([[B("⚙️ Channel Settings", callback_data=f"c:ch:{chat.id}")],
                 [B("🏠 Panel", callback_data="c:home")]]))
    await dm(ctx, config.OWNER_ID,
             f"📡 <b>New Channel Connected</b>\n"
             f"👤 {user_link(actor.id, actor.full_name)} <code>{actor.id}</code>\n"
             f"📢 {esc(chat.title)} <code>{chat.id}</code>")
    await audit(actor.id, "channel_add", str(chat.id), chat.title or "")


# ═══════════════════════════════════════════════════════════════
#  SECTION 5 ── COMMANDS
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(ctx)
    await q("INSERT INTO seen_users(user_id,username,first_name,first_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,"
            "first_name=excluded.first_name", (u.id, u.username, u.first_name, now()))

    if is_admin(u.id):
        t, k = await scr_owner(ctx)
        await update.message.reply_text(t, reply_markup=k, parse_mode=HTML)
        return

    if await get_setting("maintenance", "0") == "1":
        await update.message.reply_text(
            f"🔧 <b>Maintenance Mode</b>\n{LINE}\n"
            "Bot abhi update ho raha hai. Thodi der baad try karein.\n"
            "Aapki protection background me chalti rahegi ✅",
            parse_mode=HTML)
        return

    c = await get_client(u.id)
    if c:
        await upd_client(u.id, username=u.username, first_name=u.first_name)
    if is_active(c):
        t, k = await scr_client(u.id, u.full_name)
    else:
        if c and c["is_blocked"]:
            t = (f"⛔ <b>Access Blocked</b>\n{LINE}\n"
                 "Aapka access owner ne block kiya hai.\nSupport se contact karein.")
            k = kb([[B("💬 Contact Owner", url=config.SUPPORT_LINK)]])
        elif c:
            t = (f"⌛ <b>Subscription Expired</b>\n{LINE}\n"
                 f"💳 Plan   : <b>{plan_name(c['plan'])}</b>\n"
                 f"📅 Expired: <b>{fdate(c['expires_at'])}</b>\n\n"
                 "Aapki protection band hai. Renew karke turant chalu karein 👇")
            k = kb([[B("🔁 Renew Now", callback_data="g:plans")],
                    [B("💬 Contact Owner", url=config.SUPPORT_LINK)]])
        else:
            t, k = scr_guest(u.full_name)
        await update.message.reply_text(t, reply_markup=k, parse_mode=HTML,
                                        disable_web_page_preview=True)
        return
    await update.message.reply_text(t, reply_markup=k, parse_mode=HTML,
                                    disable_web_page_preview=True)


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u, c = update.effective_user, update.effective_chat
    txt = f"🆔 <b>Your ID:</b> <code>{u.id}</code>"
    if c.type != ChatType.PRIVATE:
        txt += f"\n💬 <b>Chat ID:</b> <code>{c.id}</code>"
    await update.message.reply_text(txt, parse_mode=HTML)


async def cmd_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    base = (f"📖 <b>{config.BRAND} — Commands</b>\n{LINE}\n"
            "/start — Panel kholein\n"
            "/panel — Same as start\n"
            "/id — Apna chat ID\n"
            "/help — Ye message\n")
    if is_admin(u.id):
        base += (f"{LINE}\n👑 <b>Owner</b>\n"
                 "/add &lt;user_id&gt; &lt;days&gt; [plan] — Client add\n"
                 "/extend &lt;user_id&gt; &lt;days&gt; — Extend\n"
                 "/remove &lt;user_id&gt; — Delete client\n"
                 "/info &lt;user_id&gt; — Client details\n"
                 "/clients — Sab clients\n"
                 "/bc &lt;text&gt; — Broadcast\n"
                 "/backup — DB file\n"
                 "/emoji — ✨ Premium emoji setup\n")
    await update.message.reply_text(base, parse_mode=HTML)


# ── owner quick commands ──
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    a = ctx.args
    if len(a) < 2 or not a[0].lstrip("-").isdigit() or not a[1].isdigit():
        await update.message.reply_text(
            "📌 <b>Usage:</b> <code>/add &lt;user_id&gt; &lt;days&gt; [plan]</code>\n"
            "Plans: " + ", ".join(config.PLANS), parse_mode=HTML)
        return
    uid, days = int(a[0]), int(a[1])
    plan = a[2] if len(a) > 2 and a[2] in config.PLANS else config.DEFAULT_PLAN
    c = await add_client(uid, days, plan)
    await audit(update.effective_user.id, "add_client", str(uid), f"{days}d {plan}")
    await update.message.reply_text(await client_card(c), parse_mode=HTML)
    await notify_new_sub(ctx, c, days)


async def cmd_extend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    a = ctx.args
    if len(a) < 2:
        await update.message.reply_text("📌 <code>/extend &lt;user_id&gt; &lt;days&gt;</code>", parse_mode=HTML)
        return
    uid, days = int(a[0]), int(a[1])
    c = await get_client(uid)
    if not c:
        await update.message.reply_text("❌ Client nahi mila.")
        return
    await upd_client(uid, expires_at=max(now(), c["expires_at"]) + days * 86400,
                     last_reminder=0, expired_notified=0)
    c = await get_client(uid)
    await update.message.reply_text(f"✅ Extended +{days}d\n" + await client_card(c), parse_mode=HTML)
    await dm(ctx, uid, f"🎉 <b>Subscription Extended!</b>\n{LINE}\n"
                       f"➕ Added : <b>{days} days</b>\n"
                       f"📅 New expiry: <b>{fdate(c['expires_at'])}</b>\n"
                       f"⏳ Remaining : <b>{days_left(c):.1f} days</b>")


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("📌 <code>/remove &lt;user_id&gt;</code>", parse_mode=HTML)
        return
    uid = int(ctx.args[0])
    await del_client(uid)
    await update.message.reply_text(f"🗑 Client <code>{uid}</code> deleted.", parse_mode=HTML)
    await dm(ctx, uid, f"🗑 <b>Subscription Removed</b>\n{LINE}\n"
                       "Aapka subscription owner ne remove kar diya hai.\n"
                       "Sabhi channels disconnect ho gaye hain.")


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("📌 <code>/info &lt;user_id&gt;</code>", parse_mode=HTML)
        return
    c = await get_client(int(ctx.args[0]))
    if not c:
        await update.message.reply_text("❌ Not found.")
        return
    await update.message.reply_text(await client_card(c), parse_mode=HTML,
                                    reply_markup=client_kb(c["user_id"]))


async def cmd_clients(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    t, k = await owner_list_page(0)
    await update.message.reply_text(t, reply_markup=k, parse_mode=HTML)


async def cmd_bc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("📌 <code>/bc &lt;message&gt;</code>", parse_mode=HTML)
        return
    await do_broadcast(ctx, update.message, " ".join(ctx.args))


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await send_backup(ctx, update.effective_chat.id)


async def cmd_emoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """✨ Premium emoji panel + reply karke instant ID nikalna."""
    if not is_admin(update.effective_user.id):
        return
    rep = update.message.reply_to_message
    if rep:  # kisi premium-emoji message pe reply -> IDs dikhao/save karo
        raw = rep.text or rep.caption or ""
        ents = (rep.entities or []) + (rep.caption_entities or [])
        pairs = [(raw[e.offset:e.offset + e.length], e.custom_emoji_id)
                 for e in ents if e.type == "custom_emoji" and e.custom_emoji_id]
        if not pairs:
            await update.message.reply_text("❌ Us message me koi premium emoji nahi hai.")
            return
        for base, eid in pairs:
            await save_emoji(base, eid)
            await save_emoji(_plain_key(base), eid)
        lst = "\n".join(f"{b} → <code>{i}</code>" for b, i in pairs[:20])
        await update.message.reply_text(
            f"✅ <b>{len(pairs)} emoji saved</b>\n{LINE}\n{lst}", parse_mode=HTML)
        return
    total = len(config.EMOJI_SLOTS)
    done = sum(1 for e in config.EMOJI_SLOTS if e in EMOJI_MAP or _plain_key(e) in EMOJI_MAP)
    await update.message.reply_text(
        f"✨ <b>PREMIUM EMOJI</b>\n{LINE}\n"
        f"🔘 Status : {'🟢 ON' if EMOJI_ON else '⚪️ OFF'}\n"
        f"🗂 Mapped : <b>{done}/{total}</b>  {bar(done, total, 12)}\n{LINE}\n"
        "🪄 Premium emoji wale message pe <code>/emoji</code> reply karein —\n"
        "IDs turant save ho jayengi.",
        parse_mode=HTML,
        reply_markup=kb([[B("🪄 Auto-Map", callback_data="o:peauto")],
                         [B("✨ Full Panel", callback_data="o:pe")]]))


# ═══════════════════════════════════════════════════════════════
#  SECTION 6 ── OWNER HELPERS
# ═══════════════════════════════════════════════════════════════
async def client_card(c: dict) -> str:
    chs = await channels_of(c["user_id"])
    dl = days_left(c)
    status = ("⛔ BLOCKED" if c["is_blocked"] else
              "✅ ACTIVE" if c["expires_at"] > now() else "⌛ EXPIRED")
    lines = "\n".join(f"   • {esc(x['title'])[:24]} — {x['removed']}" for x in chs[:8]) or "   —"
    return (
        f"👤 <b>CLIENT DETAILS</b>\n{LINE}\n"
        f"🆔 ID      : <code>{c['user_id']}</code>\n"
        f"📛 Name    : {esc(c['first_name'] or '—')}\n"
        f"🔗 Username: {'@'+c['username'] if c['username'] else '—'}\n"
        f"📶 Status  : <b>{status}</b>\n"
        f"💳 Plan    : <b>{plan_name(c['plan'])}</b> (₹{config.PLANS.get(c['plan'],{}).get('price',0):,}/mo)\n"
        f"📡 Slots   : <b>{len(chs)}/{c['slots']}</b>\n"
        f"📅 Start   : {fdate(c['created_at'])}\n"
        f"⌛ Expiry  : <b>{fdate(c['expires_at'])}</b> ({dl:.1f}d left)\n"
        f"🚫 Removed : <b>{c['total_removed']}</b>\n"
        f"{LINE}\n📡 <b>Channels</b>\n{lines}\n{LINE}"
    )


def client_kb(uid: int) -> M:
    return kb([
        [B("➕7d", callback_data=f"o:ext:{uid}:7"), B("➕30d", callback_data=f"o:ext:{uid}:30"),
         B("➕90d", callback_data=f"o:ext:{uid}:90")],
        [B("📅 Custom Days", callback_data=f"o:extc:{uid}"), B("💳 Change Plan", callback_data=f"o:plan:{uid}")],
        [B("✉️ Message", callback_data=f"o:msg:{uid}"), B("⛔ Block / Unblock", callback_data=f"o:blk:{uid}")],
        [B("🗑 Delete Client", callback_data=f"o:del:{uid}")],
        [B("‹ Back to list", callback_data="o:list:0"), B("🏠 Home", callback_data="o:home")],
    ])


async def owner_list_page(page: int) -> tuple[str, M]:
    cl = await all_clients()
    ps = config.PAGE_SIZE
    pages = max(1, (len(cl) + ps - 1) // ps)
    page = max(0, min(page, pages - 1))
    chunk = cl[page * ps:(page + 1) * ps]
    t = f"👥 <b>CLIENTS</b>  <i>({len(cl)} total)</i>\n{LINE}\n"
    rows = []
    if not chunk:
        t += "Abhi koi client nahi hai.\nNeeche se add karein 👇"
    for c in chunk:
        dl = days_left(c)
        icon = "⛔" if c["is_blocked"] else "✅" if dl > 3 else "🟡" if dl > 0 else "⌛"
        nm = c["first_name"] or (("@" + c["username"]) if c["username"] else str(c["user_id"]))
        t += (f"{icon} <b>{esc(nm)[:18]}</b> <code>{c['user_id']}</code>\n"
              f"     {plan_name(c['plan'])} • {dl:.1f}d • {c['total_removed']} removed\n")
        rows.append([B(f"{icon} {nm[:20]}", callback_data=f"o:cli:{c['user_id']}")])
    nav = []
    if page > 0:
        nav.append(B("‹ Prev", callback_data=f"o:list:{page-1}"))
    nav.append(B(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(B("Next ›", callback_data=f"o:list:{page+1}"))
    rows.append(nav)
    rows.append([B("➕ Add Client", callback_data="o:add"), B("🏠 Home", callback_data="o:home")])
    return t, kb(rows)


async def notify_new_sub(ctx, c: dict, days: int):
    ok = await dm(
        ctx, c["user_id"],
        f"🎉 <b>SUBSCRIPTION ACTIVATED!</b>\n{LINE}\n"
        f"Congratulations! Aapka <b>{config.BRAND}</b> subscription active ho gaya hai.\n\n"
        f"💳 Plan    : <b>{plan_name(c['plan'])}</b>\n"
        f"📡 Channels: <b>{c['slots']}</b> tak add kar sakte hain\n"
        f"⏳ Validity: <b>{days} days</b>\n"
        f"📅 Expiry  : <b>{fdate(c['expires_at'])}</b>\n{LINE}\n"
        "🚀 <b>Ab kya karein?</b>\n"
        "1️⃣ Bot ko apne channel me <b>Admin</b> banayein\n"
        "2️⃣ Permissions dein: <code>Ban Users</code> + <code>Invite Users</code>\n"
        "3️⃣ Bas! Channel apne aap connect ho jayega ✅\n\n"
        "Uske baad har <b>Telegram Premium</b> user jo join karega\n"
        "ya request bhejega — turant remove ho jayega. 🛡",
        kb([[B("🏠 Open Panel", callback_data="c:home")],
            [B("❓ Setup Guide", callback_data="c:help")]]))
    if not ok:
        await dm(ctx, config.OWNER_ID,
                 f"⚠️ Client <code>{c['user_id']}</code> ko DM nahi ja paya "
                 "(usne bot start nahi kiya hai).")


async def send_backup(ctx, chat_id: int):
    try:
        await q("PRAGMA wal_checkpoint(FULL)")
        with open(config.DB_PATH, "rb") as f:
            data = f.read()
        await ctx.bot.send_document(
            chat_id, InputFile(io.BytesIO(data), filename=f"cprotect_{fdate(now()).replace(' ','')}.db"),
            caption=f"🗄 <b>Database Backup</b>\n📅 {fdatetime(now())}\n📦 {len(data)/1024:.1f} KB",
            parse_mode=HTML)
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"❌ Backup failed: {esc(e)}", parse_mode=HTML)


async def do_broadcast(ctx, msg, text: str):
    cl = await all_clients()
    ok = fail = 0
    prog = await msg.reply_text(f"📣 Broadcasting to {len(cl)} clients…")
    for c in cl:
        if await dm(ctx, c["user_id"], f"📣 <b>Announcement</b>\n{LINE}\n{text}"):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(0.05)
    await prog.edit_text(f"📣 <b>Broadcast Done</b>\n{LINE}\n✅ Sent: <b>{ok}</b>\n❌ Failed: <b>{fail}</b>",
                         parse_mode=HTML)


# ═══════════════════════════════════════════════════════════════
#  SECTION 7 ── CALLBACK ROUTER
# ═══════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qy = update.callback_query
    u = qy.from_user
    data = qy.data or ""
    await qy.answer()
    try:
        if data == "noop":
            return
        if data.startswith("g:"):
            await cb_guest(qy, ctx, u, data[2:])
        elif data.startswith("c:"):
            await cb_client(qy, ctx, u, data[2:])
        elif data.startswith("o:"):
            if not is_admin(u.id):
                await qy.answer("⛔ Owner only", show_alert=True)
                return
            await cb_owner(qy, ctx, u, data[2:])
    except Exception:
        log.error("callback error\n%s", traceback.format_exc())
        try:
            await qy.answer("⚠️ Error — dobara try karein", show_alert=True)
        except Exception:
            pass


# ── guest ──
async def cb_guest(qy, ctx, u, act: str):
    if act == "home":
        c = await get_client(u.id)
        if is_admin(u.id):
            t, k = await scr_owner(ctx)
        elif is_active(c):
            t, k = await scr_client(u.id, u.full_name)
        else:
            t, k = scr_guest(u.full_name)
        await safe_edit(qy, t, k)
    elif act == "plans":
        t, k = scr_plans()
        await safe_edit(qy, t, k)
    elif act == "how":
        t, k = scr_how()
        await safe_edit(qy, t, k)
    elif act == "id":
        await qy.answer(f"Your Chat ID: {u.id}", show_alert=True)
    elif act.startswith("req:"):
        key = act.split(":")[1]
        p = config.PLANS.get(key)
        if not p:
            return
        await dm(ctx, config.OWNER_ID,
                 f"🔔 <b>NEW SUBSCRIPTION REQUEST</b>\n{LINE}\n"
                 f"👤 {user_link(u.id, u.full_name)}\n"
                 f"🔗 {'@'+u.username if u.username else '—'}\n"
                 f"🆔 <code>{u.id}</code>\n"
                 f"💳 Plan : <b>{p['name']}</b> — ₹{p['price']:,}/mo\n"
                 f"🕒 {fdatetime(now())}\n{LINE}",
                 kb([[B(f"✅ Activate 30d ({p['name']})", callback_data=f"o:quick:{u.id}:{key}:30")],
                     [B("📅 Custom", callback_data=f"o:addfor:{u.id}:{key}"),
                      B("👤 Profile", callback_data=f"o:cli:{u.id}")]]))
        await safe_edit(
            qy,
            f"✅ <b>Request Sent!</b>\n{LINE}\n"
            f"💳 Plan: <b>{p['name']}</b> — ₹{p['price']:,}/month\n"
            f"🆔 Aapka ID: <code>{u.id}</code>\n\n"
            "Owner ko notification chali gayi hai.\n"
            "Payment aur activation ke liye owner se baat karein 👇",
            kb([[B("💬 Contact Owner", url=config.SUPPORT_LINK)],
                [B("‹ Back", callback_data="g:plans")]]))


# ── client ──
async def cb_client(qy, ctx, u, act: str):
    c = await get_client(u.id)
    if not is_active(c) and not is_admin(u.id):
        t, k = scr_guest(u.full_name)
        await safe_edit(qy, t, k)
        return
    uid = u.id
    parts = act.split(":")
    head = parts[0]

    if head == "home":
        clear_st(ctx)
        t, k = await scr_client(uid, u.full_name)
        await safe_edit(qy, t, k)

    elif head == "chans":
        page = int(parts[1]) if len(parts) > 1 else 0
        chs = await channels_of(uid)
        t = (f"📡 <b>MY CHANNELS</b>  ({len(chs)}/{c['slots']})\n{LINE}\n")
        rows = []
        if not chs:
            t += ("Koi channel connect nahi hai.\n\n"
                  "<b>Add karne ka tarika:</b>\n"
                  "1️⃣ Channel → Administrators → Add Admin\n"
                  "2️⃣ Bot ko select karein\n"
                  "3️⃣ <code>Ban Users</code> + <code>Invite Users</code> ON\n"
                  "4️⃣ Save — channel yahan aa jayega ✅")
        for ch in chs:
            dot = "🟢" if ch["active"] else "⏸"
            t += (f"{dot} <b>{esc(ch['title'])}</b>\n"
                  f"     🎯 {config.ACTIONS[ch['action']].split(' ',1)[1]} • 🚫 {ch['removed']} removed\n")
            rows.append([B(f"{dot} {ch['title'][:24]}", callback_data=f"c:ch:{ch['chat_id']}")])
        if len(chs) < c["slots"]:
            t += f"\n➕ <b>{c['slots']-len(chs)} slot free</b> — bot ko naye channel me admin banayein."
        else:
            t += "\n⚠️ Saare slots full hain. Upgrade karein ya koi channel remove karein."
            rows.append([B("⬆️ Upgrade Plan", callback_data="c:upgrade")])
        rows.append([B("🏠 Home", callback_data="c:home")])
        await safe_edit(qy, t, kb(rows))

    elif head == "ch":
        cid = int(parts[1])
        ch = await get_channel(cid)
        if not ch or ch["owner_id"] != uid:
            await qy.answer("❌ Not found", show_alert=True)
            return
        on = lambda v: "🟢 ON" if v else "⚪️ OFF"
        t = (f"⚙️ <b>CHANNEL SETTINGS</b>\n{LINE}\n"
             f"📡 <b>{esc(ch['title'])}</b>\n"
             f"🆔 <code>{ch['chat_id']}</code>\n"
             f"📅 Added : {fdate(ch['added_at'])}\n"
             f"🚫 Removed: <b>{ch['removed']}</b>\n"
             f"📶 Status : {'🟢 Protecting' if ch['active'] else '⏸ Paused'}\n{LINE}\n"
             f"🎯 <b>Action</b>: {config.ACTIONS[ch['action']]}\n{LINE}\n"
             f"🛡 <b>Filters</b>\n"
             f"  ⭐ Premium user      {on(ch['f_premium'])}\n"
             f"  🕵️ No username       {on(ch['f_nouser'])}\n"
             f"  🖼 No profile photo  {on(ch['f_nophoto'])}\n"
             f"  🆕 New account       {on(ch['f_newacc'])}\n"
             f"  🔤 Name blacklist    {on(ch['f_words'])}\n"
             f"  ✅ Auto-approve rest {on(ch['auto_approve'])}\n{LINE}")
        k = kb([
            [B(f"⭐ Premium {on(ch['f_premium'])}", callback_data=f"c:tg:{cid}:f_premium")],
            [B(f"🕵️ No username {on(ch['f_nouser'])}", callback_data=f"c:tg:{cid}:f_nouser"),
             B(f"🖼 No DP {on(ch['f_nophoto'])}", callback_data=f"c:tg:{cid}:f_nophoto")],
            [B(f"🆕 New acc {on(ch['f_newacc'])}", callback_data=f"c:tg:{cid}:f_newacc"),
             B(f"🔤 Words {on(ch['f_words'])}", callback_data=f"c:tg:{cid}:f_words")],
            [B(f"✅ Auto-approve {on(ch['auto_approve'])}", callback_data=f"c:tg:{cid}:auto_approve")],
            [B("🎯 Change Action", callback_data=f"c:act:{cid}")],
            [B("⏸ Pause" if ch["active"] else "▶️ Resume", callback_data=f"c:tg:{cid}:active"),
             B("🗑 Remove", callback_data=f"c:chdel:{cid}")],
            [B("‹ Channels", callback_data="c:chans:0"), B("🏠 Home", callback_data="c:home")],
        ])
        await safe_edit(qy, t, k)

    elif head == "tg":
        cid, flag = int(parts[1]), parts[2]
        ch = await get_channel(cid)
        if not ch or ch["owner_id"] != uid:
            return
        await upd_channel(cid, **{flag: 0 if ch[flag] else 1})
        await cb_client(qy, ctx, u, f"ch:{cid}")

    elif head == "act":
        cid = int(parts[1])
        rows = [[B(v, callback_data=f"c:setact:{cid}:{k}")] for k, v in config.ACTIONS.items()]
        rows.append([B("‹ Back", callback_data=f"c:ch:{cid}")])
        await safe_edit(qy,
                        f"🎯 <b>Choose Action</b>\n{LINE}\n"
                        "Filter match hone par bot kya kare?\n\n"
                        "🔨 <b>Ban</b> — hamesha ke liye block\n"
                        "👢 <b>Kick</b> — nikal dega, dobara join kar sakta hai\n"
                        "🚫 <b>Decline</b> — sirf join request reject\n"
                        "📝 <b>Report</b> — kuch nahi karega, sirf batayega\n"
                        f"{LINE}", kb(rows))

    elif head == "setact":
        cid, a = int(parts[1]), parts[2]
        if a in config.ACTIONS:
            await upd_channel(cid, action=a)
        await cb_client(qy, ctx, u, f"ch:{cid}")

    elif head == "chdel":
        cid = int(parts[1])
        await safe_edit(qy, f"🗑 <b>Remove Channel?</b>\n{LINE}\nProtection band ho jayegi aur slot free ho jayega.",
                        kb([[B("✅ Yes, remove", callback_data=f"c:chdel2:{cid}"),
                             B("❌ Cancel", callback_data=f"c:ch:{cid}")]]))

    elif head == "chdel2":
        cid = int(parts[1])
        ch = await get_channel(cid)
        if ch and ch["owner_id"] == uid:
            await q("DELETE FROM channels WHERE chat_id=?", (cid,))
            try:
                await ctx.bot.leave_chat(cid)
            except Exception:
                pass
        await cb_client(qy, ctx, u, "chans:0")

    elif head == "stats":
        s = await stats_of(uid)
        buckets = [0] * 7
        for r in s["rows7"]:
            d = (now() - r["at"]) // 86400
            if 0 <= d < 7:
                buckets[6 - d] += 1
        mx = max(buckets) or 1
        graph = ""
        for i, v in enumerate(buckets):
            day = (datetime.now(config.TZ) - timedelta(days=6 - i)).strftime("%a")
            graph += f"  {day} │{bar(v, mx, 12)} {v}\n"
        top = "".join(f"  • {esc(r['t'])[:22]} — <b>{r['c']}</b>\n" for r in s["top"]) or "  —\n"
        t = (f"📊 <b>STATISTICS</b>\n{LINE}\n"
             f"📅 Today      : <b>{s['today']}</b>\n"
             f"🗓 Last 7 days: <b>{s['d7']}</b>\n"
             f"📆 Last 30 days: <b>{s['d30']}</b>\n"
             f"🏆 All time   : <b>{s['total']}</b>\n"
             f"⭐ Premium removed: <b>{s['premium']}</b>\n{LINE}\n"
             f"📈 <b>Last 7 Days</b>\n{graph}{LINE}\n"
             f"🏅 <b>Top Channels</b>\n{top}{LINE}")
        await safe_edit(qy, t, kb([[B("📥 Export CSV", callback_data="c:csv"),
                                    B("🚫 Removed Log", callback_data="c:rem:0")],
                                   [B("🔄 Refresh", callback_data="c:stats"),
                                    B("🏠 Home", callback_data="c:home")]]))

    elif head == "rem":
        page = int(parts[1]) if len(parts) > 1 else 0
        per = 8
        rows_ = await q("SELECT * FROM removals WHERE owner_id=? ORDER BY at DESC LIMIT ? OFFSET ?",
                        (uid, per, page * per), "all")
        total = (await q("SELECT COUNT(*) c FROM removals WHERE owner_id=?", (uid,), "one"))["c"]
        pages = max(1, (total + per - 1) // per)
        t = f"🚫 <b>REMOVED USERS</b>  <i>({total})</i>\n{LINE}\n"
        if not rows_:
            t += "Abhi tak koi remove nahi hua. 🎉"
        for r in rows_:
            ic = {"ban": "🔨", "kick": "👢", "decline": "🚫", "report": "📝"}.get(r["action"], "🛡")
            t += (f"{ic} {esc(r['full_name'] or '—')[:20]} "
                  f"{'⭐' if r['premium'] else ''}\n"
                  f"     <code>{r['user_id']}</code> • {esc(r['chat_title'] or '')[:16]}\n"
                  f"     🕒 {fdatetime(r['at'])}\n")
        nav = []
        if page > 0:
            nav.append(B("‹ Prev", callback_data=f"c:rem:{page-1}"))
        nav.append(B(f"{page+1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(B("Next ›", callback_data=f"c:rem:{page+1}"))
        await safe_edit(qy, t, kb([nav, [B("📥 Export CSV", callback_data="c:csv")],
                                   [B("🏠 Home", callback_data="c:home")]]))

    elif head == "wl":
        rows_ = await wl_list(uid)
        t = (f"✅ <b>WHITELIST</b>  <i>({len(rows_)})</i>\n{LINE}\n"
             "Whitelist wale users kabhi remove nahi honge —\n"
             "chahe wo Premium hi kyun na ho.\n"
             "<i>(Channel admins automatically safe hain)</i>\n" + LINE + "\n")
        btns = []
        if not rows_:
            t += "Abhi list khali hai."
        for r in rows_[:20]:
            t += f"  • <code>{r['user_id']}</code> {esc(r['label'] or '')}\n"
            btns.append([B(f"🗑 {r['user_id']} {r['label'][:12] if r['label'] else ''}",
                           callback_data=f"c:wldel:{r['user_id']}")])
        btns.append([B("➕ Add to Whitelist", callback_data="c:wladd")])
        btns.append([B("🏠 Home", callback_data="c:home")])
        await safe_edit(qy, t, kb(btns))

    elif head == "wladd":
        st(ctx).update(k="wl_add")
        await safe_edit(qy, f"➕ <b>Add to Whitelist</b>\n{LINE}\n"
                            "User ka <b>numeric ID</b> bhejein.\n"
                            "Optional label bhi de sakte hain:\n"
                            "<code>123456789 Mera Dost</code>\n\n"
                            "Ek saath kai IDs bhi bhej sakte hain (nayi line me).",
                        kb([[B("✖️ Cancel", callback_data="c:wl:0")]]))

    elif head == "wldel":
        await q("DELETE FROM whitelist WHERE owner_id=? AND user_id=?", (uid, int(parts[1])))
        await cb_client(qy, ctx, u, "wl:0")

    elif head == "set":
        lc = c["log_channel"]
        words = await bw_list(uid)
        t = (f"⚙️ <b>SETTINGS</b>\n{LINE}\n"
             f"🔔 DM notifications : {'🟢 ON' if c['notify_dm'] else '⚪️ OFF'}\n"
             f"🗂 Log channel      : {'<code>'+str(lc)+'</code>' if lc else '❌ Not set'}\n"
             f"🔤 Blacklist words  : <b>{len(words)}</b>\n{LINE}\n"
             "<b>DM notifications</b> — har ban ka card aapke DM me.\n"
             "<b>Log channel</b> — private channel me saare logs.\n"
             "<b>Blacklist</b> — naam me ye word ho to remove.\n" + LINE)
        await safe_edit(qy, t, kb([
            [B(f"🔔 DM alerts: {'ON' if c['notify_dm'] else 'OFF'}", callback_data="c:tgdm")],
            [B("🗂 Set Log Channel", callback_data="c:logset")] +
            ([B("🗑 Clear", callback_data="c:logdel")] if lc else []),
            [B("🔤 Blacklist Words", callback_data="c:words")],
            [B("🏠 Home", callback_data="c:home")]]))

    elif head == "tgdm":
        await upd_client(uid, notify_dm=0 if c["notify_dm"] else 1)
        await cb_client(qy, ctx, u, "set")

    elif head == "logset":
        st(ctx).update(k="log_set")
        await safe_edit(qy, f"🗂 <b>Set Log Channel</b>\n{LINE}\n"
                            "<b>Steps:</b>\n"
                            "1️⃣ Ek private channel banayein\n"
                            "2️⃣ Bot ko usme <b>admin</b> banayein (post permission)\n"
                            "3️⃣ Uska ID yahan bhejein — jaise <code>-1001234567890</code>\n\n"
                            "<i>Tip: channel se koi message is bot ko forward karein, "
                            "bot ID bata dega.</i>",
                        kb([[B("✖️ Cancel", callback_data="c:set")]]))

    elif head == "logdel":
        await upd_client(uid, log_channel=None)
        await cb_client(qy, ctx, u, "set")

    elif head == "words":
        words = await bw_list(uid)
        t = (f"🔤 <b>NAME BLACKLIST</b>  <i>({len(words)})</i>\n{LINE}\n"
             "Agar joiner ke naam/username me ye words honge\n"
             "to wo remove ho jayega (filter ON hone par).\n" + LINE + "\n")
        t += ("  " + ", ".join(f"<code>{esc(w)}</code>" for w in words)) if words else "  Khali hai."
        await safe_edit(qy, t, kb([[B("➕ Add Words", callback_data="c:wordadd")],
                                   [B("🗑 Clear All", callback_data="c:wordclr")],
                                   [B("‹ Settings", callback_data="c:set")]]))

    elif head == "wordadd":
        st(ctx).update(k="word_add")
        await safe_edit(qy, f"➕ <b>Add Blacklist Words</b>\n{LINE}\n"
                            "Comma se alag karke bhejein:\n"
                            "<code>casino, betting, xxx, promo</code>",
                        kb([[B("✖️ Cancel", callback_data="c:words")]]))

    elif head == "wordclr":
        await q("DELETE FROM badwords WHERE owner_id=?", (uid,))
        await cb_client(qy, ctx, u, "words")

    elif head == "sub":
        chs = await channels_of(uid)
        p = config.PLANS.get(c["plan"], {})
        t = (f"💳 <b>MY SUBSCRIPTION</b>\n{LINE}\n"
             f"👤 <code>{uid}</code>\n"
             f"💳 Plan     : <b>{plan_name(c['plan'])}</b>\n"
             f"💰 Price    : ₹{p.get('price',0):,}/month\n"
             f"📡 Channels : <b>{len(chs)}/{c['slots']}</b>\n"
             f"📅 Started  : {fdate(c['created_at'])}\n"
             f"⌛ Expires  : <b>{fdate(c['expires_at'])}</b>\n"
             f"⏳ Remaining: <b>{days_left(c):.1f} days</b>\n"
             f"🚫 Total removed: <b>{c['total_removed']}</b>\n{LINE}\n"
             f"{'🟢 <b>Active & Protecting</b>' if days_left(c) > 3 else '🟡 <b>Expiring soon — renew karein!</b>'}")
        await safe_edit(qy, t, kb([[B("🔁 Renew / Extend", callback_data="c:upgrade")],
                                   [B("💬 Contact Owner", url=config.SUPPORT_LINK)],
                                   [B("🏠 Home", callback_data="c:home")]]))

    elif head == "upgrade":
        t, k = scr_plans()
        await safe_edit(qy, "⬆️ <b>Upgrade / Renew</b>\n" + t.split("\n", 1)[1], k)

    elif head == "csv":
        rows_ = await q("SELECT * FROM removals WHERE owner_id=? ORDER BY at DESC", (uid,), "all")
        if not rows_:
            await qy.answer("Koi data nahi hai 🤷", show_alert=True)
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "User ID", "Name", "Username", "Premium", "Channel", "Reason", "Action", "Source"])
        for r in rows_:
            w.writerow([fdatetime(r["at"]), r["user_id"], r["full_name"], r["username"],
                        "YES" if r["premium"] else "NO", r["chat_title"], r["reason"],
                        r["action"], r["source"]])
        data = buf.getvalue().encode()
        await ctx.bot.send_document(
            uid, InputFile(io.BytesIO(data), filename=f"removed_{uid}.csv"),
            caption=f"📥 <b>Export</b> — {len(rows_)} records\n📅 {fdatetime(now())}",
            parse_mode=HTML)
        await qy.answer("✅ CSV bhej diya")

    elif head == "help":
        t = (f"❓ <b>HELP & SETUP</b>\n{LINE}\n"
             "<b>🔧 Channel connect karein</b>\n"
             "  1. Channel → Administrators → Add Admin\n"
             "  2. Is bot ko select karein\n"
             "  3. ON karein: <code>Ban Users</code>, <code>Invite Users</code>\n"
             "  4. Save → channel auto-connect ✅\n\n"
             "<b>🎯 Action modes</b>\n"
             "  🔨 Ban — permanent block\n"
             "  👢 Kick — nikal dega (rejoin possible)\n"
             "  🚫 Decline — sirf request reject\n"
             "  📝 Report — sirf notify\n\n"
             "<b>🛡 Filters</b>\n"
             "  ⭐ Premium — Telegram Premium users\n"
             "  🕵️ No username — bina @username\n"
             "  🖼 No DP — bina profile photo\n"
             "  🆕 New account — bilkul naye accounts\n"
             "  🔤 Blacklist — naam me banned word\n\n"
             "<b>✅ Kaun safe hai?</b>\n"
             "  • Channel owner & admins\n"
             "  • Aapki whitelist ke users\n\n"
             "<b>⚠️ Kaam nahi kar raha?</b>\n"
             "  • Bot ko <b>Ban Users</b> permission di?\n"
             "  • Join request ke liye <b>Invite Users</b> chahiye\n"
             "  • Subscription active hai?\n"
             f"{LINE}")
        await safe_edit(qy, t, kb([[B("💬 Support", url=config.SUPPORT_LINK)],
                                   [B("🏠 Home", callback_data="c:home")]]))


# ── owner ──
async def cb_owner(qy, ctx, u, act: str):
    global EMOJI_ON, EMOJI_SUPPORTED
    parts = act.split(":")
    head = parts[0]

    if head == "home":
        clear_st(ctx)
        t, k = await scr_owner(ctx)
        await safe_edit(qy, t, k)

    elif head == "add":
        st(ctx).update(k="add_id")
        await safe_edit(qy, f"➕ <b>ADD CLIENT — Step 1/3</b>\n{LINE}\n"
                            "Client ka <b>Chat ID</b> bhejein.\n\n"
                            "<i>Client /id command se apna ID nikal sakta hai,\n"
                            "ya uska message yahan forward karein.</i>",
                        kb([[B("✖️ Cancel", callback_data="o:home")]]))

    elif head == "addfor":
        target, plan = int(parts[1]), parts[2]
        st(ctx).update(k="add_days", uid=target, plan=plan)
        await safe_edit(qy, f"➕ <b>ADD CLIENT — Step 3/3</b>\n{LINE}\n"
                            f"🆔 <code>{target}</code>\n"
                            f"💳 {plan_name(plan)}\n\n"
                            "Ab <b>kitne din</b> ka subscription? (number bhejein)",
                        kb([[B("7", callback_data=f"o:addd:{target}:{plan}:7"),
                             B("30", callback_data=f"o:addd:{target}:{plan}:30"),
                             B("90", callback_data=f"o:addd:{target}:{plan}:90"),
                             B("365", callback_data=f"o:addd:{target}:{plan}:365")],
                            [B("✖️ Cancel", callback_data="o:home")]]))

    elif head in ("addd", "quick"):
        target, plan, days = int(parts[1]), parts[2], int(parts[3])
        clear_st(ctx)
        c = await add_client(target, days, plan)
        await audit(u.id, "add_client", str(target), f"{days}d {plan}")
        await safe_edit(qy, f"✅ <b>SUBSCRIPTION CREATED</b>\n{LINE}\n" + await client_card(c),
                        client_kb(target))
        await notify_new_sub(ctx, c, days)

    elif head == "list":
        t, k = await owner_list_page(int(parts[1]))
        await safe_edit(qy, t, k)

    elif head == "cli":
        target = int(parts[1])
        c = await get_client(target)
        if not c:
            await qy.answer("❌ Client not found", show_alert=True)
            return
        await safe_edit(qy, await client_card(c), client_kb(target))

    elif head == "ext":
        target, days = int(parts[1]), int(parts[2])
        c = await get_client(target)
        if not c:
            return
        await upd_client(target, expires_at=max(now(), c["expires_at"]) + days * 86400,
                         last_reminder=0, expired_notified=0)
        c = await get_client(target)
        await audit(u.id, "extend", str(target), f"+{days}d")
        await safe_edit(qy, f"✅ <b>Extended +{days} days</b>\n{LINE}\n" + await client_card(c),
                        client_kb(target))
        await dm(ctx, target,
                 f"🎉 <b>Subscription Extended!</b>\n{LINE}\n"
                 f"➕ Added   : <b>{days} days</b>\n"
                 f"📅 New expiry: <b>{fdate(c['expires_at'])}</b>\n"
                 f"⏳ Remaining : <b>{days_left(c):.1f} days</b>\n{LINE}\n"
                 "Protection continue hai 🟢")

    elif head == "extc":
        target = int(parts[1])
        st(ctx).update(k="ext_days", uid=target)
        await safe_edit(qy, f"📅 <b>Custom Extension</b>\n{LINE}\n"
                            f"🆔 <code>{target}</code>\n\nKitne din add karne hain? Number bhejein.",
                        kb([[B("✖️ Cancel", callback_data=f"o:cli:{target}")]]))

    elif head == "plan":
        target = int(parts[1])
        rows, cur = [], []
        for k_, p in config.PLANS.items():
            cur.append(B(f"{p['name']} ₹{p['price']:,}", callback_data=f"o:setplan:{target}:{k_}"))
            if len(cur) == 2:
                rows.append(cur); cur = []
        if cur:
            rows.append(cur)
        rows.append([B("‹ Back", callback_data=f"o:cli:{target}")])
        await safe_edit(qy, f"💳 <b>Change Plan</b>\n{LINE}\n🆔 <code>{target}</code>\n\n"
                            "Naya plan choose karein (slots turant update honge):", kb(rows))

    elif head == "setplan":
        target, plan = int(parts[1]), parts[2]
        slots = config.PLANS[plan]["slots"]
        await upd_client(target, plan=plan, slots=slots)
        c = await get_client(target)
        await audit(u.id, "change_plan", str(target), plan)
        await safe_edit(qy, f"✅ <b>Plan Updated</b>\n{LINE}\n" + await client_card(c), client_kb(target))
        await dm(ctx, target, f"💳 <b>Plan Updated</b>\n{LINE}\n"
                              f"Naya plan: <b>{plan_name(plan)}</b>\n"
                              f"📡 Ab aap <b>{slots}</b> channel connect kar sakte hain.")

    elif head == "blk":
        target = int(parts[1])
        c = await get_client(target)
        if not c:
            return
        newv = 0 if c["is_blocked"] else 1
        await upd_client(target, is_blocked=newv)
        c = await get_client(target)
        await audit(u.id, "block" if newv else "unblock", str(target))
        await safe_edit(qy, f"{'⛔ <b>Blocked</b>' if newv else '✅ <b>Unblocked</b>'}\n{LINE}\n"
                        + await client_card(c), client_kb(target))
        await dm(ctx, target, "⛔ <b>Access Blocked</b>\nAapka access owner ne band kar diya hai."
                 if newv else "✅ <b>Access Restored</b>\nAapka access wapas chalu ho gaya hai 🟢")

    elif head == "del":
        target = int(parts[1])
        await safe_edit(qy, f"🗑 <b>Delete Client?</b>\n{LINE}\n"
                            f"🆔 <code>{target}</code>\n\n"
                            "Subscription, channels aur whitelist sab delete ho jayenge.\n"
                            "<b>Ye undo nahi ho sakta.</b>",
                        kb([[B("✅ Yes, delete", callback_data=f"o:del2:{target}"),
                             B("❌ Cancel", callback_data=f"o:cli:{target}")]]))

    elif head == "del2":
        target = int(parts[1])
        chs = await channels_of(target)
        await del_client(target)
        for ch in chs:
            try:
                await ctx.bot.leave_chat(ch["chat_id"])
            except Exception:
                pass
        await audit(u.id, "delete_client", str(target))
        await dm(ctx, target, f"🗑 <b>Subscription Deleted</b>\n{LINE}\n"
                              "Aapka subscription remove kar diya gaya hai.\n"
                              "Sabhi channels disconnect ho gaye. Renew karne ke liye contact karein.")
        t, k = await owner_list_page(0)
        await safe_edit(qy, f"✅ Client <code>{target}</code> deleted.\n\n" + t, k)

    elif head == "msg":
        target = int(parts[1])
        st(ctx).update(k="msg_client", uid=target)
        await safe_edit(qy, f"✉️ <b>Message Client</b>\n{LINE}\n🆔 <code>{target}</code>\n\n"
                            "Jo message bhejna hai wo type karein:",
                        kb([[B("✖️ Cancel", callback_data=f"o:cli:{target}")]]))

    elif head == "search":
        st(ctx).update(k="search")
        await safe_edit(qy, f"🔍 <b>Search Client</b>\n{LINE}\n"
                            "User ID, username ya naam bhejein:",
                        kb([[B("✖️ Cancel", callback_data="o:home")]]))

    elif head == "bc":
        st(ctx).update(k="broadcast")
        n = len(await all_clients())
        await safe_edit(qy, f"📣 <b>Broadcast</b>\n{LINE}\n"
                            f"👥 Recipients: <b>{n}</b> clients\n\n"
                            "Message type karein (HTML allowed):",
                        kb([[B("✖️ Cancel", callback_data="o:home")]]))

    elif head == "stats":
        cl = await all_clients()
        act = [c for c in cl if is_active(c)]
        pdist = ""
        for k_, p in config.PLANS.items():
            n = len([c for c in act if c["plan"] == k_])
            if n:
                pdist += f"  {p['name']:<12} × {n}  = ₹{p['price']*n:,}\n"
        midnight = int(datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        d = {}
        for key, since in (("today", midnight), ("d7", now() - 7 * 86400), ("d30", now() - 30 * 86400)):
            d[key] = (await q("SELECT COUNT(*) c FROM removals WHERE at>=?", (since,), "one"))["c"]
        tot = (await q("SELECT COUNT(*) c FROM removals", (), "one"))["c"]
        prem = (await q("SELECT COUNT(*) c FROM removals WHERE premium=1", (), "one"))["c"]
        topc = await q("SELECT chat_title t, COUNT(*) c FROM removals GROUP BY chat_id "
                       "ORDER BY c DESC LIMIT 5", (), "all")
        topl = "".join(f"  • {esc(r['t'])[:22]} — <b>{r['c']}</b>\n" for r in topc) or "  —\n"
        t = (f"📊 <b>GLOBAL STATISTICS</b>\n{LINE}\n"
             f"👥 Total clients : <b>{len(cl)}</b>  (active <b>{len(act)}</b>)\n"
             f"📡 Channels      : <b>{(await q('SELECT COUNT(*) c FROM channels', (), 'one'))['c']}</b>\n"
             f"👀 Bot users     : <b>{(await q('SELECT COUNT(*) c FROM seen_users', (), 'one'))['c']}</b>\n{LINE}\n"
             f"🚫 <b>Removals</b>\n"
             f"  Today : <b>{d['today']}</b>\n  7 days: <b>{d['d7']}</b>\n"
             f"  30 days: <b>{d['d30']}</b>\n  All time: <b>{tot}</b>\n"
             f"  ⭐ Premium: <b>{prem}</b>\n{LINE}\n"
             f"💰 <b>Revenue (active)</b>\n{pdist or '  —'}"
             f"  <b>MRR = ₹{sum(config.PLANS.get(c['plan'],{}).get('price',0) for c in act):,}</b>\n{LINE}\n"
             f"🏅 <b>Top Channels</b>\n{topl}{LINE}")
        await safe_edit(qy, t, kb([[B("🔄 Refresh", callback_data="o:stats"),
                                    B("🏠 Home", callback_data="o:home")]]))

    elif head == "chans":
        page = int(parts[1]) if len(parts) > 1 else 0
        per = 8
        rows_ = await q("SELECT * FROM channels ORDER BY removed DESC LIMIT ? OFFSET ?",
                        (per, page * per), "all")
        total = (await q("SELECT COUNT(*) c FROM channels", (), "one"))["c"]
        pages = max(1, (total + per - 1) // per)
        t = f"📡 <b>ALL CHANNELS</b>  <i>({total})</i>\n{LINE}\n"
        for ch in rows_:
            owner = await get_client(ch["owner_id"])
            on = owner["first_name"] if owner and owner["first_name"] else str(ch["owner_id"])
            t += (f"{'🟢' if ch['active'] else '⏸'} <b>{esc(ch['title'])[:24]}</b>\n"
                  f"     👤 {esc(on)[:14]} <code>{ch['owner_id']}</code>\n"
                  f"     🚫 {ch['removed']} • 🎯 {ch['action']}\n")
        if not rows_:
            t += "Koi channel nahi."
        nav = []
        if page > 0:
            nav.append(B("‹ Prev", callback_data=f"o:chans:{page-1}"))
        nav.append(B(f"{page+1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(B("Next ›", callback_data=f"o:chans:{page+1}"))
        await safe_edit(qy, t, kb([nav, [B("🏠 Home", callback_data="o:home")]]))

    elif head == "exp":
        soon = await q("SELECT * FROM clients WHERE is_blocked=0 AND expires_at>? AND expires_at<=? "
                       "ORDER BY expires_at", (now(), now() + 7 * 86400), "all")
        expired = await q("SELECT * FROM clients WHERE expires_at<=? ORDER BY expires_at DESC LIMIT 10",
                          (now(),), "all")
        t = f"⌛ <b>EXPIRING SOON</b> (7 days)\n{LINE}\n"
        rows = []
        if not soon:
            t += "Koi nahi 👍\n"
        for c in soon:
            nm = c["first_name"] or str(c["user_id"])
            t += f"🟡 <b>{esc(nm)[:18]}</b> — {days_left(c):.1f}d — {plan_name(c['plan'])}\n     <code>{c['user_id']}</code>\n"
            rows.append([B(f"⚡ {nm[:14]} (+30d)", callback_data=f"o:ext:{c['user_id']}:30"),
                         B("👤", callback_data=f"o:cli:{c['user_id']}")])
        t += f"{LINE}\n💀 <b>RECENTLY EXPIRED</b>\n"
        if not expired:
            t += "—\n"
        for c in expired:
            nm = c["first_name"] or str(c["user_id"])
            t += f"⌛ {esc(nm)[:18]} — {fdate(c['expires_at'])} — <code>{c['user_id']}</code>\n"
        rows.append([B("🏠 Home", callback_data="o:home")])
        await safe_edit(qy, t, kb(rows))

    elif head == "audit":
        rows_ = await q("SELECT * FROM audit ORDER BY id DESC LIMIT 15", (), "all")
        t = f"📝 <b>AUDIT LOG</b>\n{LINE}\n"
        for r in rows_:
            t += (f"• <b>{esc(r['action'])}</b> → <code>{esc(r['target'])}</code>\n"
                  f"   {esc(r['detail'] or '')} <i>{fdatetime(r['at'])}</i>\n")
        if not rows_:
            t += "Khali."
        await safe_edit(qy, t, kb([[B("🔄 Refresh", callback_data="o:audit"),
                                    B("🏠 Home", callback_data="o:home")]]))

    elif head == "maint":
        cur = await get_setting("maintenance", "0")
        await set_setting("maintenance", "0" if cur == "1" else "1")
        t, k = await scr_owner(ctx)
        await safe_edit(qy, t, k)

    elif head == "backup":
        await send_backup(ctx, qy.message.chat_id)
        await qy.answer("🗄 Backup sent")

    # ─────────── ✨ PREMIUM EMOJI MANAGER ───────────
    elif head == "pe":
        total = len(config.EMOJI_SLOTS)
        done = sum(1 for e in config.EMOJI_SLOTS
                   if e in EMOJI_MAP or _plain_key(e) in EMOJI_MAP)
        t = (f"✨ <b>PREMIUM EMOJI</b>\n{LINE}\n"
             f"🔘 Status  : {'🟢 <b>ON</b>' if EMOJI_ON else '⚪️ <b>OFF</b>'}\n"
             f"📶 Delivery: {'🟢 Working' if EMOJI_SUPPORTED else '🔴 Blocked by Telegram'}\n"
             f"🗂 Mapped  : <b>{done}/{total}</b>  {bar(done, total, 12)}\n{LINE}\n"
             "Bot ke saare emoji automatically <b>premium / animated</b>\n"
             "emoji me convert ho jate hain.\n\n"
             "🪄 <b>Auto-Map</b> — sabse aasan tarika:\n"
             "   Ek message me premium emoji bhejein, bot khud\n"
             "   unki IDs nikal ke save kar lega.\n\n"
             "📦 <b>Emoji Pack</b> — poora pack ek saath set karein.\n"
             f"{LINE}\n"
             "⚠️ <i>Telegram rule: custom emoji tabhi dikhti hai jab bot ne\n"
             "Fragment se username liya ho, ya aapke (owner) paas active\n"
             "Telegram Premium ho. Warna bot khud normal emoji bhejta hai.</i>")
        k = kb([
            [B("🪄 Auto-Map (recommended)", callback_data="o:peauto")],
            [B("📦 Set Whole Pack", callback_data="o:pepack"),
             B("👁 Preview", callback_data="o:peprev")],
            [B("📋 View Mapped", callback_data="o:pelist"),
             B("🎯 Map One-by-One", callback_data="o:peone:0")],
            [B(f"{'⚪️ Turn OFF' if EMOJI_ON else '🟢 Turn ON'}", callback_data="o:petg"),
             B("🔄 Retry Delivery", callback_data="o:peretry")],
            [B("🗑 Clear All", callback_data="o:peclr"), B("🏠 Home", callback_data="o:home")],
        ])
        await safe_edit(qy, t, k)

    elif head == "petg":
        EMOJI_ON = not EMOJI_ON
        await set_setting("premium_emoji", "1" if EMOJI_ON else "0")
        await cb_owner(qy, ctx, u, "pe")

    elif head == "peretry":
        EMOJI_SUPPORTED = True
        await qy.answer("🔄 Delivery reset — dobara try karega")
        await cb_owner(qy, ctx, u, "pe")

    elif head == "peauto":
        st(ctx).update(k="pe_auto")
        await safe_edit(qy,
            f"🪄 <b>AUTO-MAP</b>\n{LINE}\n"
            "Ab mujhe ek message bhejein jisme aapke <b>premium emoji</b> hon.\n\n"
            "<b>Kaise?</b>\n"
            "1️⃣ Emoji keyboard kholein → premium/animated pack chunein\n"
            "2️⃣ Jitne emoji chahiye sab ek hi message me bhej dein\n"
            "3️⃣ Send — main unki IDs nikal ke save kar dunga\n\n"
            "<b>Mapping kaise hoti hai?</b>\n"
            "Har premium emoji ka apna <i>base</i> emoji hota hai (jaise ✅ ka\n"
            "premium version bhi ✅ hi hota hai) — usi base pe map ho jayega.\n\n"
            "<i>Tip: kisi aur ka premium-emoji message forward bhi kar sakte hain.</i>",
            kb([[B("✖️ Cancel", callback_data="o:pe")]]))

    elif head == "pepack":
        st(ctx).update(k="pe_pack")
        miss = [e for e in config.EMOJI_SLOTS
                if e not in EMOJI_MAP and _plain_key(e) not in EMOJI_MAP]
        await safe_edit(qy,
            f"📦 <b>SET WHOLE PACK</b>\n{LINE}\n"
            "Manual mapping — har line me:\n"
            "<code>emoji ID</code>\n\n"
            "<b>Example:</b>\n"
            "<code>✅ 5237699328843200968\n"
            "❌ 5210952531676504517\n"
            "🛡 5253742260465035583</code>\n\n"
            f"⏳ Abhi <b>{len(miss)}</b> emoji unmapped hain.\n"
            f"{LINE}\n"
            "<i>ID nikalne ka tarika: premium emoji bhejein aur\n"
            "🪄 Auto-Map use karein — wo khud ID nikal deta hai.</i>",
            kb([[B("🪄 Auto-Map instead", callback_data="o:peauto")],
                [B("✖️ Cancel", callback_data="o:pe")]]))

    elif head == "pelist":
        if not EMOJI_MAP:
            await qy.answer("Abhi koi emoji mapped nahi hai", show_alert=True)
            return
        lines, seen_ids = [], set()
        for e in config.EMOJI_SLOTS:
            eid = EMOJI_MAP.get(e) or EMOJI_MAP.get(_plain_key(e))
            if eid and (e, eid) not in seen_ids:
                seen_ids.add((e, eid))
                lines.append(f"{e} → <code>{eid}</code>")
        body = "\n".join(lines[:45]) or "—"
        extra = f"\n<i>+{len(lines)-45} more…</i>" if len(lines) > 45 else ""
        await safe_edit(qy, f"📋 <b>MAPPED EMOJI</b> ({len(lines)})\n{LINE}\n{body}{extra}\n{LINE}",
                        kb([[B("🗑 Clear All", callback_data="o:peclr")],
                            [B("‹ Back", callback_data="o:pe")]]))

    elif head == "peprev":
        t = (f"👁 <b>LIVE PREVIEW</b>\n{LINE}\n"
             "✅ Success   ❌ Error   ⚠️ Warning\n"
             "🛡 Protect   🔨 Ban     👢 Kick\n"
             "⭐ Premium   📡 Channel 💳 Plan\n"
             "📊 Stats     👤 Client  👑 Owner\n"
             "🔔 Reminder  🎉 Success 🚫 Removed\n"
             f"{LINE}\n"
             f"{'✨ Upar ke emoji <b>premium</b> dikhne chahiye.' if EMOJI_ON and EMOJI_MAP else '⚪️ Premium emoji OFF hai ya mapped nahi.'}\n\n"
             "<i>Agar ye normal dikh rahe hain to matlab Telegram ne\n"
             "custom emoji allow nahi ki — bot ne safely fallback kar diya.\n"
             "Fix: bot owner account pe Telegram Premium hona chahiye.</i>")
        await safe_edit(qy, t, kb([[B("🔄 Refresh", callback_data="o:peprev")],
                                   [B("‹ Back", callback_data="o:pe")]]))

    elif head == "peone":
        idx = int(parts[1]) if len(parts) > 1 else 0
        slots = config.EMOJI_SLOTS
        idx = max(0, min(idx, len(slots) - 1))
        e = slots[idx]
        cur = EMOJI_MAP.get(e) or EMOJI_MAP.get(_plain_key(e))
        st(ctx).update(k="pe_one", idx=idx)
        nav = []
        if idx > 0:
            nav.append(B("‹ Prev", callback_data=f"o:peone:{idx-1}"))
        nav.append(B(f"{idx+1}/{len(slots)}", callback_data="noop"))
        if idx < len(slots) - 1:
            nav.append(B("Next ›", callback_data=f"o:peone:{idx+1}"))
        rows = [nav]
        if cur:
            rows.append([B("🗑 Unmap this", callback_data=f"o:peunmap:{idx}")])
        rows.append([B("‹ Back", callback_data="o:pe")])
        await safe_edit(qy,
            f"🎯 <b>MAP ONE-BY-ONE</b>\n{LINE}\n"
            f"Slot <b>{idx+1}/{len(slots)}</b>\n"
            f"Base emoji : <b>{e}</b>\n"
            f"Current ID : {f'<code>{cur}</code>' if cur else '❌ not set'}\n{LINE}\n"
            "Iske liye <b>premium emoji</b> bhejein (ya sirf uski ID).\n"
            "Save hote hi apne aap next slot khul jayega.", kb(rows))

    elif head == "peunmap":
        idx = int(parts[1])
        e = config.EMOJI_SLOTS[idx]
        await q("DELETE FROM emoji_map WHERE emoji IN (?,?)", (e, _plain_key(e)))
        EMOJI_MAP.pop(e, None)
        EMOJI_MAP.pop(_plain_key(e), None)
        await cb_owner(qy, ctx, u, f"peone:{idx}")

    elif head == "peclr":
        await q("DELETE FROM emoji_map")
        EMOJI_MAP.clear()
        EMOJI_MAP.update(config.EMOJI_IDS)
        await qy.answer("🗑 Saare mappings clear ho gaye")
        await cb_owner(qy, ctx, u, "pe")


# ═══════════════════════════════════════════════════════════════
#  SECTION 8 ── TEXT INPUT (state machine)
# ═══════════════════════════════════════════════════════════════
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    msg = update.message
    if not msg:
        return
    u = update.effective_user
    text = (msg.text or msg.caption or "").strip()

    # forwarded message se ID nikalna
    if msg.forward_origin and not st(ctx):
        try:
            oc = msg.forward_origin.chat  # channel se forward
            await msg.reply_text(f"📡 <b>Chat ID:</b> <code>{oc.id}</code>\n"
                                 f"📛 {esc(oc.title)}", parse_mode=HTML)
            return
        except AttributeError:
            try:
                su = msg.forward_origin.sender_user
                await msg.reply_text(f"👤 <b>User ID:</b> <code>{su.id}</code>\n"
                                     f"📛 {esc(su.full_name)}", parse_mode=HTML)
                return
            except AttributeError:
                pass

    state = st(ctx)
    k = state.get("k")
    if not k:
        return

    # ───── OWNER STATES ─────
    if k == "add_id" and is_admin(u.id):
        tgt = None
        if msg.forward_origin and getattr(msg.forward_origin, "sender_user", None):
            tgt = msg.forward_origin.sender_user.id
        elif text.lstrip("-").isdigit():
            tgt = int(text)
        if not tgt:
            await msg.reply_text("❌ Valid numeric ID bhejein.", parse_mode=HTML)
            return
        state.update(k="add_plan", uid=tgt)
        rows, cur = [], []
        for key, p in config.PLANS.items():
            cur.append(B(f"{p['name']} ₹{p['price']:,}", callback_data=f"o:addfor:{tgt}:{key}"))
            if len(cur) == 2:
                rows.append(cur); cur = []
        if cur:
            rows.append(cur)
        rows.append([B("✖️ Cancel", callback_data="o:home")])
        ex = await get_client(tgt)
        warn = (f"\n⚠️ Ye client pehle se hai — days <b>add</b> honge.\n"
                f"   Current expiry: {fdate(ex['expires_at'])}\n") if ex else ""
        await msg.reply_text(f"➕ <b>ADD CLIENT — Step 2/3</b>\n{LINE}\n"
                             f"🆔 <code>{tgt}</code>{warn}\n"
                             "Ab <b>plan</b> choose karein 👇",
                             reply_markup=kb(rows), parse_mode=HTML)
        return

    if k == "add_days" and is_admin(u.id):
        if not text.isdigit() or int(text) <= 0:
            await msg.reply_text("❌ Days ke liye number bhejein (e.g. 30)")
            return
        days, tgt, plan = int(text), state["uid"], state["plan"]
        clear_st(ctx)
        c = await add_client(tgt, days, plan)
        await audit(u.id, "add_client", str(tgt), f"{days}d {plan}")
        await msg.reply_text(f"✅ <b>SUBSCRIPTION CREATED</b>\n{LINE}\n" + await client_card(c),
                             reply_markup=client_kb(tgt), parse_mode=HTML)
        await notify_new_sub(ctx, c, days)
        return

    if k == "ext_days" and is_admin(u.id):
        if not text.lstrip("-").isdigit():
            await msg.reply_text("❌ Number bhejein.")
            return
        days, tgt = int(text), state["uid"]
        clear_st(ctx)
        c = await get_client(tgt)
        if not c:
            await msg.reply_text("❌ Client not found.")
            return
        await upd_client(tgt, expires_at=max(now(), c["expires_at"]) + days * 86400,
                         last_reminder=0, expired_notified=0)
        c = await get_client(tgt)
        await audit(u.id, "extend", str(tgt), f"{days}d")
        await msg.reply_text(f"✅ <b>{days:+d} days</b>\n{LINE}\n" + await client_card(c),
                             reply_markup=client_kb(tgt), parse_mode=HTML)
        await dm(ctx, tgt, f"🔔 <b>Subscription Updated</b>\n{LINE}\n"
                           f"📅 New expiry: <b>{fdate(c['expires_at'])}</b>\n"
                           f"⏳ Remaining : <b>{days_left(c):.1f} days</b>")
        return

    if k == "msg_client" and is_admin(u.id):
        tgt = state["uid"]
        clear_st(ctx)
        ok = await dm(ctx, tgt, f"✉️ <b>Message from Owner</b>\n{LINE}\n{text}\n{LINE}\n"
                                f"💬 Reply: @{config.OWNER_USERNAME}")
        await msg.reply_text("✅ Message sent." if ok else "❌ Deliver nahi hua (user ne bot block kiya?)",
                             reply_markup=client_kb(tgt))
        return

    if k == "search" and is_admin(u.id):
        clear_st(ctx)
        term = text.lstrip("@")
        if term.isdigit():
            res = [x for x in [await get_client(int(term))] if x]
        else:
            res = await q("SELECT * FROM clients WHERE username LIKE ? OR first_name LIKE ? LIMIT 10",
                          (f"%{term}%", f"%{term}%"), "all")
        if not res:
            await msg.reply_text("❌ Koi client nahi mila.",
                                 reply_markup=kb([[B("🏠 Home", callback_data="o:home")]]))
            return
        if len(res) == 1:
            await msg.reply_text(await client_card(res[0]), reply_markup=client_kb(res[0]["user_id"]),
                                 parse_mode=HTML)
            return
        rows = [[B(f"{c['first_name'] or c['user_id']}", callback_data=f"o:cli:{c['user_id']}")]
                for c in res]
        rows.append([B("🏠 Home", callback_data="o:home")])
        await msg.reply_text(f"🔍 <b>{len(res)} results</b>", reply_markup=kb(rows), parse_mode=HTML)
        return

    if k == "broadcast" and is_admin(u.id):
        clear_st(ctx)
        await do_broadcast(ctx, msg, text)
        return

    # ───── ✨ PREMIUM EMOJI STATES ─────
    if k == "pe_auto" and is_admin(u.id):
        clear_st(ctx)
        ents = (msg.entities or []) + (msg.caption_entities or [])
        raw = msg.text or msg.caption or ""
        pairs = []
        for en in ents:
            if en.type == "custom_emoji" and en.custom_emoji_id:
                base = raw[en.offset:en.offset + en.length]
                if base.strip():
                    pairs.append((base, en.custom_emoji_id))
        if not pairs:
            await msg.reply_text(
                f"❌ <b>Koi premium emoji nahi mila</b>\n{LINE}\n"
                "Message me <b>animated / premium</b> emoji hone chahiye —\n"
                "normal emoji ki koi ID nahi hoti.\n\n"
                "Emoji keyboard me premium pack (👑 wala) se emoji chunein.",
                parse_mode=HTML,
                reply_markup=kb([[B("🪄 Try Again", callback_data="o:peauto")],
                                 [B("‹ Back", callback_data="o:pe")]]))
            return
        n = 0
        for base, eid in pairs:
            await save_emoji(base, eid)
            await save_emoji(_plain_key(base), eid)
            n += 1
        total = len(config.EMOJI_SLOTS)
        done = sum(1 for e in config.EMOJI_SLOTS
                   if e in EMOJI_MAP or _plain_key(e) in EMOJI_MAP)
        shown = "  ".join(f"{b}" for b, _ in pairs[:12])
        await msg.reply_text(
            f"✅ <b>{n} PREMIUM EMOJI SAVED!</b>\n{LINE}\n"
            f"{shown}\n{LINE}\n"
            f"🗂 Coverage: <b>{done}/{total}</b>  {bar(done, total, 12)}\n\n"
            "Ye emoji ab bot ke har message me automatically use honge ✨",
            parse_mode=HTML,
            reply_markup=kb([[B("🪄 Add More", callback_data="o:peauto"),
                              B("👁 Preview", callback_data="o:peprev")],
                             [B("✨ Emoji Panel", callback_data="o:pe")]]))
        await audit(u.id, "emoji_map", "auto", f"{n} saved")
        return

    if k == "pe_pack" and is_admin(u.id):
        clear_st(ctx)
        ok = bad = 0
        for line in text.splitlines():
            bits = line.strip().split()
            if len(bits) >= 2 and bits[-1].isdigit():
                await save_emoji(bits[0], bits[-1])
                await save_emoji(_plain_key(bits[0]), bits[-1])
                ok += 1
            elif line.strip():
                bad += 1
        total = len(config.EMOJI_SLOTS)
        done = sum(1 for e in config.EMOJI_SLOTS
                   if e in EMOJI_MAP or _plain_key(e) in EMOJI_MAP)
        await msg.reply_text(
            f"{'✅' if ok else '❌'} <b>Pack Import</b>\n{LINE}\n"
            f"✅ Saved  : <b>{ok}</b>\n"
            f"⚠️ Skipped: <b>{bad}</b>\n"
            f"🗂 Coverage: <b>{done}/{total}</b>  {bar(done, total, 12)}",
            parse_mode=HTML,
            reply_markup=kb([[B("👁 Preview", callback_data="o:peprev")],
                             [B("✨ Emoji Panel", callback_data="o:pe")]]))
        return

    if k == "pe_one" and is_admin(u.id):
        idx = state.get("idx", 0)
        slots = config.EMOJI_SLOTS
        base = slots[idx]
        eid = None
        for en in (msg.entities or []):
            if en.type == "custom_emoji" and en.custom_emoji_id:
                eid = en.custom_emoji_id
                break
        if not eid and text.isdigit():
            eid = text
        if not eid:
            await msg.reply_text("❌ Premium emoji ya numeric ID bhejein.")
            return
        await save_emoji(base, eid)
        await save_emoji(_plain_key(base), eid)
        nxt = min(idx + 1, len(slots) - 1)
        state.update(k="pe_one", idx=nxt)
        await msg.reply_text(
            f"✅ <b>{base}</b> mapped → <code>{eid}</code>\n{LINE}\n"
            f"➡️ Next slot <b>{nxt+1}/{len(slots)}</b>: <b>{slots[nxt]}</b>\n"
            "Iske liye premium emoji bhejein (ya Back dabayein).",
            parse_mode=HTML,
            reply_markup=kb([[B("‹ Prev", callback_data=f"o:peone:{max(0,idx)}"),
                              B("Skip ›", callback_data=f"o:peone:{min(nxt+1,len(slots)-1)}")],
                             [B("✨ Emoji Panel", callback_data="o:pe")]]))
        return

    # ───── CLIENT STATES ─────
    c = await get_client(u.id)
    if not is_active(c):
        clear_st(ctx)
        return

    if k == "wl_add":
        clear_st(ctx)
        added = 0
        for line in text.splitlines():
            bits = line.strip().split(maxsplit=1)
            if bits and bits[0].isdigit():
                await q("INSERT OR REPLACE INTO whitelist(owner_id,user_id,label,at) VALUES(?,?,?,?)",
                        (u.id, int(bits[0]), bits[1] if len(bits) > 1 else "", now()))
                added += 1
        await msg.reply_text(f"✅ <b>{added}</b> user whitelist me add ho gaye."
                             if added else "❌ Koi valid ID nahi mili.",
                             reply_markup=kb([[B("✅ Whitelist", callback_data="c:wl:0")],
                                              [B("🏠 Home", callback_data="c:home")]]),
                             parse_mode=HTML)
        return

    if k == "log_set":
        clear_st(ctx)
        cid = None
        if msg.forward_origin and getattr(msg.forward_origin, "chat", None):
            cid = msg.forward_origin.chat.id
        elif text.lstrip("-").isdigit():
            cid = int(text)
        if not cid:
            await msg.reply_text("❌ Valid channel ID bhejein (e.g. -1001234567890)")
            return
        try:
            await ctx.bot.send_message(cid, f"✅ <b>{config.BRAND} Log Channel Connected</b>\n{LINE}\n"
                                            "Ab saare protection logs yahan aayenge.", parse_mode=HTML)
        except Exception as e:
            await msg.reply_text(f"❌ Yahan post nahi kar paya.\n<code>{esc(e)}</code>\n\n"
                                 "Bot ko us channel me admin banayein.", parse_mode=HTML)
            return
        await upd_client(u.id, log_channel=cid)
        await msg.reply_text(f"✅ Log channel set: <code>{cid}</code>", parse_mode=HTML,
                             reply_markup=kb([[B("⚙️ Settings", callback_data="c:set")]]))
        return

    if k == "word_add":
        clear_st(ctx)
        n = 0
        for w in text.replace("\n", ",").split(","):
            w = w.strip().lower()
            if w:
                await q("INSERT OR IGNORE INTO badwords(owner_id,word) VALUES(?,?)", (u.id, w))
                n += 1
        await msg.reply_text(f"✅ <b>{n}</b> words added.", parse_mode=HTML,
                             reply_markup=kb([[B("🔤 Blacklist", callback_data="c:words")]]))
        return


# ═══════════════════════════════════════════════════════════════
#  SECTION 9 ── BACKGROUND JOBS
# ═══════════════════════════════════════════════════════════════
async def job_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    """Expiry se 3 din pehle, har 6 ghante (= din me 4 baar) reminder."""
    gap = config.REMINDER_INTERVAL_HRS * 3600
    soon = await q("SELECT * FROM clients WHERE is_blocked=0 AND expires_at>? AND expires_at<=?",
                   (now(), now() + config.REMINDER_DAYS_BEFORE * 86400), "all")
    for c in soon:
        if now() - (c["last_reminder"] or 0) < gap:
            continue
        dl = days_left(c)
        hrs = int(dl * 24)
        urgency = "🔴 <b>URGENT</b>" if dl < 1 else "🟠 <b>Reminder</b>" if dl < 2 else "🟡 <b>Reminder</b>"
        await dm(ctx, c["user_id"],
                 f"{urgency} — Subscription Expiring!\n{LINE}\n"
                 f"💳 Plan   : <b>{plan_name(c['plan'])}</b>\n"
                 f"📅 Expiry : <b>{fdatetime(c['expires_at'])}</b>\n"
                 f"⏳ Bacha  : <b>{dl:.1f} din ({hrs} hours)</b>\n{LINE}\n"
                 "⚠️ Expire hote hi aapke saare channels ki protection\n"
                 "<b>band</b> ho jayegi aur premium users join kar payenge.\n\n"
                 "Abhi renew karein 👇",
                 kb([[B("🔁 Renew Now", callback_data="c:upgrade")],
                     [B("💬 Contact Owner", url=config.SUPPORT_LINK)]]))
        await upd_client(c["user_id"], last_reminder=now())
    if soon:
        log.info("reminders sent: %d candidates", len(soon))


async def job_expiry(ctx: ContextTypes.DEFAULT_TYPE):
    """Expire hone par client + owner ko inform karein."""
    rows = await q("SELECT * FROM clients WHERE expires_at<=? AND expired_notified=0", (now(),), "all")
    for c in rows:
        chs = await channels_of(c["user_id"])
        await dm(ctx, c["user_id"],
                 f"⌛ <b>SUBSCRIPTION EXPIRED</b>\n{LINE}\n"
                 f"💳 Plan    : <b>{plan_name(c['plan'])}</b>\n"
                 f"📅 Expired : <b>{fdate(c['expires_at'])}</b>\n"
                 f"📡 Channels: <b>{len(chs)}</b> (ab unprotected 🔴)\n"
                 f"🚫 Total removed (lifetime): <b>{c['total_removed']}</b>\n{LINE}\n"
                 "🔴 <b>Protection band ho gayi hai.</b>\n"
                 "Aapke channels me ab premium users join kar sakte hain.\n\n"
                 "Renew karte hi sab wapas chalu ho jayega ✅",
                 kb([[B("🔁 Renew Now", callback_data="c:upgrade")],
                     [B("💬 Contact Owner", url=config.SUPPORT_LINK)]]))
        await upd_client(c["user_id"], expired_notified=1)
        await dm(ctx, config.OWNER_ID,
                 f"⌛ <b>Client Expired</b>\n"
                 f"👤 {esc(c['first_name'] or '')} <code>{c['user_id']}</code>\n"
                 f"💳 {plan_name(c['plan'])} • 📡 {len(chs)} channels",
                 kb([[B("⚡ Renew +30d", callback_data=f"o:ext:{c['user_id']}:30"),
                      B("👤 Profile", callback_data=f"o:cli:{c['user_id']}")]]))


async def job_backup(ctx: ContextTypes.DEFAULT_TYPE):
    await send_backup(ctx, config.OWNER_ID)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Exception: %s", ctx.error, exc_info=ctx.error)


# ═══════════════════════════════════════════════════════════════
#  SECTION 10 ── BOOTSTRAP
# ═══════════════════════════════════════════════════════════════
async def post_init(app: Application):
    await init_db()
    await load_emoji_map()
    me = await app.bot.get_me()
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Open panel"),
        BotCommand("panel", "🛡 Control panel"),
        BotCommand("id", "🆔 Get chat ID"),
        BotCommand("help", "❓ Help"),
    ], scope=BotCommandScopeDefault())
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "👑 Owner panel"),
            BotCommand("add", "➕ Add client"),
            BotCommand("extend", "📅 Extend"),
            BotCommand("remove", "🗑 Delete client"),
            BotCommand("info", "👤 Client info"),
            BotCommand("clients", "👥 All clients"),
            BotCommand("bc", "📣 Broadcast"),
            BotCommand("backup", "🗄 Backup DB"),
            BotCommand("emoji", "✨ Premium emoji"),
            BotCommand("id", "🆔 Chat ID"),
        ], scope=BotCommandScopeChat(config.OWNER_ID))
    except Exception:
        pass
    log.info("✅ %s online as @%s | owner=%s", config.BRAND, me.username, config.OWNER_ID)
    try:
        await app.bot.send_message(
            config.OWNER_ID,
            f"🟢 <b>{config.BRAND} STARTED</b>\n{LINE}\n"
            f"🤖 @{me.username}\n🕒 {fdatetime(now())}\n"
            f"👑 Owner: <code>{config.OWNER_ID}</code>\n"
            f"✨ Premium emoji: {'🟢 ON' if EMOJI_ON else '⚪️ OFF'} "
            f"({len(EMOJI_MAP)} mapped)\n{LINE}\n"
            "Panel kholne ke liye /start",
            parse_mode=HTML)
    except Exception:
        pass


def main():
    if not config.BOT_TOKEN or "PASTE" in config.BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN set nahi hai! config.py ya .env me daalein.")

    # PremiumBot = custom emoji auto-converter (safe fallback ke sath)
    premium_bot = PremiumBot(
        token=config.BOT_TOKEN,
        defaults=Defaults(parse_mode=HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)),
        request=HTTPXRequest(connection_pool_size=256, read_timeout=30, connect_timeout=30),
        get_updates_request=HTTPXRequest(connection_pool_size=16, read_timeout=60, connect_timeout=30),
    )
    app = (ApplicationBuilder()
           .bot(premium_bot)
           .concurrent_updates(True)
           .post_init(post_init)
           .build())

    # commands
    app.add_handler(CommandHandler(["start", "panel"], cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("extend", cmd_extend))
    app.add_handler(CommandHandler(["remove", "del"], cmd_remove))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("clients", cmd_clients))
    app.add_handler(CommandHandler(["bc", "broadcast"], cmd_bc))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("emoji", cmd_emoji))

    # ui
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_text))

    # protection
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatJoinRequestHandler(on_join_request))

    app.add_error_handler(on_error)

    # jobs
    jq = app.job_queue
    m = config.JOB_INTERVAL_MINUTES * 60
    jq.run_repeating(job_reminders, interval=m, first=30)
    jq.run_repeating(job_expiry, interval=m, first=60)
    jq.run_repeating(job_backup, interval=86400, first=3600)

    log.info("🚀 Starting %s …", config.BRAND)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
