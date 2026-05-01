import asyncio
import os
import sys
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

from config import (
    ALLOWED_ROLE_IDS,
    DISCORD_ALLOWED_USER_ID,
    ENABLE_CODEX_BRIDGE_AUTOSTART,
    FREE_CHAT_CHANNELS,
)
from mafuyu import MafuyuSession


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

sessions: dict[int, MafuyuSession] = {}
last_channel_id = None
last_user_name = None
last_message_time = None


def is_allowed_user(author) -> bool:
    return DISCORD_ALLOWED_USER_ID > 0 and author.id == DISCORD_ALLOWED_USER_ID


def user_has_allowed_role(member) -> bool:
    roles = getattr(member, "roles", [])
    return any(role.id in ALLOWED_ROLE_IDS for role in roles)


def can_use_tools_in_context(is_dm: bool, author) -> bool:
    if is_dm:
        return is_allowed_user(author)
    return user_has_allowed_role(author)


def can_chat_in_context(is_dm: bool, author, channel_id: int) -> bool:
    if is_dm:
        return is_allowed_user(author)
    return user_has_allowed_role(author) or channel_id in FREE_CHAT_CHANNELS


def command_tools_allowed(ctx) -> bool:
    return can_use_tools_in_context(ctx.guild is None, ctx.author)


@bot.check
async def restrict_privileged_commands(ctx):
    if ctx.command and ctx.command.name in {"clear", "mafuyu"}:
        if ctx.command.name == "clear":
            return can_chat_in_context(ctx.guild is None, ctx.author, ctx.channel.id)
        return command_tools_allowed(ctx)
    return True


def get_session(guild_id: int = None, user_id: int = None) -> MafuyuSession:
    session_key = guild_id if guild_id else user_id
    if session_key not in sessions:
        sessions[session_key] = MafuyuSession()
    return sessions[session_key]


def strip_bot_mention(content: str) -> str:
    if not bot.user:
        return content.strip()
    mention = f"<@{bot.user.id}>"
    mention_nick = f"<@!{bot.user.id}>"
    return content.replace(mention, "").replace(mention_nick, "").strip()


async def run_session_response(
    session: MafuyuSession,
    content: str,
    user_name: str,
    message_obj: discord.Message,
    allow_tools: bool,
    *,
    is_dm: bool,
    is_owner: bool,
    has_allowed_role: bool,
) -> str:
    loop = asyncio.get_running_loop()

    def progress_callback(status: str):
        print(f"[Callback] {status}")

    return await loop.run_in_executor(
        None,
        lambda: session.respond(
            content,
            user_name,
            progress_callback,
            allow_tools,
            is_dm=is_dm,
            is_owner=is_owner,
            has_allowed_role=has_allowed_role,
        ),
    )


@bot.event
async def on_ready():
    print("=== Mafuyu Bot Online ===")
    print(f"Logged in as: {bot.user}")
    if DISCORD_ALLOWED_USER_ID <= 0:
        print("[Config] DISCORD_ALLOWED_USER_ID is not set. DM access is disabled.")

    if ENABLE_CODEX_BRIDGE_AUTOSTART:
        bridge_script = "codex_bridge.py"
        try:
            import subprocess
            print(f"[AutoLaunch] Starting {bridge_script} in new window...")
            subprocess.Popen(f'start "Codex Bridge (Mafuyu)" python {bridge_script}', shell=True)
        except Exception as e:
            print(f"[AutoLaunch] Failed to start bridge: {e}")

    if not auto_talk_loop.is_running():
        auto_talk_loop.start()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply("You are not allowed to use this command here.", allowed_mentions=discord.AllowedMentions.none())
        return
    raise error


@tasks.loop(minutes=20.0)
async def auto_talk_loop():
    global last_channel_id, last_message_time
    if not last_channel_id or not last_message_time:
        return
    if datetime.now() - last_message_time < timedelta(minutes=60):
        return
    if 0 <= datetime.now().hour < 7:
        return

    channel = bot.get_channel(last_channel_id)
    if not channel:
        return

    session = get_session(user_id=last_channel_id)
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, session.initiate_talk, last_user_name)

    if response:
        async with channel.typing():
            await asyncio.sleep(3)
            last_message_time = datetime.now()
            await channel.send(response)


@bot.event
async def on_message(message):
    global last_channel_id, last_user_name, last_message_time

    if message.author.bot:
        return

    await bot.process_commands(message)

    is_dm = message.guild is None
    if is_dm and not is_allowed_user(message.author):
        return

    if is_dm and is_allowed_user(message.author):
        last_channel_id = message.channel.id
        last_user_name = message.author.global_name or message.author.name
        last_message_time = datetime.now()

    is_mention = bot.user and bot.user.id in [m.id for m in message.mentions]
    is_free_chat = message.channel.id in FREE_CHAT_CHANNELS
    has_allowed_role = False if is_dm else user_has_allowed_role(message.author)

    if not can_chat_in_context(is_dm, message.author, message.channel.id):
        return
    if not is_dm and not is_mention and not is_free_chat:
        return

    content = strip_bot_mention(message.content) or "やっほー"

    if message.reference and message.reference.message_id:
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if ref_msg:
                ref_content = ref_msg.content[:500]
                ref_author = ref_msg.author.display_name
                content = (
                    f"[UNTRUSTED_DISCORD_QUOTE author={ref_author!r}]\n"
                    f"{ref_content}\n"
                    "[/UNTRUSTED_DISCORD_QUOTE]\n\n"
                    f"[USER_MESSAGE]\n{content}\n[/USER_MESSAGE]"
                )
        except Exception:
            pass

    async with message.channel.typing():
        session = get_session(
            guild_id=message.guild.id if message.guild else None,
            user_id=message.author.id,
        )
        user_name = message.author.global_name or message.author.name
        allow_tools = is_dm or has_allowed_role
        response = await run_session_response(
            session,
            content,
            user_name,
            message,
            allow_tools,
            is_dm=is_dm,
            is_owner=is_allowed_user(message.author),
            has_allowed_role=has_allowed_role,
        )
        last_message_time = datetime.now()

    await message.reply(response, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="clear")
async def clear_history(ctx):
    session = get_session(
        guild_id=ctx.guild.id if ctx.guild else None,
        user_id=ctx.author.id,
    )
    session.clear_history()
    await ctx.reply("履歴クリアしたよ！", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="mafuyu")
async def talk_to_mafuyu(ctx, *, message: str = None):
    if not message:
        await ctx.reply("なに？話しかけてよ。", allowed_mentions=discord.AllowedMentions.none())
        return

    async with ctx.channel.typing():
        session = get_session(
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
        )
        user_name = ctx.author.global_name or ctx.author.name
        allow_tools = command_tools_allowed(ctx)
        response = await run_session_response(
            session,
            message,
            user_name,
            ctx.message,
            allow_tools,
            is_dm=ctx.guild is None,
            is_owner=is_allowed_user(ctx.author),
            has_allowed_role=False if ctx.guild is None else user_has_allowed_role(ctx.author),
        )

    await ctx.reply(response, allowed_mentions=discord.AllowedMentions.none())


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        env_path = "discord.env"
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("DISCORD_TOKEN="):
                        token = line.strip().split("=", 1)[1]
                        break

    if not token:
        print("=" * 50)
        print("DISCORD_TOKEN is not set.")
        print("Set DISCORD_TOKEN in the environment or discord.env.")
        print("=" * 50)
    else:
        bot.run(token)
