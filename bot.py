import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

TOKEN          = os.getenv("DISCORD_TOKEN")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PAGE_SIZE = 10

async def get_user_data(session, username):
    try:
        async with session.get(
            f"https://lichess.org/api/user/{username}",
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                d = await resp.json()
                rating = d.get("perfs", {}).get("rapid", {}).get("rating", None)
                games = d.get("perfs", {}).get("rapid", {}).get("games", 0)
                return rating, games
    except:
        pass
    return None, 0

async def get_rating(session, username):
    rating, _ = await get_user_data(session, username)
    return rating

async def fetch_ratings_bulk(usernames, mode="rapid"):
    results = []
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://lichess.org/api/users",
                data=",".join(usernames),
                headers={"Content-Type": "text/plain"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    users = await resp.json()
                    for user in users:
                        rating = user.get("perfs", {}).get(mode, {}).get("rating", None)
                        games = user.get("perfs", {}).get(mode, {}).get("games", 0)
                        results.append({
                            "username": user["username"],
                            "rating": rating if rating else "Unrated",
                            "games": games
                        })
        except Exception as e:
            print(f"Lichess API error: {e}")

    qualified = [x for x in results if isinstance(x["rating"], int) and x["games"] >= 4]
    unqualified = [x for x in results if not (isinstance(x["rating"], int) and x["games"] >= 4)]
    qualified.sort(key=lambda x: x["rating"], reverse=True)
    return qualified + unqualified

def get_guild_data(guild_id):
    gid = str(guild_id)
    result = supabase.table("guild_data").select("*").eq("guild_id", gid).execute()
    if result.data:
        return result.data[0]
    supabase.table("guild_data").insert({"guild_id": gid, "channel_id": None, "leaderboard_message_id": None}).execute()
    return {"guild_id": gid, "channel_id": None, "leaderboard_message_id": None}

def get_members(guild_id):
    gid = str(guild_id)
    result = supabase.table("members").select("*").eq("guild_id", gid).execute()
    return result.data or []

def add_member_db(guild_id, discord_id, username, start_rating):
    supabase.table("members").upsert({
        "guild_id": str(guild_id),
        "discord_id": str(discord_id),
        "username": username.lower(),
        "start_rating": start_rating
    }).execute()

def remove_member_db(guild_id, username):
    supabase.table("members").delete().eq("guild_id", str(guild_id)).eq("username", username.lower()).execute()

def set_channel_db(guild_id, channel_id):
    gid = str(guild_id)
    supabase.table("guild_data").upsert({
        "guild_id": gid,
        "channel_id": str(channel_id),
        "leaderboard_message_id": None
    }).execute()

def set_leaderboard_message(guild_id, message_id):
    supabase.table("guild_data").update({
        "leaderboard_message_id": str(message_id)
    }).eq("guild_id", str(guild_id)).execute()

def build_leaderboard_pages(ratings, members, mode="rapid"):
    medals = ["🥇", "🥈", "🥉"]
    qualified = [r for r in ratings if r["games"] >= 4]
    unqualified = [r for r in ratings if r["games"] < 4]

    lines = []
    for i, entry in enumerate(qualified):
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        rating_display = str(entry["rating"])
        start = None
        if mode == "rapid":
            for m in members:
                if m["username"].lower() == entry["username"].lower():
                    start = m.get("start_rating")
                    break
        if start and isinstance(entry["rating"], int):
            diff = entry["rating"] - start
            sign = "+" if diff >= 0 else ""
            gain = f" `({sign}{diff})`"
        else:
            gain = ""
        lines.append(f"{rank} **{entry['username']}** — {rating_display}{gain}")

    for entry in unqualified:
        rating_display = str(entry["rating"]) if isinstance(entry["rating"], int) else "—"
        lines.append(f"⏳ **{entry['username']}** — {rating_display} *(needs 4+ games)*")

    pages = []
    for i in range(0, max(len(lines), 1), PAGE_SIZE):
        pages.append(lines[i:i+PAGE_SIZE])
    return pages

async def build_leaderboard_embed(guild_id, mode="rapid", page=0):
    members = get_members(guild_id)
    if not members:
        return None, None, "No members registered yet."
    usernames = [m["username"] for m in members]
    ratings = await fetch_ratings_bulk(usernames, mode)
    if not ratings:
        return None, None, "Could not fetch ratings from Lichess."

    pages = build_leaderboard_pages(ratings, members, mode)
    total_pages = len(pages)
    page = max(0, min(page, total_pages - 1))

    mode_display = mode.capitalize()
    embed = discord.Embed(
        title=f"♟️ Anime Soul Chess Squad — {mode_display} Leaderboard",
        description="\n".join(pages[page]),
        color=0x4a90d9,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Page {page+1}/{total_pages} • Lichess {mode_display} Rating • Updates every 3 min • ⏳ = needs 4+ games")
    return embed, total_pages, None

async def build_gain_embed(guild_id, page=0):
    members = get_members(guild_id)
    if not members:
        return None, None, "No members registered yet."
    gains = []
    async with aiohttp.ClientSession() as session:
        for m in members:
            username = m["username"]
            start = m.get("start_rating")
            current, games = await get_user_data(session, username)
            if start and current and games >= 4:
                gains.append({"username": username, "gain": current - start, "current": current})
    if not gains:
        return None, None, "No members with 4+ games yet."
    gains.sort(key=lambda x: x["gain"], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, entry in enumerate(gains):
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        sign = "+" if entry["gain"] >= 0 else ""
        lines.append(f"{rank} **{entry['username']}** — {sign}{entry['gain']} ELO ({entry['current']} current)")

    pages = []
    for i in range(0, max(len(lines), 1), PAGE_SIZE):
        pages.append(lines[i:i+PAGE_SIZE])
    total_pages = len(pages)
    page = max(0, min(page, total_pages - 1))

    embed = discord.Embed(
        title="📈 ELO Gained This Month",
        description="\n".join(pages[page]),
        color=0x2ecc71,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Page {page+1}/{total_pages} • ELO gained since start of month • Lichess Rapid")
    return embed, total_pages, None

class LeaderboardView(discord.ui.View):
    def __init__(self, guild_id, mode="rapid", page=0, gain=False):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.mode = mode
        self.page = page
        self.gain = gain

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self.update(interaction)

    async def update(self, interaction: discord.Interaction):
        if self.gain:
            embed, total_pages, error = await build_gain_embed(self.guild_id, self.page)
        else:
            embed, total_pages, error = await build_leaderboard_embed(self.guild_id, self.mode, self.page)
        if error:
            await interaction.response.send_message(f"⚠️ {error}", ephemeral=True)
            return
        self.page = max(0, min(self.page, total_pages - 1))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= total_pages - 1
        await interaction.response.edit_message(embed=embed, view=self)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=3)
async def live_leaderboard():
    result = supabase.table("guild_data").select("*").execute()
    for guild_data in result.data:
        guild_id = guild_data["guild_id"]
        channel_id = guild_data.get("channel_id")
        if not channel_id:
            continue
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        embed, total_pages, error = await build_leaderboard_embed(guild_id)
        if not embed:
            continue
        msg_id = guild_data.get("leaderboard_message_id")
        try:
            if msg_id:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
            else:
                msg = await channel.send(embed=embed)
                set_leaderboard_message(guild_id, msg.id)
        except discord.NotFound:
            msg = await channel.send(embed=embed)
            set_leaderboard_message(guild_id, msg.id)
        except Exception as e:
            print(f"Leaderboard update error: {e}")

@tasks.loop(hours=1)
async def monthly_reset_and_announce():
    now = datetime.now(timezone.utc)
    if now.day != 1 or now.hour != 0:
        return
    result = supabase.table("guild_data").select("*").execute()
    for guild_data in result.data:
        guild_id = guild_data["guild_id"]
        channel_id = guild_data.get("channel_id")
        members = get_members(guild_id)
        if not members:
            continue

        is_first_month = (now.month == 6 and now.year == 2026)

        if not is_first_month and channel_id:
            channel = bot.get_channel(int(channel_id))
            if channel:
                gains = []
                async with aiohttp.ClientSession() as session:
                    for m in members:
                        username = m["username"]
                        start = m.get("start_rating")
                        current, games = await get_user_data(session, username)
                        if start and current and games >= 4:
                            gains.append({"username": username, "gain": current - start, "current": current})
                if gains:
                    gains.sort(key=lambda x: x["gain"], reverse=True)
                    winner = gains[0]
                    sign = "+" if winner["gain"] >= 0 else ""
                    embed = discord.Embed(
                        title="🎉 Monthly ELO Gain Winner! 🎉",
                        description=(
                            f"🥳 Congratulations to **{winner['username']}**!\n\n"
                            f"🏆 Most ELO gained this month: **{sign}{winner['gain']}** ELO\n"
                            f"📊 Current rating: **{winner['current']}**\n\n"
                            f"✨ 🎊 🎆 🎇 ✨ 🎊 🎆 🎇 ✨"
                        ),
                        color=0xf1c40f,
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.set_footer(text="See you next month! Keep climbing! ♟️")
                    await channel.send(embed=embed)

        async with aiohttp.ClientSession() as session:
            for m in members:
                new_rating, _ = await get_user_data(session, m["username"])
                if new_rating:
                    supabase.table("members").update({"start_rating": new_rating}).eq("guild_id", guild_id).eq("username", m["username"]).execute()

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.tree.sync()
    live_leaderboard.start()
    monthly_reset_and_announce.start()

@bot.tree.command(name="setchannel", description="Set this channel as the leaderboard channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def set_channel(interaction: discord.Interaction):
    set_channel_db(interaction.guild.id, interaction.channel.id)
    await interaction.response.send_message(f"✅ Leaderboard channel set to {interaction.channel.mention}!")

@bot.tree.command(name="joinleaderboard", description="Join the leaderboard with your Lichess username")
@app_commands.describe(lichess_username="Your Lichess username")
async def join_leaderboard(interaction: discord.Interaction, lichess_username: str):
    await interaction.response.defer()
    members = get_members(interaction.guild.id)
    username_lower = lichess_username.lower()
    if any(m["username"] == username_lower for m in members):
        await interaction.followup.send(f"⚠️ **{lichess_username}** is already on the leaderboard.")
        return
    async with aiohttp.ClientSession() as session:
        rating, games = await get_user_data(session, username_lower)
        if rating is None:
            await interaction.followup.send(f"❌ Could not find **{lichess_username}** on Lichess.")
            return
    add_member_db(interaction.guild.id, interaction.user.id, username_lower, rating)
    if games < 4:
        await interaction.followup.send(f"✅ {interaction.user.mention} joined as **{lichess_username}**! Starting rating: **{rating}** ⏳ Play 4+ Rapid games to appear in rankings.")
    else:
        await interaction.followup.send(f"✅ {interaction.user.mention} joined as **{lichess_username}**! Starting rating: **{rating}**")

@bot.tree.command(name="leaderboard", description="Show the Rapid rating leaderboard")
async def show_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, total_pages, error = await build_leaderboard_embed(interaction.guild.id, mode="rapid", page=0)
    if error:
        await interaction.followup.send(f"⚠️ {error}")
        return
    view = LeaderboardView(interaction.guild.id, mode="rapid", page=0)
    view.previous.disabled = True
    if total_pages <= 1:
        view.next.disabled = True
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="blitzleaderboard", description="Show the Blitz rating leaderboard")
async def show_blitz_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, total_pages, error = await build_leaderboard_embed(interaction.guild.id, mode="blitz", page=0)
    if error:
        await interaction.followup.send(f"⚠️ {error}")
        return
    view = LeaderboardView(interaction.guild.id, mode="blitz", page=0)
    view.previous.disabled = True
    if total_pages <= 1:
        view.next.disabled = True
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="bulletleaderboard", description="Show the Bullet rating leaderboard")
async def show_bullet_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, total_pages, error = await build_leaderboard_embed(interaction.guild.id, mode="bullet", page=0)
    if error:
        await interaction.followup.send(f"⚠️ {error}")
        return
    view = LeaderboardView(interaction.guild.id, mode="bullet", page=0)
    view.previous.disabled = True
    if total_pages <= 1:
        view.next.disabled = True
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="gainleaderboard", description="Show who gained the most ELO this month")
async def show_gain_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, total_pages, error = await build_gain_embed(interaction.guild.id, page=0)
    if error:
        await interaction.followup.send(f"⚠️ {error}")
        return
    view = LeaderboardView(interaction.guild.id, gain=True, page=0)
    view.previous.disabled = True
    if total_pages <= 1:
        view.next.disabled = True
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="members", description="List all registered members")
async def list_members(interaction: discord.Interaction):
    members = get_members(interaction.guild.id)
    if not members:
        await interaction.response.send_message("No members yet. Use `/joinleaderboard` to join.")
        return
    names = "\n".join([f"• {m['username']}" for m in sorted(members, key=lambda x: x['username'])])
    embed = discord.Embed(title="♟️ Registered Squad Members", description=names, color=0x1a1a2e)
    embed.set_footer(text=f"{len(members)} members total")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="addmember", description="Add a member to the leaderboard")
@app_commands.describe(lichess_username="Lichess username to add")
@app_commands.checks.has_permissions(manage_messages=True)
async def add_member(interaction: discord.Interaction, lichess_username: str):
    await interaction.response.defer()
    members = get_members(interaction.guild.id)
    username_lower = lichess_username.lower()
    if any(m["username"] == username_lower for m in members):
        await interaction.followup.send(f"⚠️ **{lichess_username}** is already on the leaderboard.")
        return
    async with aiohttp.ClientSession() as session:
        rating, games = await get_user_data(session, username_lower)
        if rating is None:
            await interaction.followup.send(f"❌ Could not find **{lichess_username}** on Lichess.")
            return
    add_member_db(interaction.guild.id, username_lower, username_lower, rating)
    await interaction.followup.send(f"✅ **{lichess_username}** added! Starting rating: **{rating}**")

@bot.tree.command(name="removemember", description="Remove a member from the leaderboard")
@app_commands.describe(lichess_username="Lichess username to remove")
@app_commands.checks.has_permissions(manage_messages=True)
async def remove_member(interaction: discord.Interaction, lichess_username: str):
    members = get_members(interaction.guild.id)
    username_lower = lichess_username.lower()
    if not any(m["username"] == username_lower for m in members):
        await interaction.response.send_message(f"❌ **{lichess_username}** is not on the leaderboard.")
        return
    remove_member_db(interaction.guild.id, username_lower)
    await interaction.response.send_message(f"✅ **{lichess_username}** removed.")

@bot.tree.command(name="chesshelp", description="Show all available commands")
async def chess_help(interaction: discord.Interaction):
    embed = discord.Embed(title="♟️ Chess Squad Bot — Commands", color=0x4a90d9)
    embed.add_field(name="/setchannel",          value="[Admin] Set leaderboard channel",  inline=False)
    embed.add_field(name="/joinleaderboard",     value="Add your Lichess account",          inline=False)
    embed.add_field(name="/leaderboard",         value="Show Rapid leaderboard",            inline=False)
    embed.add_field(name="/blitzleaderboard",    value="Show Blitz leaderboard",            inline=False)
    embed.add_field(name="/bulletleaderboard",   value="Show Bullet leaderboard",           inline=False)
    embed.add_field(name="/gainleaderboard",     value="Show ELO gained this month",        inline=False)
    embed.add_field(name="/members",             value="List all registered members",       inline=False)
    embed.add_field(name="/addmember",           value="[Admin] Add a member",              inline=False)
    embed.add_field(name="/removemember",        value="[Admin] Remove a member",           inline=False)
    embed.set_footer(text="Updates every 3 min • Resets monthly • ⏳ = needs 4+ Rapid games")
    await interaction.response.send_message(embed=embed)

@set_channel.error
@add_member.error
@remove_member.error
async def permission_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.")

if __name__ == "__main__":
    bot.run(TOKEN)
