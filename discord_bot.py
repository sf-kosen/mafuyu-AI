# Mafuyu Discord Bot
# Discordで真冬とチャット

import discord
from discord.ext import commands, tasks
import asyncio
import sys

# WindowsでのEvent Loopエラー回避
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from mafuyu import MafuyuSession

# Bot設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# 各サーバー/ユーザーごとのセッション (サーバー単位で共有)
sessions: dict[int, MafuyuSession] = {}
last_channel_id = None
last_user_name = None
last_message_time = None
from datetime import datetime, timedelta

def get_session(guild_id: int = None, user_id: int = None) -> MafuyuSession:
    """サーバーごとのセッションを取得。DMの場合はユーザーIDを使用。"""
    # DMの場合はuser_idを使う、サーバーの場合はguild_idを使う
    session_key = guild_id if guild_id else user_id
    if session_key not in sessions:
        sessions[session_key] = MafuyuSession()
    return sessions[session_key]

def strip_bot_mention(content: str) -> str:
    """Botメンションを取り除いたメッセージを返す。"""
    if not bot.user:
        return content.strip()
    mention = f"<@{bot.user.id}>"
    mention_nick = f"<@!{bot.user.id}>"
    return content.replace(mention, "").replace(mention_nick, "").strip()

async def run_session_response(session: MafuyuSession, content: str, user_name: str, message_obj: discord.Message) -> str:
    """同期処理を別スレッドで実行して応答を返す。進捗コールバック付き。"""
    loop = asyncio.get_running_loop()
    
    # Placeholder for status updates (optional, or just typing)
    # Since we can't easily edit the user's message, 
    # and we don't want to spam messages, we will assume 'typing' context is enough for now,
    # OR we can send a temporary message.
    
    temp_msg = None
    
    def progress_callback(status: str):
         # Run async send in thread-safe way? 
         # run_in_executor runs in a thread, so we can't await here directly.
         # Ideally we schedule a coroutine to update a message.
         # For simplicity in this iteration, let's just print to console to verify flow,
         # or try to run_coroutine_threadsafe if we had a message object.
         print(f"[Callback] {status}")
         if temp_msg:
             asyncio.run_coroutine_threadsafe(temp_msg.edit(content=f"{status}"), loop)

    # Note: Actual message editing requires creating the message first.
    # Let's keep it simple: Just pass the callback.
    
    return await loop.run_in_executor(None, session.respond, content, user_name, progress_callback)


@bot.event
async def on_ready():
    print(f'=== Mafuyu Bot Online ===')
    print(f'Logged in as: {bot.user}')
    
    # Launch Codex Bridge in a new window automatically
    bridge_script = "codex_bridge.py"
    # start "Title" python script
    # This creates a NEW window.
    try:
        import subprocess
        print(f"[AutoLaunch] Starting {bridge_script} in new window...")
        subprocess.Popen(f'start "Codex Bridge (Mafuyu)" python {bridge_script}', shell=True)
    except Exception as e:
        print(f"[AutoLaunch] Failed to start bridge: {e}")

    if not auto_talk_loop.is_running():
        auto_talk_loop.start()

@tasks.loop(minutes=20.0)
async def auto_talk_loop():
    """自律発話ループ"""
    global last_channel_id, last_user_name, last_message_time
    if not last_channel_id or not last_message_time:
        return
        
    # 前回の会話から時間が経っていないならスキップ (例: 1時間以内は黙る)
    if datetime.now() - last_message_time < timedelta(minutes=60):
        return

    # 夜間（0時〜7時）は静かにする (Night Mode)
    current_hour = datetime.now().hour
    if 0 <= current_hour < 7:
        return

    channel = bot.get_channel(last_channel_id)
    if not channel:
        return

    session = get_session(last_channel_id)
    
    # Check if we should speak (random chance + thought)
    # Use run_in_executor to avoid blocking
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, session.initiate_talk, last_user_name)
    
    if response:
        print(f"[AutoTalk] Speaking to channel {last_channel_id}")
        async with channel.typing():
            await asyncio.sleep(3) # Pausing for dramatic effect
            # 発言したら時間を更新して、連投を防ぐ
            last_message_time = datetime.now()
            await channel.send(response)


