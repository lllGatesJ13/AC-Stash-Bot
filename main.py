import discord
from discord.ext import commands, tasks
from discord import app_commands
import os, json, time, asyncio
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# --- DATABASE SETUP ---

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True  # commits automatically after each execute
cursor = conn.cursor(cursor_factory=RealDictCursor)

# Create table if not exists
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    discord_id TEXT PRIMARY KEY,
    meta_username TEXT,
    nuts INTEGER DEFAULT 0,
    rp INTEGER DEFAULT 0,
    cc INTEGER DEFAULT 0,
    unmined_rp INTEGER DEFAULT 0,
    unmined_cc INTEGER DEFAULT 0,
    token_timestamp DOUBLE PRECISION DEFAULT 0
);
""")

# --- DATABASE ACCESS HELPERS ---

def get_user_entry(user_id: str):
    cursor.execute("SELECT * FROM users WHERE discord_id = %s;", (user_id,))
    entry = cursor.fetchone()
    if not entry:
        # Insert new user with default values
        cursor.execute("""
            INSERT INTO users (discord_id) VALUES (%s);
        """, (user_id,))
        return {
            "discord_id": user_id,
            "meta_username": None,
            "nuts": 0,
            "rp": 0,
            "cc": 0,
            "unmined_rp": 0,
            "unmined_cc": 0,
            "token_timestamp": 0
        }
    return dict(entry)

def save_user_entry(user_id: str, entry: dict):
    cursor.execute("""
        INSERT INTO users (discord_id, meta_username, nuts, rp, cc, unmined_rp, unmined_cc, token_timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (discord_id) DO UPDATE SET
            meta_username = EXCLUDED.meta_username,
            nuts = EXCLUDED.nuts,
            rp = EXCLUDED.rp,
            cc = EXCLUDED.cc,
            unmined_rp = EXCLUDED.unmined_rp,
            unmined_cc = EXCLUDED.unmined_cc,
            token_timestamp = EXCLUDED.token_timestamp
        ;
    """, (
        user_id,
        entry.get("meta_username"),
        entry.get("nuts", 0),
        entry.get("rp", 0),
        entry.get("cc", 0),
        entry.get("unmined_rp", 0),
        entry.get("unmined_cc", 0),
        entry.get("token_timestamp", 0)
    ))

def delete_user_entry(user_id: str):
    cursor.execute("DELETE FROM users WHERE discord_id = %s;", (user_id,))

def get_all_users():
    cursor.execute("SELECT * FROM users;")
    return cursor.fetchall()

# --- TOKEN UTILS ---

def is_valid_token(entry):
    ts = entry.get("token_timestamp", 0)
    return ts and (time.time() - ts) < 3600

def get_token_seconds_remaining(entry):
    elapsed = time.time() - entry.get("token_timestamp", 0)
    return max(0, int(3600 - elapsed))

def allowed_channel(interaction: discord.Interaction):
    return interaction.channel_id == CHANNEL_ID

# --- BOT EVENTS AND COMMANDS ---

@bot.event
async def on_ready():
    print(f"✅ Bot is ready: {bot.user} (ID: {bot.user.id})")
    guild = discord.Object(id=GUILD_ID)
    try:
        synced = await tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    auto_generate.start()

@tasks.loop(minutes=1)
async def auto_generate():
    # Add RP and CC mining balances every minute
    users = get_all_users()
    for entry in users:
        entry["unmined_rp"] = entry.get("unmined_rp", 0) + 1
        entry["unmined_cc"] = entry.get("unmined_cc", 0) + 15
        save_user_entry(entry["discord_id"], entry)

@tree.command(name="connect", description="Link your Meta username", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(meta_username="Your Meta username")
async def connect(interaction: discord.Interaction, meta_username: str):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    entry = get_user_entry(user_id)
    entry["meta_username"] = meta_username
    entry["token_timestamp"] = time.time()
    save_user_entry(user_id, entry)
    await interaction.followup.send(f"🔗 Connected as `{meta_username}`", ephemeral=True)

@tree.command(name="unlink", description="Unlink your Meta account", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    delete_user_entry(user_id)
    await interaction.followup.send("🔌 Disconnected.", ephemeral=True)

@tree.command(name="account", description="View your game account", guild=discord.Object(id=GUILD_ID))
async def account(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    entry = get_user_entry(user_id)

    if not entry.get("meta_username"):
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Not Connected",
                description="You need to link your Meta account using `/connect`.",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return

    token_valid = is_valid_token(entry)

    if not token_valid:
        class RefreshView(discord.ui.View):
            @discord.ui.button(label="Refresh Token", style=discord.ButtonStyle.primary)
            async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
                entry["token_timestamp"] = time.time()
                save_user_entry(user_id, entry)
                await interaction.response.send_message(
                    "🔄 Token refreshed. Please run `/account` again to view your dashboard.",
                    ephemeral=True
                )
        embed = discord.Embed(
            title="⚠️ Token Expired",
            description="Your auth token has expired. Please refresh.",
            color=discord.Color.orange()
        )
        await interaction.followup.send(embed=embed, view=RefreshView(), ephemeral=True)
        return

    # Valid token: show full account
    nuts = entry.get("nuts", 0)
    rp = entry.get("rp", 0)
    cc = entry.get("cc", 0)
    unmined_rp = entry.get("unmined_rp", 0)
    unmined_cc = entry.get("unmined_cc", 0)
    seconds_left = get_token_seconds_remaining(entry)
    meta_username = entry.get("meta_username", "UnknownUser")

    embed = discord.Embed(
        title=f"{meta_username}'s AC Account",
        color=discord.Color.dark_blue()
    )
    embed.description = (
        "🪪 **| 𝐖𝐚𝐥𝐥𝐞𝐭**\n"
        f"🔩 𝗡𝘂𝘁𝘀: {nuts}\n"
        f"💎 𝗥𝗣: {rp}\n"
        f"🪙 𝗖𝗖: {cc}\n\n"
        "⛏️ **| 𝐌𝐢𝐧𝐢𝐧𝐠 𝐁𝐚𝐥𝐚𝐧𝐜𝐞**\n"
        f"💎 𝗥𝗣: {unmined_rp}\n"
        f"🪙 𝗖𝗖: {unmined_cc}\n\n"
        "📄 **| 𝐀𝐮𝐭𝐡 𝐓𝐨𝐤𝐞𝐧**\n"
        f"✅ VALID FOR: {seconds_left} seconds"
    )

    avatar_url = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
    embed.set_thumbnail(url=avatar_url)

    class AccountView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="𝐂𝐥𝐚𝐢𝐦 𝐌𝐢𝐧𝐢𝐧𝐠 𝐁𝐚𝐥𝐚𝐧𝐜𝐞𝐬", style=discord.ButtonStyle.green)
        async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
            if str(interaction.user.id) != user_id:
                await interaction.response.send_message("This is not your account.", ephemeral=True)
                return
            entry = get_user_entry(user_id)
            entry["rp"] += entry.get("unmined_rp", 0)
            entry["cc"] += entry.get("unmined_cc", 0)
            entry["unmined_rp"] = 0
            entry["unmined_cc"] = 0
            save_user_entry(user_id, entry)
            await interaction.response.send_message("✅ Mining balances claimed!", ephemeral=True)

    await interaction.followup.send(embed=embed, view=AccountView(), ephemeral=True)

@tree.command(name="spawnitems", description="Upload JSON items to your stash or loadout", guild=discord.Object(id=GUILD_ID))
async def spawnitems(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    entry = get_user_entry(user_id)

    if not entry.get("meta_username"):
        await interaction.followup.send("❌ You must `/connect` your account first.", ephemeral=True)
        return

    if not is_valid_token(entry):
        await interaction.followup.send("❌ Your token is invalid or expired. Please refresh it via `/account`.", ephemeral=True)
        return

    # Present a modal or interaction to upload file + choose "Add to Stash" or "Set Loadout"
    # For brevity, just a placeholder response:
    await interaction.followup.send("Please upload your JSON file and choose what to do (Add to Stash or Set Loadout).", ephemeral=True)

# You can add additional logic for file uploads and buttons here, matching your current spawnitems flow.

# --- RUN BOT ---
bot.run(TOKEN)
