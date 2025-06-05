import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta
import asyncio
import os
from dotenv import load_dotenv
from threading import Thread
from flask import Flask

# Load token
load_dotenv()
token = os.getenv("DISCORD_TOKEN")

# Flask server (for keep-alive)
app = Flask('')
@app.route('/')
def home():
    return "Бот работает!"
def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
Thread(target=run).start()

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# DB and constants
DB_NAME = 'user_stats.db'
ROLE_TO_GIVE_ID = 1060759821856555119
OLD_ROLE_IDS = [1060770606716567632, 1379573779839189022]
PROTECTED_ROLE_IDS = [1279364611052802130, 1244606735780675657, 1060759139002896525, 1060755422006485075]
AFK_CHANNEL_NAME = "💤 | ᴀꜱᴋ"

MESSAGE_THRESHOLD = 50
VOICE_TIME_THRESHOLD = 250 * 3600  # seconds

# Timer check thresholds
INACTIVE_MSG_THRESHOLD = 20
INACTIVE_VOICE_THRESHOLD = 60 * 3600
TIMER_DURATION = 30  # days

# DB Setup
def get_db_connection():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        messages INTEGER DEFAULT 0,
        voice_time INTEGER DEFAULT 0,
        timer_start TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# Timer update
def update_timer(user_id):
    now = datetime.utcnow().isoformat()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    c.execute('UPDATE users SET timer_start = ? WHERE user_id = ?', (now, user_id))
    conn.commit()
    conn.close()

# Check & manage role with timer logic
async def check_role(member):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time, timer_start FROM users WHERE user_id = ?', (member.id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return
    messages, voice_time, timer_start = row
    role = member.guild.get_role(ROLE_TO_GIVE_ID)
    old_roles = [member.guild.get_role(rid) for rid in OLD_ROLE_IDS]

    has_role = role in member.roles if role else False

    # Если у пользователя достаточно сообщений и голоса, но нет роли — выдать и обновить таймер
    if messages >= MESSAGE_THRESHOLD and voice_time >= VOICE_TIME_THRESHOLD:
        if role and not has_role:
            try:
                await member.remove_roles(*[r for r in old_roles if r in member.roles])
                await member.add_roles(role)
                update_timer(member.id)
            except Exception as e:
                print(e)
        # Если роль уже есть, и таймера нет, установить таймер
        elif has_role and not timer_start:
            update_timer(member.id)

# Check timers (каждый день)
@tasks.loop(hours=24)
async def check_all_users():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, messages, voice_time, timer_start FROM users WHERE timer_start IS NOT NULL')
    rows = c.fetchall()
    conn.close()

    now = datetime.utcnow()
    guild = discord.utils.get(bot.guilds)
    for user_id, msg, voice, t_start in rows:
        try:
            member = guild.get_member(user_id)
            if not member or any(r.id in PROTECTED_ROLE_IDS for r in member.roles):
                continue
            t_start = datetime.fromisoformat(t_start)
            if now - t_start >= timedelta(days=TIMER_DURATION):
                if msg < INACTIVE_MSG_THRESHOLD or voice < INACTIVE_VOICE_THRESHOLD:
                    role = guild.get_role(ROLE_TO_GIVE_ID)
                    if role in member.roles:
                        await member.remove_roles(role)
                        for rid in OLD_ROLE_IDS:
                            old = guild.get_role(rid)
                            if old:
                                await member.add_roles(old)
        except Exception as e:
            print(f"Ошибка таймера: {e}")

# Track voice time
async def track_voice_time(member):
    while member.voice and member.voice.channel and member.voice.channel.name != AFK_CHANNEL_NAME:
        await asyncio.sleep(60)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (member.id,))
        c.execute('UPDATE users SET voice_time = voice_time + 60 WHERE user_id = ?', (member.id,))
        conn.commit()
        conn.close()
        await check_role(member)

# Events
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    check_all_users.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (message.author.id,))
    c.execute('UPDATE users SET messages = messages + 1 WHERE user_id = ?', (message.author.id,))
    conn.commit()
    conn.close()
    await check_role(message.author)
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if before.channel is None and after.channel:
        bot.loop.create_task(track_voice_time(member))

# Commands
@bot.command()
async def stats(ctx):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time FROM users WHERE user_id = ?', (ctx.author.id,))
    row = c.fetchone()
    conn.close()
    if row:
        msg, voice = row
        await ctx.send(f"{ctx.author.mention}, у тебя {msg} сообщений и {voice // 3600} ч {(voice % 3600) // 60} мин в голосовых.")
    else:
        await ctx.send("Данных нет.")

@bot.command()
async def top(ctx):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, messages, voice_time FROM users ORDER BY (messages + voice_time / 60) DESC LIMIT 5')
    rows = c.fetchall()
    conn.close()
    if not rows:
        await ctx.send("Нет данных для топа.")
        return
    msg = "**Топ пользователей:**\n"
    for i, (uid, msg_count, voice_time) in enumerate(rows, 1):
        user = ctx.guild.get_member(uid)
        if user:
            msg += f"{i}. {user.display_name} — {msg_count} сообщений, {voice_time // 3600} ч {(voice_time % 3600) // 60} мин в голосовых\n"
    await ctx.send(msg)

@bot.command()
async def check(ctx):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time, timer_start FROM users WHERE user_id = ?', (ctx.author.id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await ctx.send("Нет данных по тебе.")
        return
    msg, voice, t_start = row
    response = f"Ты написал {msg} сообщений и провел {voice // 3600} ч в голосовых.\n"
    if t_start:
        delta = datetime.utcnow() - datetime.fromisoformat(t_start)
        days_left = max(0, 30 - delta.days)
        response += f"До снятия роли осталось {days_left} дней."
    else:
        response += "Таймер не запущен."
    await ctx.send(response)

bot.run(token)
