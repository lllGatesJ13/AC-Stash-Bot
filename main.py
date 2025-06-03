import discord
from discord.ext import commands, tasks
from discord import app_commands
import os, time, asyncio, json
import aiohttp
import asyncpg
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

db = None  # Will be asyncpg connection pool

# ─────────────────────────────────────────────
# Database Setup
# ─────────────────────────────────────────────

async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            meta_username TEXT,
            nuts INTEGER DEFAULT 0,
            rp INTEGER DEFAULT 0,
            cc INTEGER DEFAULT 0,
            unmined_rp INTEGER DEFAULT 0,
            unmined_cc INTEGER DEFAULT 0,
            token_timestamp DOUBLE PRECISION DEFAULT 0
        )
    """)

async def get_user(discord_id):
    user = await db.fetchrow("SELECT * FROM users WHERE discord_id = $1", str(discord_id))
    if not user:
        await db.execute("INSERT INTO users (discord_id) VALUES ($1)", str(discord_id))
        user = await db.fetchrow("SELECT * FROM users WHERE discord_id = $1", str(discord_id))
    return dict(user)

async def update_user(discord_id, **kwargs):
    fields = ', '.join(f"{k} = ${i+2}" for i, k in enumerate(kwargs))
    values = list(kwargs.values())
    await db.execute(f"UPDATE users SET {fields} WHERE discord_id = $1", str(discord_id), *values)

async def delete_user(discord_id):
    await db.execute("DELETE FROM users WHERE discord_id = $1", str(discord_id))

# ─────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────

def allowed_channel(interaction: discord.Interaction):
    return interaction.channel_id == CHANNEL_ID

def is_valid_token(ts: float):
    return ts and (time.time() - ts) < 3600

def token_seconds_remaining(ts: float):
    return max(0, int(3600 - (time.time() - ts)))

# ─────────────────────────────────────────────
# Bot Events
# ─────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    await init_db()
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Synced commands to guild {GUILD_ID}")
    auto_generate.start()

@tasks.loop(minutes=1)
async def auto_generate():
    await db.execute("""
        UPDATE users
        SET unmined_rp = unmined_rp + 1,
            unmined_cc = unmined_cc + 15
    """)

# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

@tree.command(name="connect", description="Link your Meta username", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(meta_username="Your Meta username")
async def connect(interaction: discord.Interaction, meta_username: str):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    await update_user(interaction.user.id, meta_username=meta_username, token_timestamp=time.time())
    await interaction.followup.send(f"🔗 Connected as `{meta_username}`", ephemeral=True)

@tree.command(name="unlink", description="Unlink your Meta account", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    await delete_user(interaction.user.id)
    await interaction.followup.send("🔌 Disconnected.", ephemeral=True)

@tree.command(name="account", description="View your game account", guild=discord.Object(id=GUILD_ID))
async def account(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user = await get_user(interaction.user.id)

    if not user.get("meta_username"):
        return await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Not Connected",
                description="You need to link your Meta account using `/connect`.",
                color=discord.Color.red()
            ),
            ephemeral=True
        )

    if not is_valid_token(user["token_timestamp"]):
        class RefreshView(discord.ui.View):
            @discord.ui.button(label="Refresh Token", style=discord.ButtonStyle.primary)
            async def refresh(self, i: discord.Interaction, b: discord.ui.Button):
                await update_user(i.user.id, token_timestamp=time.time())
                await i.response.send_message("🔄 Token refreshed. Please run `/account` again.", ephemeral=True)

        return await interaction.followup.send(
            embed=discord.Embed(
                title="⚠️ Token Expired",
                description="Your auth token has expired. Please refresh.",
                color=discord.Color.orange()
            ),
            view=RefreshView(),
            ephemeral=True
        )

    avatar_url = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
    embed = discord.Embed(
        title=f"{user['meta_username']}'s AC Account",
        color=discord.Color.dark_blue()
    )
    embed.description = (
        "🪪 **| Wallet**\n"
        f"🔩 Nuts: {user['nuts']}\n"
        f"💎 RP: {user['rp']}\n"
        f"🪙 CC: {user['cc']}\n\n"
        "⛏️ **| Mining Balance**\n"
        f"💎 RP: {user['unmined_rp']}\n"
        f"🪙 CC: {user['unmined_cc']}\n\n"
        "📄 **| Auth Token**\n"
        f"✅ VALID FOR: {token_seconds_remaining(user['token_timestamp'])} seconds"
    )
    embed.set_thumbnail(url=avatar_url)

    class AccountView(discord.ui.View):
        @discord.ui.button(label="Claim Mining Balance", style=discord.ButtonStyle.blurple)
        async def claim(self, i: discord.Interaction, b: discord.ui.Button):
            u = await get_user(i.user.id)
            if u["unmined_rp"] == 0 and u["unmined_cc"] == 0:
                return await i.response.send_message("🚫 No unmined resources to claim.", ephemeral=True)
            await update_user(i.user.id,
                rp=u["rp"] + u["unmined_rp"],
                cc=u["cc"] + u["unmined_cc"],
                unmined_rp=0,
                unmined_cc=0
            )
            await i.response.send_message(
                embed=discord.Embed(
                    title="✅ Claimed",
                    description=f"💎 +{u['unmined_rp']} RP\n🪙 +{u['unmined_cc']} CC",
                    color=discord.Color.green()
                ),
                ephemeral=True
            )

    await interaction.followup.send(embed=embed, view=AccountView(), ephemeral=True)

@tree.command(name="spawnitems", description="Upload a JSON file to set loadout or stash", guild=discord.Object(id=GUILD_ID))
async def spawnitems(interaction: discord.Interaction, file: discord.Attachment):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user = await get_user(interaction.user.id)

    if not user.get("meta_username"):
        return await interaction.followup.send(
            embed=discord.Embed(title="❌ Not Connected", description="Use `/connect` first.", color=discord.Color.red()),
            ephemeral=True
        )
    if not is_valid_token(user["token_timestamp"]):
        return await interaction.followup.send(
            embed=discord.Embed(title="❌ Token Expired", description="Please refresh via `/account`.", color=discord.Color.red()),
            ephemeral=True
        )

    try:
        content = await file.read()
        json_data = json.loads(content)
    except Exception:
        return await interaction.followup.send(
            embed=discord.Embed(title="❌ Invalid JSON", description="Check your file formatting.", color=discord.Color.red()),
            ephemeral=True
        )

    embed = discord.Embed(
        title="🧰 Item Upload",
        description="What would you like to do with this file?",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📄 File Name", value=file.filename, inline=False)

    class SpawnView(discord.ui.View):
        @discord.ui.button(label="Add to Stash", style=discord.ButtonStyle.primary)
        async def stash(self, i: discord.Interaction, b: discord.ui.Button):
            await i.response.send_message("✅ Item added to stash.", ephemeral=True)

        @discord.ui.button(label="Set Loadout", style=discord.ButtonStyle.secondary)
        async def loadout(self, i: discord.Interaction, b: discord.ui.Button):
            await i.response.send_message("✅ Loadout updated.", ephemeral=True)

    await interaction.followup.send(embed=embed, view=SpawnView(), ephemeral=True)

# ─────────────────────────────────────────────
# Optional: Web server to keep alive on Render/UptimeRobot
# ─────────────────────────────────────────────

async def ping_server():
    app = aiohttp.web.Application()
    async def handle(_): return aiohttp.web.Response(text="Bot is running.")
    app.router.add_get("/", handle)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, port=8080)
    await site.start()

async def start_all():
    await ping_server()
    await bot.start(TOKEN)

asyncio.run(start_all())
