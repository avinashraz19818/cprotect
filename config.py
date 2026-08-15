"""
cProtect  ─  Configuration
==========================
Sab kuch yahi se control hota hai.
Values .env file se aati hain (agar .env nahi hai to yahan default use hoga).
"""

import os
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:      # dotenv optional
    pass


def _int(key: str, default: int) -> int:
    try:
        return int(str(os.getenv(key, default)).strip())
    except (TypeError, ValueError):
        return default


# ══════════════════════════ CORE ══════════════════════════
# BotFather se mila token yahan ya .env me daalein
BOT_TOKEN = os.getenv("BOT_TOKEN", "8938584003:AAFcAGk_TSDiN1aq-_3jfGP1knk3tkJvUWw").strip()

# Main owner (aap)
OWNER_ID = _int("OWNER_ID", 8015937475)
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "zayro_o").lstrip("@")

# Extra co-admins  ->  EXTRA_ADMINS=123,456
EXTRA_ADMINS = {int(x) for x in os.getenv("EXTRA_ADMINS", "").replace(" ", "").split(",") if x.isdigit()}
ADMIN_IDS = {OWNER_ID} | EXTRA_ADMINS

BRAND = os.getenv("BRAND", "cProtect")
SUPPORT_LINK = f"https://t.me/{OWNER_USERNAME}"

DB_PATH = os.getenv("DB_PATH", "cprotect.db")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))


# ══════════════════════ SUBSCRIPTION PLANS ══════════════════════
# key : { slots = kitne channel connect kar sakta hai , price = ₹/month }
PLANS = {
    "p1":  {"slots": 1,  "price": 999,  "name": "1 Channel"},
    "p2":  {"slots": 2,  "price": 1499, "name": "2 Channel"},
    "p4":  {"slots": 4,  "price": 2499, "name": "4 Channel"},
    "p5":  {"slots": 5,  "price": 2999, "name": "5 Channel"},
    "p8":  {"slots": 8,  "price": 3999, "name": "8 Channel"},
    "p10": {"slots": 10, "price": 4499, "name": "10 Channel"},
    "p15": {"slots": 15, "price": 5999, "name": "15 Channel"},
}
DEFAULT_PLAN = "p1"


# ══════════════════════════ REMINDERS ══════════════════════════
REMINDER_DAYS_BEFORE   = _int("REMINDER_DAYS_BEFORE", 3)    # kitne din pehle se reminder
REMINDER_INTERVAL_HRS  = _int("REMINDER_INTERVAL_HRS", 6)   # 24/6 = 4 reminder per day
JOB_INTERVAL_MINUTES   = _int("JOB_INTERVAL_MINUTES", 20)   # background checker


# ══════════════════════════ PROTECTION ══════════════════════════
# Premium user milne par default action
DEFAULT_ACTION = os.getenv("DEFAULT_ACTION", "ban")

ACTIONS = {
    "ban":     "🔨 Ban (permanent)",
    "kick":    "👢 Kick (rejoin allowed)",
    "decline": "🚫 Decline only",
    "report":  "📝 Report only",
}

# "New account" filter ke liye user-id threshold
# (Telegram id jitni badi, account utna naya)
NEW_ACCOUNT_ID = _int("NEW_ACCOUNT_ID", 7_600_000_000)

# UI
PAGE_SIZE = 6


# ══════════════════════ ✨ PREMIUM (CUSTOM) EMOJI ══════════════════════
# Bot ke saare emoji ko Telegram Premium / animated emoji me badal deta hai.
#
# ⚠️ Telegram ka rule:
#    Custom emoji tabhi bhejegi jab —
#      • bot ne Fragment se username khareeda ho, YA
#      • bot ke owner ke paas active Telegram Premium ho
#        (tab sirf private / group / supergroup chats me kaam karega,
#         channels me nahi — wahan bot khud plain emoji bhej dega)
#
# Agar allow nahi hua to bot automatically plain emoji par fallback kar
# dega — koi message fail nahi hoga.
PREMIUM_EMOJI = True          # master switch (bot ke andar se bhi ON/OFF ho sakta hai)

# Yahan kuch daalne ki zarurat NAHI hai.
# Bot me:  /start → ✨ Premium Emoji → 🪄 Auto-Map
# apne premium emoji bhej dein, IDs khud save ho jayengi (database me).
# Chahein to yahan permanent default bhi de sakte hain:  "✅": "5237699328843200968"
EMOJI_IDS: dict[str, str] = {}

# Bot me use hone waale saare emoji (mapping panel isi list se banta hai)
EMOJI_SLOTS = [
    # status
    "✅", "❌", "⚠️", "❗", "⛔", "🚫", "🟢", "🟡", "🟠", "🔴", "⚪️", "⏸", "▶️",
    # panel / nav
    "🏠", "🔄", "‹", "›", "➕", "🗑", "✖️", "🔍", "📌", "👇", "👉", "🆔",
    # people
    "👤", "👥", "👑", "👋", "🕵️", "🤖", "👀",
    # protection
    "🛡", "🔨", "👢", "🎯", "🔤", "🖼", "🆕", "⭐", "🔐", "✨",
    # channel / data
    "📡", "📢", "🗂", "🗄", "📥", "📊", "📈", "📝", "📖", "📣", "✉️", "💬",
    # subscription
    "💳", "💰", "📅", "🗓", "📆", "⌛", "⏳", "🕒", "🔁", "⬆️", "🔔", "🎉",
    # misc
    "⚙️", "🔧", "⚡", "🚀", "🏆", "🏅", "🥇", "🥈", "🥉", "🏷", "📦",
    "🔗", "📛", "📶", "💀", "🤷", "👍", "🤝",
]
