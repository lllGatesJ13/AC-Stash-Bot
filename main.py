# main.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import os, time, json, logging
import psycopg2
from psycopg2.extras import Json
from flask import Flask
from threading import Thread

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ACBot")

# Load secrets
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

# Flask for uptime pings
app = Flask(__name__)
@app.route('/')
def home():
    log.info("🌐 Uptime ping received")
    return "Bot is alive", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_flask).start()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# PostgreSQL setup
try:
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            meta_username TEXT,
            data JSONB
        )
    """)
    conn.commit()
    log.info("✅ PostgreSQL table 'users' ensured")
except Exception as e:
    log.error(f"❌ Database setup error: {e}")
    raise

# Helpers
def get_user_entry(user_id: str) -> dict:
    cur.execute("SELECT data FROM users WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    if result:
        return result[0]
    entry = {
        "nuts": 0, "rp": 0, "cc": 0,
        "unmined_rp": 0, "unmined_cc": 0,
        "token_timestamp": 0
    }
    cur.execute("INSERT INTO users (user_id, data) VALUES (%s, %s)", (user_id, Json(entry)))
    conn.commit()
    return entry

def save_user_entry(user_id: str, entry: dict, meta_username=None):
    if meta_username:
        cur.execute("""
            INSERT INTO users (user_id, meta_username, data)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
            SET meta_username = EXCLUDED.meta_username,
                data = EXCLUDED.data
        """, (user_id, meta_username, Json(entry)))
    else:
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(entry), user_id))
    conn.commit()

def delete_user_entry(user_id: str):
    cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
    conn.commit()

def is_valid_token(entry: dict) -> bool:
    ts = entry.get("token_timestamp", 0)
    return ts and (time.time() - ts) < 3600

def get_token_seconds_remaining(entry: dict) -> int:
    return max(0, int(3600 - (time.time() - entry.get("token_timestamp", 0))))

def allowed_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel_id == CHANNEL_ID

# Events
@bot.event
async def on_ready():
    log.info(f"✅ Bot ready: {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
        log.info(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        log.error(f"❌ Command sync error: {e}")
    auto_generate.start()

@tasks.loop(minutes=1)
async def auto_generate():
    try:
        cur.execute("SELECT user_id, data FROM users")
        for user_id, data in cur.fetchall():
            data["unmined_rp"] = data.get("unmined_rp", 0) + 1
            data["unmined_cc"] = data.get("unmined_cc", 0) + 15
            save_user_entry(user_id, data)
        log.info("⛏️ Mining balances updated")
    except Exception as e:
        log.error(f"⛏️ auto_generate error: {e}")

# Slash commands
@tree.command(name="connect", description="Link your Meta username", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(meta_username="Your Meta username")
async def connect(interaction: discord.Interaction, meta_username: str):
    if not allowed_channel(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        entry = get_user_entry(str(interaction.user.id))
        entry["token_timestamp"] = time.time()
        save_user_entry(str(interaction.user.id), entry, meta_username=meta_username)
        await interaction.followup.send(f"🔗 Connected as `{meta_username}`", ephemeral=True)
    except Exception as e:
        log.error(f"/connect error: {e}")
        await interaction.followup.send("❌ Failed to connect.", ephemeral=True)

@tree.command(name="unlink", description="Unlink your Meta account", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if not allowed_channel(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        delete_user_entry(str(interaction.user.id))
        await interaction.followup.send("🔌 Disconnected.", ephemeral=True)
    except Exception as e:
        log.error(f"/unlink error: {e}")
        await interaction.followup.send("❌ Failed to unlink.", ephemeral=True)

@tree.command(name="account", description="View your game account", guild=discord.Object(id=GUILD_ID))
async def account(interaction: discord.Interaction):
    if not allowed_channel(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        user_id = str(interaction.user.id)
        entry = get_user_entry(user_id)
        cur.execute("SELECT meta_username FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        meta_username = row[0] if row else "Unknown"

        if not is_valid_token(entry):
            class RefreshView(discord.ui.View):
                @discord.ui.button(label="Refresh Token", style=discord.ButtonStyle.primary)
                async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
                    entry["token_timestamp"] = time.time()
                    save_user_entry(user_id, entry)
                    await interaction.response.send_message("🔄 Token refreshed. Try `/account` again.", ephemeral=True)

            await interaction.followup.send(embed=discord.Embed(
                title="⚠️ Token Expired",
                description="Click below to refresh.",
                color=discord.Color.orange()), view=RefreshView(), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{meta_username}'s Account",
            description=(
                f"🔩 Nuts: {entry['nuts']}\n"
                f"💎 RP: {entry['rp']}\n"
                f"🪙 CC: {entry['cc']}\n\n"
                f"⛏️ Mining: +{entry['unmined_rp']} RP / +{entry['unmined_cc']} CC\n"
                f"⏳ Token valid for {get_token_seconds_remaining(entry)}s"
            ),
            color=discord.Color.blue()
        )
        avatar = interaction.user.avatar or interaction.user.default_avatar
        embed.set_thumbnail(url=avatar.url)

        class AccountView(discord.ui.View):
            @discord.ui.button(label="Claim Mining", style=discord.ButtonStyle.success)
            async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
                if entry["unmined_rp"] == 0 and entry["unmined_cc"] == 0:
                    await interaction.response.send_message("🚫 Nothing to claim.", ephemeral=True)
                    return
                entry["rp"] += entry["unmined_rp"]
                entry["cc"] += entry["unmined_cc"]
                rp, cc = entry["unmined_rp"], entry["unmined_cc"]
                entry["unmined_rp"] = 0
                entry["unmined_cc"] = 0
                save_user_entry(user_id, entry)
                await interaction.response.send_message(
                    f"✅ Claimed {rp} RP & {cc} CC", ephemeral=True
                )

        await interaction.followup.send(embed=embed, view=AccountView(), ephemeral=True)
    except Exception as e:
        log.error(f"/account error: {e}")
        await interaction.followup.send("❌ Failed to load account.", ephemeral=True)

# Start bot
bot.run(TOKEN)
