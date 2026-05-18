import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, time

TOKEN              = os.getenv("DISCORD_TOKEN")
LEADERBOARD_CHANNEL_ID = int(os.getenv("LEADERBOARD_CHANNEL_ID", "0"))
POST_HOUR          = int(os.getenv("POST_HOUR", "20"))
POST_MINUTE        = int(os.getenv("POST_MINUTE", "0"))
MEMBERS_FILE       = "members.json"

def load_members():
    if os.path.exists(MEMBERS_FILE):
        with open(MEMBERS_FILE) as f:
            return json.load(f)
    return {}

def save_members(members):
    with open(MEMBERS_FILE, "w") as f:
        json.dump(members, f, indent=2)

async def fetch_rapid_ratings(usernames):
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
                        rapid_rating = user.get("perfs", {}).get("rapid", {}).get("rating", None)
                        results.append({
                            "username": user["username"],
                            "rating": rapid_rating if rapid_rating else "Unrated"
                        })
        except Exception as e:
            print(f"Lichess API error: {e}")
    results.sort(key=lambda x: x["rating"] if isinstance(x["rating"], int) else -1, reverse=True)
    return results

async def build_leaderboard_embed(members):
    if not members:
        return None, "No members registered yet."
    usernames = list(members.values())
    ratings = await fetch_rapid_ratings(usernames)
    if not ratings:
        return None, "Could not fetch ratings from Lichess. Try again shortly."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, entry in enumerate(ratings):
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        rating_display = str(entry["rating"]) if entry["rating"] != "Unrated" else "—"
        lines.append(f"{rank} **{entry['username']}** — {rating_display}")
    embed = discord.Embed(
        title="♟️ Anime Soul Chess Squad — Leaderboard",
        description="\n".join(lines),
        color=0x4a90d9,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="Lichess Rapid Rating  •  Updates daily")
    return embed, None

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(time=time(hour=POST_HOUR, minute=POST_MINUTE))
async def daily_leaderboard():
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        return
    members = load_members()
    embed, error = await build_leaderboard_embed(members)
    if embed:
        await channel.send(embed=embed)
    else:
        await channel.send(f"⚠️ {error}")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    daily_leaderboard.start()

@bot.command(name="joinleaderboard")
async def join_leaderboard(ctx, lichess_username: str):
    members = load_members()
    username_lower = lichess_username.lower()
    if username_lower in members.values():
        await ctx.send(f"⚠️ **{lichess_username}** is already on the leaderboard.")
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://lichess.org/api/user/{username_lower}") as resp:
            if resp.status != 200:
                await ctx.send(f"❌ Could not find **{lichess_username}** on Lichess. Check the username and try again.")
                return
    members[str(ctx.author.id)] = username_lower
    save_members(members)
    await ctx.send(f"✅ {ctx.author.mention} joined the leaderboard as **{lichess_username}**!")

@bot.command(name="leaderboard")
async def show_leaderboard(ctx):
    members = load_members()
    async with ctx.typing():
        embed, error = await build_leaderboard_embed(members)
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ {error}")

@bot.command(name="members")
async def list_members(ctx):
    members = load_members()
    if not members:
        await ctx.send("No members yet. Use `!joinleaderboard <lichess_username>` to join.")
        return
    names = "\n".join([f"• {v}" for v in sorted(members.values())])
    embed = discord.Embed(title="♟️ Registered Squad Members", description=names, color=0x1a1a2e)
    embed.set_footer(text=f"{len(members)} members total")
    await ctx.send(embed=embed)

@bot.command(name="addmember")
@commands.has_permissions(manage_messages=True)
async def add_member(ctx, lichess_username: str):
    members = load_members()
    username_lower = lichess_username.lower()
    if username_lower in members.values():
        await ctx.send(f"⚠️ **{lichess_username}** is already on the leaderboard.")
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://lichess.org/api/user/{username_lower}") as resp:
            if resp.status != 200:
                await ctx.send(f"❌ Could not find **{lichess_username}** on Lichess.")
                return
    members[username_lower] = username_lower
    save_members(members)
    await ctx.send(f"✅ **{lichess_username}** added to the leaderboard.")

@bot.command(name="removemember")
@commands.has_permissions(manage_messages=True)
async def remove_member(ctx, lichess_username: str):
    members = load_members()
    username_lower = lichess_username.lower()
    if username_lower not in members.values():
        await ctx.send(f"❌ **{lichess_username}** is not on the leaderboard.")
        return
    members = {k: v for k, v in members.items() if v != username_lower}
    save_members(members)
    await ctx.send(f"✅ **{lichess_username}** removed from the leaderboard.")

@bot.command(name="chesshelp")
async def chess_help(ctx):
    embed = discord.Embed(title="♟️ Chess Squad Bot — Commands", color=0x4a90d9)
    embed.add_field(name="!joinleaderboard <username>", value="Add your Lichess account",         inline=False)
    embed.add_field(name="!leaderboard",                value="Show current leaderboard",          inline=False)
    embed.add_field(name="!members",                    value="List all registered members",       inline=False)
    embed.add_field(name="!addmember <username>",       value="[Squad Lead] Add a member",         inline=False)
    embed.add_field(name="!removemember <username>",    value="[Squad Lead] Remove a member",      inline=False)
    embed.set_footer(text="Leaderboard auto-posts daily • Lichess Rapid Rating")
    await ctx.send(embed=embed)

@add_member.error
@remove_member.error
async def permission_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Only the squad lead can use this command.")

if __name__ == "__main__":
    bot.run(TOKEN)