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
ACTIVE_ROLE_ID = 1060759821856555119
OLD_ROLE_IDS = [1379573779839189022, 1266456229945937983]
PROTECTED_ROLE_IDS = [1279364611052802130, 1244606735780675657, 1060759139002896525, 1060755422006485075]  # роли, которые не трогаем
AFK_CHANNEL_NAME = "💤 | ᴀꜱᴋ"

MESSAGE_THRESHOLD = 50
VOICE_TIME_THRESHOLD = 250 * 3600

INACTIVE_MSG_THRESHOLD = 20
INACTIVE_VOICE_THRESHOLD = 20 * 3600
TIMER_DURATION = 15  # дней

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
        timer_start TEXT,
        prev_role_id INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

def update_timer(user_id):
    now = datetime.utcnow().isoformat()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    c.execute('UPDATE users SET timer_start = ? WHERE user_id = ?', (now, user_id))
    conn.commit()
    conn.close()

async def check_role(member):
    if not member:
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time, timer_start, prev_role_id FROM users WHERE user_id = ?', (member.id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return

    messages, voice_time, timer_start, prev_role_id = row
    active_role = member.guild.get_role(ACTIVE_ROLE_ID)
    old_roles = [member.guild.get_role(rid) for rid in OLD_ROLE_IDS]
    has_active = active_role in member.roles if active_role else False

    # Если есть защищённые роли — выводим сообщение и не трогаем
    if any(r.id in PROTECTED_ROLE_IDS for r in member.roles):
        return

    if messages >= MESSAGE_THRESHOLD and voice_time >= VOICE_TIME_THRESHOLD:
        if active_role and not has_active:
            try:
                for r in old_roles:
                    if r in member.roles:
                        prev_role_id = r.id
                        await member.remove_roles(r)
                await member.add_roles(active_role)
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('UPDATE users SET prev_role_id = ? WHERE user_id = ?', (prev_role_id, member.id))
                conn.commit()
                conn.close()
                update_timer(member.id)
            except Exception as e:
                print(f"Ошибка при выдаче роли: {e}")
        elif has_active and not timer_start:
            update_timer(member.id)
    elif has_active and not timer_start:
        update_timer(member.id)

@tasks.loop(hours=24)
async def check_all_users():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, messages, voice_time, timer_start, prev_role_id FROM users WHERE timer_start IS NOT NULL')
    rows = c.fetchall()
    conn.close()

    now = datetime.utcnow()
    if not bot.guilds:
        return
    guild = bot.guilds[0]
    for user_id, msg, voice, t_start, prev_role_id in rows:
        try:
            member = guild.get_member(user_id)
            if not member or any(r.id in PROTECTED_ROLE_IDS for r in member.roles):
                continue
            t_start = datetime.fromisoformat(t_start)
            if now - t_start >= timedelta(days=TIMER_DURATION):
                if msg < INACTIVE_MSG_THRESHOLD or voice < INACTIVE_VOICE_THRESHOLD:
                    role = guild.get_role(ACTIVE_ROLE_ID)
                    if role in member.roles:
                        await member.remove_roles(role)
                        if prev_role_id:
                            old_role = guild.get_role(prev_role_id)
                            if old_role:
                                await member.add_roles(old_role)
        except Exception as e:
            print(f"Ошибка таймера: {e}")

async def track_voice_time(member):
    try:
        while member.voice and member.voice.channel and member.voice.channel.name != AFK_CHANNEL_NAME:
            await asyncio.sleep(60)
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (member.id,))
            c.execute('UPDATE users SET voice_time = voice_time + 60 WHERE user_id = ?', (member.id,))
            conn.commit()
            conn.close()
            await check_role(member)
            member = member.guild.get_member(member.id)  # Обновляем статус
            if member is None or member.voice is None:
                break
    except Exception as e:
        print(f"Ошибка в track_voice_time: {e}")

