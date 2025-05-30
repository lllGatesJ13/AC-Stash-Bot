import discord
from discord.ext import commands, tasks
from discord import app_commands
import os, json, time, asyncio
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
DATA_FILE = "data.json"
user_data = {}

# Persistent Data Handling
def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(user_data, f)

def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            user_data = json.load(f)

def get_user_entry(user_id: str):
    if user_id not in user_data:
        user_data[user_id] = {
            "nuts": 0,
            "rp": 0,
            "cc": 0,
            "unmined_rp": 0,
            "unmined_cc": 0,
            "token_timestamp": 0
        }
    return user_data[user_id]

def is_valid_token(entry):
    ts = entry.get("token_timestamp")
    return ts and (time.time() - ts) < 3600

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
    load_data()
    auto_generate.start()

# Background task to generate unmined currency
@tasks.loop(minutes=1)
async def auto_generate():
    for entry in user_data.values():
        entry["unmined_rp"] = entry.get("unmined_rp", 0) + 1
        entry["unmined_cc"] = entry.get("unmined_cc", 0) + 15
    save_data()

# /connect command
@tree.command(name="connect", description="Link your Meta username", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(meta_username="Your Meta username")
async def connect(interaction: discord.Interaction, meta_username: str):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    entry = get_user_entry(str(interaction.user.id))
    entry["meta_username"] = meta_username
    entry["token_timestamp"] = time.time()
    save_data()
    await interaction.followup.send(f"🔗 Connected as `{meta_username}`", ephemeral=True)

# /unlink command
@tree.command(name="unlink", description="Unlink your Meta account", guild=discord.Object(id=GUILD_ID))
async def unlink(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    user_data.pop(str(interaction.user.id), None)
    save_data()
    await interaction.followup.send("🔌 Disconnected.", ephemeral=True)

# /account command
@tree.command(name="account", description="View your account", guild=discord.Object(id=GUILD_ID))
async def account(interaction: discord.Interaction):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    entry = get_user_entry(uid)

    if "meta_username" not in entry:
        await interaction.followup.send("❌ Not connected. Use `/connect`.", ephemeral=True)
        return

    if not is_valid_token(entry):
        class RefreshView(discord.ui.View):
            @discord.ui.button(label="Refresh Token", style=discord.ButtonStyle.primary)
            async def refresh_token(self, interaction: discord.Interaction, button: discord.ui.Button):
                uid = str(interaction.user.id)
                entry = get_user_entry(uid)
                entry["token_timestamp"] = time.time()
                save_data()
                await interaction.response.edit_message(
                    content="🔁 Token refreshed. Run `/account` again.",
                    embed=None,
                    view=None
                )

        await interaction.followup.send("⚠️ Token invalid. Please refresh:", view=RefreshView(), ephemeral=True)
        return

    nuts = entry.get("nuts", 0)
    rp = entry.get("rp", 0)
    cc = entry.get("cc", 0)
    unmined_rp = entry.get("unmined_rp", 0)
    unmined_cc = entry.get("unmined_cc", 0)
    token_secs = int(3600 - (time.time() - entry["token_timestamp"]))

    embed = discord.Embed(
        title=f"{entry['meta_username']}'s AC Account",
        color=discord.Color.dark_blue()
    )
    embed.add_field(name="💳 | 𝗪𝗮𝗹𝗹𝗲𝘁", value=f"🔩 Nuts: {nuts}\n💎 Research Points: {rp}\n🪙 Company Coins: {cc}", inline=False)
    embed.add_field(name="⛏️ | 𝗠𝗶𝗻𝗶𝗻𝗴 𝗕𝗮𝗹𝗮𝗻𝗰𝗲", value=f"💎 Research Points: {unmined_rp}\n🪙 Company Coins: {unmined_cc}", inline=False)
    embed.add_field(name="📄 | Auth Token", value=f"✅ VALID FOR: {token_secs} seconds", inline=False)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    class ClaimView(discord.ui.View):
        @discord.ui.button(label="𝐂𝐥𝐚𝐢𝐦 𝐌𝐢𝐧𝐢𝐧𝐠 𝐁𝐚𝐥𝐚𝐧𝐜𝐞", style=discord.ButtonStyle.blurple)
        async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
            uid = str(interaction.user.id)
            e = get_user_entry(uid)
            e["rp"] += e.get("unmined_rp", 0)
            e["cc"] += e.get("unmined_cc", 0)
            e["unmined_rp"] = 0
            e["unmined_cc"] = 0
            save_data()
            await interaction.response.send_message(
                content="✅ | Successfully Claimed. Re-run `/account` to see changes.",
                ephemeral=True
            )

    await interaction.followup.send(embed=embed, view=ClaimView(), ephemeral=True)

# /spawnitems command
@tree.command(name="spawnitems", description="Upload a JSON file to set loadout or stash", guild=discord.Object(id=GUILD_ID))
async def spawnitems(interaction: discord.Interaction, file: discord.Attachment):
    if not allowed_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    entry = get_user_entry(str(interaction.user.id))

    if "meta_username" not in entry:
        await interaction.followup.send("❌ Not connected. Use `/connect`.", ephemeral=True)
        return
    if not is_valid_token(entry):
        await interaction.followup.send("❌ Token invalid. Use `/account` to refresh.", ephemeral=True)
        return

    try:
        content = await file.read()
        json_data = json.loads(content)
    except Exception:
        await interaction.followup.send("❌ Invalid JSON file.", ephemeral=True)
        return

    class SpawnView(discord.ui.View):
        @discord.ui.button(label="Add to Stash", style=discord.ButtonStyle.primary)
        async def stash(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("✅ Item added to stash.", ephemeral=True)

        @discord.ui.button(label="Set Loadout", style=discord.ButtonStyle.secondary)
        async def loadout(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("✅ Loadout set.", ephemeral=True)

    await interaction.followup.send("What would you like to do with this? (We are in development, this will not affect the real game)", view=SpawnView(), ephemeral=True)

bot.run(TOKEN)
