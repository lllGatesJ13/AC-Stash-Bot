import discord
from discord.ext import commands, tasks
from discord import app_commands
import os, json, time, asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from flask import Flask
import threading

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Flask app for UptimeRobot monitoring
from aiohttp import web

async def handle(request):
    print("🔔 Ping from UptimeRobot received")
    return web.Response(text="Bot is running!")

def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)

    async def run():
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 5000)
        await site.start()
        print("🌐 aiohttp web server started on port 5000 for UptimeRobot")

    asyncio.create_task(run())

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_database():
    """Initialize the database table if it doesn't exist"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_data (
            user_id VARCHAR(50) PRIMARY KEY,
            nuts INTEGER DEFAULT 0,
            rp INTEGER DEFAULT 0,
            cc INTEGER DEFAULT 0,
            unmined_rp INTEGER DEFAULT 0,
            unmined_cc INTEGER DEFAULT 0,
            token_timestamp FLOAT DEFAULT 0,
            meta_username VARCHAR(255)
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

def get_user_entry(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM user_data WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    
    if result is None:
        # Create new user entry
        cursor.execute("""
            INSERT INTO user_data (user_id, nuts, rp, cc, unmined_rp, unmined_cc, token_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (user_id, 0, 0, 0, 0, 0, 0))
        result = cursor.fetchone()
        conn.commit()
    
    cursor.close()
    conn.close()
    return dict(result)

def save_user_entry(user_id: str, entry: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_data SET 
            nuts = %s, rp = %s, cc = %s, unmined_rp = %s, unmined_cc = %s, 
            token_timestamp = %s, meta_username = %s
        WHERE user_id = %s
    """, (
        entry.get("nuts", 0),
        entry.get("rp", 0), 
        entry.get("cc", 0),
        entry.get("unmined_rp", 0),
        entry.get("unmined_cc", 0),
        entry.get("token_timestamp", 0),
        entry.get("meta_username"),
        user_id
    ))
    conn.commit()
    cursor.close()
    conn.close()

def get_all_users():
    """Get all user IDs from database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM user_data")
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return [row[0] for row in results]

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
    print(f"✅ Bot is ready: {bot.user} (ID: {bot.user.id})")
    guild = discord.Object(id=GUILD_ID)
    try:
        synced = await tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    
    # Initialize database
    init_database()
    auto_generate.start()
    
    # Start Flask server in background thread
      start_webserver()

@tasks.loop(minutes=1)
async def auto_generate():
    user_ids = get_all_users()
    for user_id in user_ids:
        entry = get_user_entry(user_id)
        entry["unmined_rp"] = entry.get("unmined_rp", 0) + 1
        entry["unmined_cc"] = entry.get("unmined_cc", 0) + 15
        save_user_entry(user_id, entry)

@tree.command(name="connect", description="Link your Meta username", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(meta_username="Your Meta username")
async def connect(interaction: discord.Interaction, meta_username: str):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    entry = get_user_entry(str(interaction.user.id))
    entry["meta_username"] = meta_username
    entry["token_timestamp"] = time.time()
    save_user_entry(str(interaction.user.id), entry)
    await interaction.followup.send(f"🔗 Connected as `{meta_username}`", ephemeral=True)

@tree.command(name="unlink", description="Unlink your Meta account", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_data WHERE user_id = %s", (str(interaction.user.id),))
    conn.commit()
    cursor.close()
    conn.close()
    
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

        @discord.ui.button(label="𝐂𝐥𝐚𝐢𝐦 𝐌𝐢𝐧𝐢𝐧𝐠 𝐁𝐚𝐥𝐚𝐧𝐜𝐞", style=discord.ButtonStyle.blurple)
        async def claim_balances(self, interaction: discord.Interaction, button: discord.ui.Button):
            nonlocal entry
            if entry.get("unmined_rp", 0) == 0 and entry.get("unmined_cc", 0) == 0:
                await interaction.response.send_message("🚫 You have no unmined resources to claim.", ephemeral=True)
                return

            claimed_rp = entry.get("unmined_rp", 0)
            claimed_cc = entry.get("unmined_cc", 0)
            entry["rp"] += claimed_rp
            entry["cc"] += claimed_cc
            entry["unmined_rp"] = 0
            entry["unmined_cc"] = 0
            save_user_entry(user_id, entry)

            embed = discord.Embed(
                title="⛏️ Mining Balances Claimed",
                description="Your unmined resources have been added to your wallet.",
                color=discord.Color.green()
            )
            embed.add_field(name="💎 Research Points", value=f"+{claimed_rp} RP", inline=True)
            embed.add_field(name="🪙 Company Coins", value=f"+{claimed_cc} CC", inline=True)
            embed.set_footer(text="Use `/account` again to see your updated balances.")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    await interaction.followup.send(embed=embed, view=AccountView(), ephemeral=True)

@tree.command(name="spawnitems", description="Upload a JSON file to set loadout or stash", guild=discord.Object(id=GUILD_ID))
async def spawnitems(interaction: discord.Interaction, file: discord.Attachment):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    entry = get_user_entry(str(interaction.user.id))

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

    if not is_valid_token(entry):
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Invalid Token",
                description="Your token has expired or is invalid.\nPlease refresh it using `/account`.",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return

    try:
        content = await file.read()
        json_data = json.loads(content)
    except Exception:
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Invalid File",
                description="The file you uploaded is not valid JSON. Please check and try again.",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🧰 Item Upload",
        description="What would you like to do with this file?",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📄 File Name", value=file.filename, inline=False)
    embed.add_field(name="⚠️ Note", value="We are in development — this will not affect the real game.", inline=False)

    class SpawnView(discord.ui.View):
        @discord.ui.button(label="Add to Stash", style=discord.ButtonStyle.primary)
        async def stash(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Success",
                    description="Item added to your stash.",
                    color=discord.Color.green()
                ),
                ephemeral=True
            )

        @discord.ui.button(label="Set Loadout", style=discord.ButtonStyle.secondary)
        async def loadout(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Success",
                    description="Your loadout has been updated.",
                    color=discord.Color.green()
                ),
                ephemeral=True
            )

    await interaction.followup.send(embed=embed, view=SpawnView(), ephemeral=True)

bot.run(TOKEN)