@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    check_all_users.start()
    # Запускаем отслеживание голосового времени для тех, кто уже в голосе
    if bot.guilds:
        guild = bot.guilds[0]
        for member in guild.voice_states:
            mem = guild.get_member(member)
            if mem and mem.voice and mem.voice.channel and mem.voice.channel.name != AFK_CHANNEL_NAME:
                bot.loop.create_task(track_voice_time(mem))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Фильтр сообщений:
    # регаем сообщения только если:
    # - длинна текста >= 3 символов
    # - ИЛИ есть стикеры, картинки, гифки (attachments или stickers)
    # - И НЕ команды !stats, !top, !check
    content = message.content.strip()
    if content.startswith('!'):
        if content.lower().startswith(('!stats', '!top', '!check')):
            # команды обрабатываем отдельно
            pass
        else:
            # учитываем как сообщение, если не команда из списка
            if len(content) < 3:
                return
    else:
        if len(content) < 3 and not message.attachments and not message.stickers:
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
    if (before.channel is None and after.channel) or (before.channel != after.channel):
        # Начинаем считать время
        if after.channel and after.channel.name != AFK_CHANNEL_NAME:
            bot.loop.create_task(track_voice_time(member))

@bot.command()
async def stats(ctx):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time FROM users WHERE user_id = ?', (ctx.author.id,))
    row = c.fetchone()
    conn.close()
    if row:
        msg, voice = row
        hours = voice // 3600
        minutes = (voice % 3600) // 60
        await ctx.send(f"{ctx.author.display_name}, у тебя {msg} сообщений и {hours} ч {minutes} мин в голосовых.")
    else:
        await ctx.send("Данных нет.")

@bot.command()
async def check(ctx, member: discord.Member = None):
    member = member or ctx.author

    if any(r.id in PROTECTED_ROLE_IDS for r in member.roles):
        await ctx.send(f"{member.display_name}, ты крутой, сиди и дальше чухай жопу.")
        return

    await check_role(member)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time, timer_start FROM users WHERE user_id = ?', (member.id,))
    row = c.fetchone()
    conn.close()

    if not row:
        await ctx.send(f"Нет данных по {member.display_name}.")
        return

    msg, voice, t_start = row
    has_role = discord.utils.get(member.roles, id=ACTIVE_ROLE_ID)

    response = f"📊 Статистика {member.display_name}:\n"
    response += f"— {msg} сообщений\n— {voice // 3600} ч {(voice % 3600) // 60} мин в голосовых\n"

    if has_role:
        if t_start:
            delta = datetime.utcnow() - datetime.fromisoformat(t_start)
            days_left = max(0, TIMER_DURATION - delta.days)
            response += f"— До снятия роли: {days_left} дней\n"
            response += f"— Нужно набрать: 20 сообщений и 20 часов в голосе за период"
        else:
            response += "— Роль активна, но таймер не запущен."
    else:
        response += "— Роль за активность не получена, таймер не запущен."

    await ctx.send(response)

@bot.command()
async def top(ctx):
    conn = get_db_connection()
    c = conn.cursor()
    # Оценка = сообщения + (минуты в голосе * 3)
    c.execute('SELECT user_id, messages, voice_time FROM users ORDER BY (messages + (voice_time / 60) * 3) DESC LIMIT 5')
    rows = c.fetchall()
    conn.close()

    if not rows:
        await ctx.send("Нет данных для топа.")
        return

    msg = "**🏆 Топ пользователей:**\n"
    for i, (uid, msg_count, voice_time) in enumerate(rows, 1):
        user = ctx.guild.get_member(uid)
        if user:
            score = msg_count + (voice_time // 60) * 3
            hours = voice_time // 3600
            minutes = (voice_time % 3600) // 60
            msg += f"{i}. {user.display_name} — {msg_count} сообщений, {hours} ч {minutes} мин в голосовых (оценка: {score}),\n"
    await ctx.send(msg)

bot.run(token)
