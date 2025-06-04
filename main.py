# main.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import os, asyncio, time, json, threading, logging
import psycopg2
from psycopg2.extras import Json
from flask import Flask
from threading import Thread

# Load environment variables
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

# Logger setup
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Flask app (for Render ping)
app = Flask(__name__)

@app.route('/')
def home():
    log.info("🌐 Uptime ping received.")
    return "Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

flask_thread = Thread(target=run_flask)
flask_thread.start()

# Discord bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# PostgreSQL setup
conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        meta_username TEXT,
        data JSONB
    )
""")
conn.commit()

def get_user_entry(user_id: str) -> dict:
    cur.execute("SELECT data FROM users WHERE user_id = %s", (user_id,))
    result = cur.fetchone()
    if result:
        return result[0]
    else:
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
        cur.execute(
            "INSERT INTO users (user_id, meta_username, data) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET meta_username = EXCLUDED.meta_username, data = EXCLUDED.data",
            (user_id, meta_username, Json(entry)))
    else:
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(entry), user_id))
    conn.commit()

def delete_user_entry(user_id: str):
    cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
    conn.commit()

def is_valid_token(entry):
    ts = entry.get("token_timestamp", 0)
    return ts and (time.time() - ts) < 3600

def get_token_seconds_remaining(entry):
    elapsed = time.time() - entry.get("token_timestamp", 0)
    return max(0, int(3600 - elapsed))

def allowed_channel(interaction: discord.Interaction):
    return interaction.channel_id == CHANNEL_ID

@bot.event
async def on_ready():
    log.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
        log.info(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        log.error(f"❌ Sync failed: {e}")
    auto_generate.start()

@tasks.loop(minutes=1)
async def auto_generate():
    cur.execute("SELECT user_id, data FROM users")
    for user_id, data in cur.fetchall():
        data["unmined_rp"] = data.get("unmined_rp", 0) + 1
        data["unmined_cc"] = data.get("unmined_cc", 0) + 15
        save_user_entry(user_id, data)
    log.info("⛏️ Updated mining balances.")

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
        log.error(f"connect error: {e}")

@tree.command(name="unlink", description="Unlink your Meta account", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if not allowed_channel(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        delete_user_entry(str(interaction.user.id))
        await interaction.followup.send("🔌 Disconnected.", ephemeral=True)
    except Exception as e:
        log.error(f"unlink error: {e}")

@tree.command(name="account", description="View your game account", guild=discord.Object(id=GUILD_ID))
async def account(interaction: discord.Interaction):
    if not allowed_channel(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        user_id = str(interaction.user.id)
        entry = get_user_entry(user_id)
        cur.execute("SELECT meta_username FROM users WHERE user_id = %s", (user_id,))
        meta_row = cur.fetchone()
        meta_username = meta_row[0] if meta_row else "UnknownUser"

        if not is_valid_token(entry):
            class RefreshView(discord.ui.View):
                @discord.ui.button(label="Refresh Token", style=discord.ButtonStyle.primary)
                async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
                    entry["token_timestamp"] = time.time()
                    save_user_entry(user_id, entry)
                    await interaction.response.send_message("🔄 Token refreshed. Run `/account` again.", ephemeral=True)

            await interaction.followup.send(embed=discord.Embed(
                title="⚠️ Token Expired",
                description="Your token expired. Refresh below.",
                color=discord.Color.orange()), view=RefreshView(), ephemeral=True)
            return

        nuts, rp, cc = entry.get("nuts", 0), entry.get("rp", 0), entry.get("cc", 0)
        unmined_rp, unmined_cc = entry.get("unmined_rp", 0), entry.get("unmined_cc", 0)
        seconds_left = get_token_seconds_remaining(entry)

        embed = discord.Embed(
            title=f"{meta_username}'s AC Account",
            description=(
                f"🔩 Nuts: {nuts}\n💎 RP: {rp}\n🪙 CC: {cc}\n\n"
                f"⛏️ Mining: {unmined_rp} RP, {unmined_cc} CC\n"
                f"⏳ Token valid for: {seconds_left}s"
            ),
            color=discord.Color.dark_blue()
        )
        avatar_url = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
        embed.set_thumbnail(url=avatar_url)

        class AccountView(discord.ui.View):
            @discord.ui.button(label="Claim Mining Balance", style=discord.ButtonStyle.blurple)
            async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
                nonlocal entry
                if entry["unmined_rp"] == 0 and entry["unmined_cc"] == 0:
                    await interaction.response.send_message("🚫 No resources to claim.", ephemeral=True)
                    return
                rp, cc = entry["unmined_rp"], entry["unmined_cc"]
                entry["rp"] += rp
                entry["cc"] += cc
                entry["unmined_rp"], entry["unmined_cc"] = 0, 0
                save_user_entry(user_id, entry)
                await interaction.response.send_message(embed=discord.Embed(
                    title="✅ Claimed", description=f"+{rp} RP\n+{cc} CC", color=discord.Color.green()
                ), ephemeral=True)

        await interaction.followup.send(embed=embed, view=AccountView(), ephemeral=True)
    except Exception as e:
        log.error(f"account error: {e}")

@tree.command(name="spawnitems", description="Upload a JSON to set loadout or stash", guild=discord.Object(id=GUILD_ID))
async def spawnitems(interaction: discord.Interaction, file: discord.Attachment):
    if not allowed_channel(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        entry = get_user_entry(str(interaction.user.id))
        if not is_valid_token(entry):
            await interaction.followup.send(embed=discord.Embed(
                title="❌ Invalid Token",
                description="Use `/account` to refresh your token.",
                color=discord.Color.red()), ephemeral=True)
            return

        content = await file.read()
        json.loads(content)  # Validate
        embed = discord.Embed(
            title="🧰 Item Upload",
            description="What do you want to do with this file?",
            color=discord.Color.blurple())
        embed.add_field(name="📄 File", value=file.filename, inline=False)

        class SpawnView(discord.ui.View):
            @discord.ui.button(label="Add to Stash", style=discord.ButtonStyle.primary)
            async def stash(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("✅ Added to stash.", ephemeral=True)

            @discord.ui.button(label="Set Loadout", style=discord.ButtonStyle.secondary)
            async def loadout(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("✅ Loadout updated.", ephemeral=True)

        await interaction.followup.send(embed=embed, view=SpawnView(), ephemeral=True)

    except Exception as e:
        log.error(f"spawnitems error: {e}")
        await interaction.followup.send("❌ Invalid file or internal error.", ephemeral=True)

# Run bot
bot.run(TOKEN)
