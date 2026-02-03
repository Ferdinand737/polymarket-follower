import discord
from discord.ext import commands
from utils.utils import *
from utils.logger import Logger, Whomst

logger = Logger(Whomst.DISCORD_BOT)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

ALLOWED_USER_ID = 273300302541881344


@bot.check
async def check_user_id(ctx):
    return ctx.author.id == ALLOWED_USER_ID


@bot.event
async def on_ready():
    logger.log(f"Logged in as {bot.user.name}")
    logger.log(f"Bot ID: {bot.user.id}")


@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("Pong!")


@bot.command(name="status")
async def status(ctx):
    await ctx.send("Polymarket Follower Bot is running!")


@bot.command(name="get_address")
async def get_address(ctx):
    address, error = get_follow_address()
    if error:
        await ctx.send(error)
    else:
        await ctx.send(f"Currently following address: {address}")


@bot.command(name="set_address")
async def set_address(ctx, *, address: str):
    if not address:
        await ctx.send("Please provide an address. Usage: !set_address <address>")
        return
    
    try:
        set_follow_address(address)
        await ctx.send(f"Follow address set to: {address.lower().strip()}")
    except ValueError as e:
        await ctx.send(f"Error: {e}")


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is not set")
    
    bot.run(DISCORD_BOT_TOKEN)


def main():
    if not DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is not set")
    
    bot.run(DISCORD_BOT_TOKEN)