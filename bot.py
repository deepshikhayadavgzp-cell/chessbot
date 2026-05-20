import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TOKEN        = os.getenv("DISCORD_TOKEN")
DATA_FILE    = "data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_guild_data(data, guild_id):
    gid = str(guild_id)
    if gid not in data:
        data[gid] = {"channel_id": None, "members": {}, "leaderboard_message_id": None}
    return data[gid]

async def get_rating(session, username):
    try:
        async with session.get(
            f"https://lichess.org/api/user/{username}",
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                d = await resp.json()
                return d.get("perfs", {}).get("rapid", {}).get("rating", None)
    except:
        pass
    return None

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
                        results.append({
                            "username": user["username"],
                            "rating": rating if rating else "Unrated"
                        })
        except Exception as e:
            print(f"Lichess API error: {e}")
    results.sort(key=lambda x: x["rating"] if isinstance(x["rating"], int) else -1, reverse=True)
    return results

async def build_leaderboard_embed(guild_data, mode="rapid"):
    members = guild_data.get("members", {})
    if not members:
        return None, "No members registered yet."
    usernames = [v["username"] for v in members.values()]
    ratings = await fetch_ratings_bulk(usernames, mode)
    if not ratings:
        return None, "Could not fetch ratings from Lichess."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, entry in enumerate(ratings):
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        rating_display = str(entry["rating"]) if entry["rating"] != "Unrated" else "—"
        start = None
        if mode == "rapid":
            for v in members.values():
                if v["username"].lower() == entry["username"].lower():
                    start = v.get("start_rating")
                    break
        if start and isinstance(entry["rating"], int):
            diff = entry["rating"] - start
            sign = "+" if diff >= 0 else ""
            gain = f" `({sign}{diff})`"
        else:
            gain = ""
        lines.append(f"{rank} **{entry['username']}** — {rating_display}{gain}")
    mode_display = mode.capitalize()
    embed = discord.Embed(
        title=f"♟️ Anime Soul Chess Squad — {mode_display} Leaderboard",
        description="\n".join(lines),
        color=0x4a90d9,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Lichess {mode_display} Rating  •  Live — updates every 10 minutes")
    return embed, None

async def build_gain_embed(guild_data):
    members = guild_data.get("members", {})
    if not members:
        return None, "No members registered yet."
    gains = []
    async with aiohttp.ClientSession() as session:
        for v in members.values():
            username = v["username"]
            start = v.get("start_rating")
            current = await get_rating(session, username)
            if start and current:
                gains.append({"username": username, "gain": current - start, "current": current})
    if not gains:
        return None, "Could not fetch ratings."
    gains.sort(key=lambda x: x["gain"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, entry in enumerate(gains):
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        sign = "+" if entry["gain"] >= 0 else ""
        lines.append(f"{rank} **{entry['username']}** — {sign}{entry['gain']} ELO ({entry['current']} current)")
    embed = discord.Embed(
        title="📈 ELO Gained This Month",
        description="\n".join(lines),
        color=0x2ecc71,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="ELO gained since start of month • Lichess Rapid")
    return embed, None

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=1)
async def live_leaderboard():
    data = load_data()
    for guild_id, guild_data in data.items():
        channel_id = guild_data.get("channel_id")
        if not channel_id:
            continue
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        embed, error = await build_leaderboard_embed(guild_data)
        if not embed:
            continue
        msg_id = guild_data.get("leaderboard_message_id")
        try:
            if msg_id:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
            else:
                msg = await channel.send(embed=embed)
                guild_data["leaderboard_message_id"] = str(msg.id)
                save_data(data)
        except discord.NotFound:
            msg = await channel.send(embed=embed)
            guild_data["leaderboard_message_id"] = str(msg.id)
            save_data(data)
        except Exception as e:
            print(f"Leaderboard update error: {e}")

@tasks.loop(hours=1)
async def monthly_reset_and_announce():
    now = datetime.now(timezone.utc)
    if now.day != 1 or now.hour != 0:
        return
    data = load_data()
    for guild_id, guild_data in data.items():
        channel_id = guild_data.get("channel_id")
        members = guild_data.get("members", {})
        if not members:
            continue

        is_first_month = (now.month == 6 and now.year == 2026)

        if not is_first_month and channel_id:
            channel = bot.get_channel(int(channel_id))
            if channel:
                gains = []
                async with aiohttp.ClientSession() as session:
                    for v in members.values():
                        username = v["username"]
                        start = v.get("start_rating")
                        current = await get_rating(session, username)
                        if start and current:
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
            for v in members.values():
                new_rating = await get_rating(session, v["username"])
                if new_rating:
                    v["start_rating"] = new_rating
        save_data(data)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    live_leaderboard.start()
    monthly_reset_and_announce.start()

@bot.command(name="setchannel")
@commands.has_permissions(manage_guild=True)
async def set_channel(ctx):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    guild_data["channel_id"] = str(ctx.channel.id)
    guild_data["leaderboard_message_id"] = None
    save_data(data)
    await ctx.send(f"✅ Leaderboard channel set to {ctx.channel.mention}!")

@bot.command(name="joinleaderboard")
async def join_leaderboard(ctx, lichess_username: str):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    members = guild_data["members"]
    username_lower = lichess_username.lower()
    if any(v["username"] == username_lower for v in members.values()):
        await ctx.send(f"⚠️ **{lichess_username}** is already on the leaderboard.")
        return
    async with aiohttp.ClientSession() as session:
        rating = await get_rating(session, username_lower)
        if rating is None:
            await ctx.send(f"❌ Could not find **{lichess_username}** on Lichess.")
            return
    members[str(ctx.author.id)] = {"username": username_lower, "start_rating": rating}
    save_data(data)
    await ctx.send(f"✅ {ctx.author.mention} joined as **{lichess_username}**! Starting rating: **{rating}**")

@bot.command(name="leaderboard")
async def show_leaderboard(ctx):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    async with ctx.typing():
        embed, error = await build_leaderboard_embed(guild_data, mode="rapid")
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ {error}")

@bot.command(name="blitzleaderboard")
async def show_blitz_leaderboard(ctx):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    async with ctx.typing():
        embed, error = await build_leaderboard_embed(guild_data, mode="blitz")
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ {error}")

@bot.command(name="bulletleaderboard")
async def show_bullet_leaderboard(ctx):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    async with ctx.typing():
        embed, error = await build_leaderboard_embed(guild_data, mode="bullet")
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ {error}")

@bot.command(name="gainleaderboard")
async def show_gain_leaderboard(ctx):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    async with ctx.typing():
        embed, error = await build_gain_embed(guild_data)
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ {error}")

@bot.command(name="members")
async def list_members(ctx):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    members = guild_data.get("members", {})
    if not members:
        await ctx.send("No members yet. Use `!joinleaderboard <lichess_username>` to join.")
        return
    names = "\n".join([f"• {v['username']}" for v in sorted(members.values(), key=lambda x: x['username'])])
    embed = discord.Embed(title="♟️ Registered Squad Members", description=names, color=0x1a1a2e)
    embed.set_footer(text=f"{len(members)} members total")
    await ctx.send(embed=embed)

@bot.command(name="addmember")
@commands.has_permissions(manage_messages=True)
async def add_member(ctx, lichess_username: str):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    members = guild_data["members"]
    username_lower = lichess_username.lower()
    if any(v["username"] == username_lower for v in members.values()):
        await ctx.send(f"⚠️ **{lichess_username}** is already on the leaderboard.")
        return
    async with aiohttp.ClientSession() as session:
        rating = await get_rating(session, username_lower)
        if rating is None:
            await ctx.send(f"❌ Could not find **{lichess_username}** on Lichess.")
            return
    members[username_lower] = {"username": username_lower, "start_rating": rating}
    save_data(data)
    await ctx.send(f"✅ **{lichess_username}** added! Starting rating: **{rating}**")

@bot.command(name="removemember")
@commands.has_permissions(manage_messages=True)
async def remove_member(ctx, lichess_username: str):
    data = load_data()
    guild_data = get_guild_data(data, ctx.guild.id)
    members = guild_data["members"]
    username_lower = lichess_username.lower()
    if not any(v["username"] == username_lower for v in members.values()):
        await ctx.send(f"❌ **{lichess_username}** is not on the leaderboard.")
        return
    guild_data["members"] = {k: v for k, v in members.items() if v["username"] != username_lower}
    save_data(data)
    await ctx.send(f"✅ **{lichess_username}** removed.")

@bot.command(name="chesshelp")
async def chess_help(ctx):
    embed = discord.Embed(title="♟️ Chess Squad Bot — Commands", color=0x4a90d9)
    embed.add_field(name="!setchannel",                 value="[Admin] Set leaderboard channel",  inline=False)
    embed.add_field(name="!joinleaderboard <username>", value="Add your Lichess account",          inline=False)
    embed.add_field(name="!leaderboard",                value="Show Rapid leaderboard",            inline=False)
    embed.add_field(name="!blitzleaderboard",           value="Show Blitz leaderboard",            inline=False)
    embed.add_field(name="!bulletleaderboard",          value="Show Bullet leaderboard",           inline=False)
    embed.add_field(name="!gainleaderboard",            value="Show ELO gained this month",        inline=False)
    embed.add_field(name="!members",                    value="List all registered members",       inline=False)
    embed.add_field(name="!addmember <username>",       value="[Admin] Add a member",              inline=False)
    embed.add_field(name="!removemember <username>",    value="[Admin] Remove a member",           inline=False)
    embed.set_footer(text="Live leaderboard updates every 10 min • Resets monthly • Lichess Rapid")
    await ctx.send(embed=embed)

@set_channel.error
@add_member.error
@remove_member.error
async def permission_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")

@join_leaderboard.error
@add_member.error
async def missing_arg_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Please provide a Lichess username. Example: `!joinleaderboard YourLichessName`")

if __name__ == "__main__":
    bot.run(TOKEN)
