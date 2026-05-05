import os
from datetime import datetime, timedelta, timezone
from threading import Thread

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, db
from flask import Flask

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
KANAL_ID = int(os.getenv("KANAL_ID", "0"))
ROL_ID = int(os.getenv("ROL_ID", "0"))
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")

FIREBASE_BOSSES_PATH = "bossTracker/bosses"
CHECK_SECONDS = 30

sent_five_min_alerts = set()
last_seen_kills = {}
first_scan_done = False

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot aktif"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def init_firebase():
    key_path = "/etc/secrets/serviceAccountKey.json"

    if not os.path.exists(key_path):
        key_path = "serviceAccountKey.json"

    if not os.path.exists(key_path):
        raise RuntimeError("serviceAccountKey.json bulunamadı.")

    if not FIREBASE_DATABASE_URL:
        raise RuntimeError("FIREBASE_DATABASE_URL eksik.")

    if not firebase_admin._apps:
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred, {
            "databaseURL": FIREBASE_DATABASE_URL
        })

def parse_time(value):
    if not value:
        return None

    try:
        value = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def format_dt(dt):
    tr_dt = dt.astimezone(timezone(timedelta(hours=3)))
    return tr_dt.strftime("%d.%m.%Y %H:%M")

def normalize_last_killed(last_killed):
    if isinstance(last_killed, list):
        return {
            str(i): v
            for i, v in enumerate(last_killed)
            if v
        }

    if isinstance(last_killed, dict):
        return last_killed

    return {}

def get_bosses():
    try:
        data = db.reference(FIREBASE_BOSSES_PATH).get()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print("Firebase okuma hatası:", e)
        return []

async def send_discord_message(text):
    channel = bot.get_channel(KANAL_ID)

    if not channel:
        print("Discord kanalı bulunamadı.")
        return

    await channel.send(text)

async def notify_killed(name, ch, next_spawn):
    await send_discord_message(
        f"☠️ **{name}** Kanal **{ch}** kesildi <@&{ROL_ID}>\n"
        f"Yeni spawn: **{format_dt(next_spawn)}**"
    )

async def notify_five_minutes(name, ch, next_spawn):
    await send_discord_message(
        f"⚠️ **{name}** Kanal **{ch}** doğmasına **5 dakika kaldı!** <@&{ROL_ID}>\n"
        f"Spawn: **{format_dt(next_spawn)}**"
    )

@tasks.loop(seconds=CHECK_SECONDS)
async def boss_control_loop():
    global first_scan_done

    bosses = get_bosses()
    now = datetime.now(timezone.utc)

    print(f"Kontrol edildi. Boss sayısı: {len(bosses)}")

    for boss in bosses:
        if not isinstance(boss, dict):
            continue

        boss_id = boss.get("id")
        boss_name = boss.get("name", "Boss")
        interval = int(boss.get("intervalMinutes", 0) or 0)
        last_killed = normalize_last_killed(boss.get("lastKilledAt") or {})

        if not boss_id or interval <= 0:
            continue

        for ch, val in last_killed.items():
            killed_at = parse_time(val)

            if not killed_at:
                continue

            next_spawn = killed_at + timedelta(minutes=interval)
            key = f"{boss_id}:{ch}"
            current = killed_at.isoformat()
            previous = last_seen_kills.get(key)

            if previous is None:
                last_seen_kills[key] = current

                if first_scan_done:
                    await notify_killed(boss_name, ch, next_spawn)

            elif previous != current:
                last_seen_kills[key] = current
                await notify_killed(boss_name, ch, next_spawn)

            remaining = (next_spawn - now).total_seconds()
            alert_key = f"{boss_id}:{ch}:{next_spawn.isoformat()}:5min"

            if 0 < remaining <= 5 * 60 and alert_key not in sent_five_min_alerts:
                sent_five_min_alerts.add(alert_key)

                if first_scan_done:
                    await notify_five_minutes(boss_name, ch, next_spawn)

    first_scan_done = True

@bot.command()
async def test(ctx):
    await ctx.send("Bot aktif")

@bot.command()
async def bossdebug(ctx):
    bosses = get_bosses()
    await ctx.send(f"Boss sayısı: {len(bosses)}")

@bot.event
async def on_ready():
    print(f"{bot.user} aktif")

    if not boss_control_loop.is_running():
        boss_control_loop.start()

if __name__ == "__main__":
    init_firebase()
    Thread(target=run_web, daemon=True).start()
    bot.run(DISCORD_TOKEN)
