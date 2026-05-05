import os
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, db


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


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def init_firebase():
    if not FIREBASE_DATABASE_URL:
        raise RuntimeError("FIREBASE_DATABASE_URL .env içinde yok.")

    if not os.path.exists("serviceAccountKey.json"):
        raise RuntimeError(
            "serviceAccountKey.json bulunamadı. "
            "Firebase Console > Project settings > Service accounts kısmından indirip bot klasörüne koy."
        )

    if not firebase_admin._apps:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(
            cred,
            {"databaseURL": FIREBASE_DATABASE_URL}
        )


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
    if not dt:
        return "-"

    tr_dt = dt.astimezone(timezone(timedelta(hours=3)))
    return tr_dt.strftime("%d.%m.%Y %H:%M")


def make_embed(title, description, color):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Boss Takip Paneli")
    return embed


async def send_channel_message(embed, mention_role=True):
    channel = bot.get_channel(KANAL_ID)

    if channel is None:
        print("Kanal bulunamadı. KANAL_ID doğru mu?")
        return

    content = f"<@&{ROL_ID}>" if mention_role and ROL_ID else None
    await channel.send(content=content, embed=embed)


async def notify_killed(boss_name, channel_no, killed_at, next_spawn):
    embed = make_embed(
        "☠️ Boss Kesildi",
        (
            f"**{boss_name}** Kanal **{channel_no}** kesildi.\n"
            f"Son kesim: **{format_dt(killed_at)}**\n"
            f"Yeni spawn: **{format_dt(next_spawn)}**"
        ),
        0x22c55e
    )
    await send_channel_message(embed, mention_role=True)


async def notify_five_minutes(boss_name, channel_no, next_spawn):
    embed = make_embed(
        "⚠️ 5 Dakika Kaldı",
        (
            f"**{boss_name}** Kanal **{channel_no}** doğmasına **5 dakika kaldı!**\n"
            f"Spawn saati: **{format_dt(next_spawn)}**"
        ),
        0xf97316
    )
    await send_channel_message(embed, mention_role=True)


def get_bosses():
    try:
        data = db.reference(FIREBASE_BOSSES_PATH).get()
    except Exception as exc:
        print("Firebase okuma hatası:", exc)
        return []

    if not isinstance(data, list):
        return []

    return data


def normalize_last_killed_at(last_killed_at):
    if isinstance(last_killed_at, list):
        return {
            str(index): value
            for index, value in enumerate(last_killed_at)
            if value
        }

    if isinstance(last_killed_at, dict):
        return last_killed_at

    return {}


@tasks.loop(seconds=CHECK_SECONDS)
async def boss_control_loop():
    global first_scan_done

    bosses = get_bosses()
    now = datetime.now(timezone.utc)

    print(f"[{format_dt(now)}] Kontrol edildi. Boss sayısı: {len(bosses)}")

    for boss in bosses:
        if not isinstance(boss, dict):
            continue

        boss_id = boss.get("id")
        boss_name = boss.get("name", "Bilinmeyen Boss")
        interval_minutes = int(boss.get("intervalMinutes", 0) or 0)
        last_killed_at = normalize_last_killed_at(boss.get("lastKilledAt") or {})

        if not boss_id or interval_minutes <= 0:
            continue

        for channel_no, killed_value in last_killed_at.items():
            killed_at = parse_time(killed_value)

            if not killed_at:
                continue

            next_spawn = killed_at + timedelta(minutes=interval_minutes)
            state_key = f"{boss_id}:{channel_no}"
            current_kill_iso = killed_at.isoformat()
            previous_kill_iso = last_seen_kills.get(state_key)

            # İlk taramada mevcut kayıtları sadece hafızaya al.
            # Bot çalıştıktan sonra gelen her yeni kayıt bildirim atsın.
            if previous_kill_iso is None:
                last_seen_kills[state_key] = current_kill_iso

                if first_scan_done:
                    await notify_killed(boss_name, channel_no, killed_at, next_spawn)

            elif previous_kill_iso != current_kill_iso:
                last_seen_kills[state_key] = current_kill_iso
                await notify_killed(boss_name, channel_no, killed_at, next_spawn)

            remaining = (next_spawn - now).total_seconds()
            alert_key = f"{boss_id}:{channel_no}:{next_spawn.isoformat()}:5min"

            if 0 < remaining <= 5 * 60 and alert_key not in sent_five_min_alerts:
                sent_five_min_alerts.add(alert_key)

                if first_scan_done:
                    await notify_five_minutes(boss_name, channel_no, next_spawn)

    first_scan_done = True


@bot.command(name="test")
async def test_command(ctx):
    if ctx.channel.id != KANAL_ID:
        return

    embed = make_embed(
        "✅ Bot Aktif",
        "Discord boss bildirim botu çalışıyor.",
        0x3b82f6
    )
    await ctx.send(embed=embed)


@bot.command(name="bossdebug")
async def boss_debug_command(ctx):
    if ctx.channel.id != KANAL_ID:
        return

    bosses = get_bosses()
    embed = make_embed(
        "🧪 Boss Debug",
        f"Firebase'den okunan boss sayısı: **{len(bosses)}**",
        0x3b82f6
    )
    await ctx.send(embed=embed)


@bot.event
async def on_ready():
    print(f"{bot.user} aktif.")

    if not boss_control_loop.is_running():
        boss_control_loop.start()


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN .env içinde yok.")

    init_firebase()
    bot.run(DISCORD_TOKEN)
