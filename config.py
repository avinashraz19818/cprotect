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

# Yahan default premium emoji IDs pre-loaded hain.
# Bot automatically inhe use karega. Agar aap apne custom emoji use karna
# chahte hain to bot me /start → ✨ Premium Emoji → 🪄 Auto-Map se add karein.
# Format: "base_emoji": "custom_emoji_id"
EMOJI_IDS: dict[str, str] = {
    # ── Status ──
    "✅":  "5368324148174726370",
    "❌":  "5465665605978032963",
    "⚠️":  "5471952986970523945",
    "❗":  "5260547337988802518",
    "⛔":  "5471952986970523946",
    "🚫":  "5471952986970523947",
    "🟢":  "5411502215531023578",
    "🟡":  "5411502215531023579",
    "🟠":  "5411502215531023580",
    "🔴":  "5411502215531023581",
    "⚪️":  "5411502215531023582",
    "⏸":  "5379748061717530028",
    "▶️":  "5298529923889665598",

    # ── Panel / Navigation ──
    "🏠":  "5413615020665544141",
    "🔄":  "5307725453654230139",
    "‹":   "5294085801192595462",
    "›":   "5294085801192595463",
    "➕":  "5280310033370513104",
    "🗑":  "5379748061717530030",
    "✖️":  "5465665605978032964",
    "🔍":  "5467449968982231982",
    "📌":  "5447201532486811649",
    "👇":  "5471952986970523950",
    "👉":  "5471952986970523951",
    "🆔":  "5447417861978882985",

    # ── People ──
    "👤":  "5442934365288073597",
    "👥":  "5442934365288073598",
    "👑":  "5470052537468453780",
    "👋":  "5471952986970523948",
    "🕵️":  "5467583825049984354",
    "🤖":  "5471932458371657778",
    "👀":  "5469941068270534489",

    # ── Protection ──
    "🛡":  "5467583825049984353",
    "🔨":  "5431449002169407117",
    "👢":  "5431449002169407118",
    "🎯":  "5467449968982231983",
    "🔤":  "5431859071459919248",
    "🖼":  "5467638985999392771",
    "🆕":  "5447417861978882986",
    "⭐":  "5368324148174726371",
    "🔐":  "5467583825049984355",
    "✨":  "5366318460679960979",

    # ── Channel / Data ──
    "📡":  "5303285100887005721",
    "📢":  "5303285100887005722",
    "🗂":  "5467449968982231984",
    "🗄":  "5467449968982231985",
    "📥":  "5467449968982231986",
    "📊":  "5431859071459919249",
    "📈":  "5431859071459919250",
    "📝":  "5431859071459919251",
    "📖":  "5467449968982231987",
    "📣":  "5303285100887005723",
    "✉️":  "5301149063974105077",
    "💬":  "5301149063974105078",

    # ── Subscription ──
    "💳":  "5447201532486811650",
    "💰":  "5447201532486811651",
    "📅":  "5467449968982231988",
    "🗓":  "5467449968982231989",
    "📆":  "5467449968982231990",
    "⌛":  "5415863788499247036",
    "⏳":  "5415863788499247037",
    "🕒":  "5467449968982231991",
    "🔁":  "5307725453654230140",
    "⬆️":  "5280310033370513105",
    "🔔":  "5307725453654230141",
    "🎉":  "5366318460679960980",

    # ── Misc ──
    "⚙️":  "5467449968982231992",
    "🔧":  "5467449968982231993",
    "⚡":  "5366318460679960981",
    "🚀":  "5415863788499247038",
    "🏆":  "5379748061717530029",
    "🏅":  "5379748061717530031",
    "🥇":  "5379748061717530032",
    "🥈":  "5379748061717530033",
    "🥉":  "5379748061717530034",
    "🏷":  "5467449968982231994",
    "📦":  "5467449968982231995",
    "🔗":  "5467449968982231996",
    "📛":  "5467449968982231997",
    "📶":  "5467449968982231998",
    "💀":  "5467583825049984356",
    "🤷":  "5471952986970523949",
    "👍":  "5368324148174726372",
    "🤝":  "5469941068270534490",
}

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