@bot.event
async def on_message(message):
    # Botのメッセージは無視
    if message.author.bot:
        return

    # コマンド処理
    await bot.process_commands(message)
    
    is_dm = message.guild is None

    # 【セキュリティ】
    # - DM: 特定のユーザーのみ許可 (他は無視)
    # - Server: 全員許可
    ALLOWED_USER = 'mikan.1111'
    if is_dm and message.author.name != ALLOWED_USER:
        return
    
    # 【重要】自律発話のターゲット更新
    # ユーザーの要望: 自律的に話しかけるのは「DMのみ」
    # サーバーで会話しても、自律発話のターゲット（last_channel_id）は書き換えない
    if is_dm and message.author.name == ALLOWED_USER:
        global last_channel_id, last_user_name, last_message_time
        last_channel_id = message.channel.id
        # DMの場合はGlobal Nameがない場合がある
        last_user_name = message.author.global_name or message.author.name
        last_message_time = datetime.now()
    
    # 直接メンションのみ反応（@here, @everyone は無視）
    is_mention = bot.user and bot.user.id in [m.id for m in message.mentions]
    
    # 【Free Chat Channel】メンション不要で自由に話せるチャンネル
    FREE_CHAT_CHANNELS = [1458301980131721410]
    is_free_chat = message.channel.id in FREE_CHAT_CHANNELS
    
    # 【Role-Based Access】開発者ロール以外はサーバーでメンションできない
    ALLOWED_ROLE_ID = 1453967404307845232
    if not is_dm:
        has_allowed_role = any(role.id == ALLOWED_ROLE_ID for role in message.author.roles)
        if not has_allowed_role and not is_free_chat:
            return  # 権限なし
    
    if not is_dm and not is_mention and not is_free_chat:
        return

    # メンションを除去してメッセージ取得
    content = strip_bot_mention(message.content)
    if not content:
        content = 'やっほー'
    
    # 【New】引用(Reply)の文脈を抽出
    if message.reference and message.reference.message_id:
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if ref_msg:
                ref_content = ref_msg.content[:500]  # 長すぎる場合は切り詰め
                ref_author = ref_msg.author.display_name
                content = f"[引用: {ref_author}の発言「{ref_content}」について]\n{content}"
        except:
            pass  # 取得失敗時は無視
    
    # 入力中表示
    async with message.channel.typing():
        # セッション取得 (サーバー単位 or DM単位)
        guild_id = message.guild.id if message.guild else None
        user_id = message.author.id
        session = get_session(guild_id=guild_id, user_id=user_id)
        # ユーザー識別
        user_name = message.author.global_name or message.author.name
        response = await run_session_response(session, content, user_name, message)
        # 返信後も時間を更新
        last_message_time = datetime.now()
    
    # 返信
    await message.reply(response, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name='clear')
async def clear_history(ctx):
    """セッションをクリア"""
    session = get_session(ctx.channel.id)
    session.clear_history()
    await ctx.reply('履歴クリアしたよ！', allowed_mentions=discord.AllowedMentions.none())


@bot.command(name='mafuyu')
async def talk_to_mafuyu(ctx, *, message: str = None):
    """真冬に話しかける"""
    if not message:
        await ctx.reply('なに？話しかけてよ、オタク君！', allowed_mentions=discord.AllowedMentions.none())
        return
    
    async with ctx.channel.typing():
        session = get_session(ctx.channel.id)
        # ユーザー識別
        user_name = ctx.author.global_name or ctx.author.name
        response = await run_session_response(session, message, user_name, ctx.message)
    
    await ctx.reply(response, allowed_mentions=discord.AllowedMentions.none())


if __name__ == '__main__':
    import os
    
    # トークンは環境変数または.envから
    token = os.environ.get('DISCORD_TOKEN')
    
    if not token:
        # .envファイルから読み込み
        env_path = 'discord.env'
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.startswith('DISCORD_TOKEN='):
                        token = line.strip().split('=', 1)[1]
                        break
    
    if not token:
        print('='*50)
        print('DISCORD_TOKEN が設定されていません！')
        print()
        print('1. Discord Developer Portal でBot作成')
        print('   https://discord.com/developers/applications')
        print()
        print('2. discord.env ファイルを作成:')
        print('   DISCORD_TOKEN=your_bot_token_here')
        print('='*50)
    else:
        bot.run(token)
