import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta
import asyncio
import os
from dotenv import load_dotenv
from threading import Thread
from flask import Flask, request, abort

# Load token
load_dotenv()
token = os.getenv("DISCORD_TOKEN")
# Переменная с названием базы данных (если ещё нет)
DB_NAME = 'user_stats.db'  # если у тебя в другом месте, не меняй

def log_activity(text):
    os.makedirs("logs", exist_ok=True)  # Создаёт папку logs, если нет
    with open("logs/activity.log", "a", encoding="utf-8") as f:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp} UTC] {text}\n")


app = Flask('')

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def check_auth():
    password = request.args.get('password')
    if password != os.getenv("ADMIN_PASSWORD"):
        from flask import abort
        abort(403, description="Доступ запрещён")

@app.route('/')
def home():
    return "Бот работает!"

@app.route('/logs')
def view_logs():
    check_auth()
    try:
        with open("logs/activity.log", "r", encoding='utf-8') as f:
            lines = f.readlines()
        lines = lines[-100:]  # последние 100 строк
        content = "<br>".join(line.strip() for line in lines)
        return f"<h2>Логи активности</h2><div style='font-family: monospace;'>{content}</div>"
    except Exception as e:
        return f"<p>Ошибка при чтении логов: {e}</p>"

@app.route('/admin/users')
def admin_users():
    check_auth()
    conn = get_db_connection()
    users = conn.execute('SELECT user_id, messages, voice_time FROM users ORDER BY messages + voice_time DESC').fetchall()
    conn.close()

    html = "<h2>Пользователи</h2>"
    html += "<table border=1 cellpadding=5><tr><th>ID</th><th>Сообщений</th><th>Войс, ч</th></tr>"
    for user in users:
        voice_hours = user['voice_time'] // 3600
        html += f"<tr><td><a href='/admin/user/{user['user_id']}?password={request.args.get('password')}'>{user['user_id']}</a></td><td>{user['messages']}</td><td>{voice_hours}</td></tr>"
    html += "</table>"
    return html

@app.route('/admin/user/<int:user_id>')
def admin_user_detail(user_id):
    check_auth()
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()

    if not user:
        return f"Пользователь с ID {user_id} не найден", 404

    voice_hours = user['voice_time'] // 3600
    voice_minutes = (user['voice_time'] % 3600) // 60

    html = f"<h2>Детальная статистика пользователя {user_id}</h2>"
    html += f"<ul>"
    html += f"<li>Сообщений: {user['messages']}</li>"
    html += f"<li>Время в голосе: {voice_hours} ч {voice_minutes} мин</li>"
    html += f"<li>Таймер старт: {user['timer_start']}</li>"
    html += f"<li>Предыдущая роль: {user['prev_role_id']}</li>"
    html += "</ul>"
    html += f"<a href='/admin/users?password={request.args.get('password')}'>← Назад к списку</a>"
    return html

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
PROTECTED_ROLE_IDS = [1279364611052802130, 1244606735780675657, 1060759139002896525, 1060755422006485075]
AFK_CHANNEL_NAME = "💤 | ᴀꜱᴋ"

MESSAGE_THRESHOLD = 50
VOICE_TIME_THRESHOLD = 250 * 3600

INACTIVE_VOICE_THRESHOLD = 20 * 3600
TIMER_DURATION = 15

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
                print(e)
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
    guild = discord.utils.get(bot.guilds)
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
    while member.voice and member.voice.channel and member.voice.channel.name != AFK_CHANNEL_NAME:
        await asyncio.sleep(60)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (member.id,))
        c.execute('UPDATE users SET voice_time = voice_time + 60 WHERE user_id = ?', (member.id,))
        conn.commit()
        conn.close()
        await check_role(member)

@bot.event
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    check_all_users.start()

    for guild in bot.guilds:
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue
                if voice_channel.name != AFK_CHANNEL_NAME:
                    print(f"▶️ Запуск отслеживания для {member.display_name} (уже в голосе)")
                    bot.loop.create_task(track_voice_time(member))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Игнорировать команды !stats !top !check
    if message.content.startswith('!'):
        if any(message.content.startswith(cmd) for cmd in ['!stats', '!top', '!check']):
            await bot.process_commands(message)
            return
        # иначе считаем сообщение валидным для подсчета, если >3 символов
        if len(message.content) < 3:
            await bot.process_commands(message)
            return
    else:
        # Для обычных сообщений — игнорировать если меньше 3 символов и без вложений
        if len(message.content) < 3 and not (message.stickers or message.attachments or message.embeds):
            await bot.process_commands(message)
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

@bot.command()
async def stats(ctx, member: discord.Member = None):
    member = member or ctx.author

    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT messages, voice_time FROM users WHERE user_id = ?', (member.id,))
    row = c.fetchone()
    conn.close()

    if row:
        msg, voice = row
        await ctx.send(f"{member.mention}, у тебя {msg} сообщений и {voice // 3600} ч {(voice % 3600) // 60} мин в голосовых.")
    else:
        await ctx.send(f"Данных по {member.display_name} нет.")

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
            response += f"— Нужно набрать:10 часов в голосе за период"
        else:
            response += "— Роль активна, но таймер не запущен."
    else:
        response += "— Роль за активность не получена, таймер не запущен."

    await ctx.send(response)

@bot.command()
async def top(ctx):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, messages, voice_time FROM users ORDER BY (messages + voice_time / 60 * 3) DESC LIMIT 5')
    rows = c.fetchall()
    conn.close()
    if not rows:
        await ctx.send("Нет данных для топа.")
        return

    response = "🏆 Топ по активности:\n"
    for i, (user_id, messages, voice_time) in enumerate(rows, start=1):
        member = ctx.guild.get_member(user_id)
        if member:
            score = messages + (voice_time // 60) * 3
            response += f"{i}. {member.display_name} — {messages} сообщений, {voice_time // 3600} ч {(voice_time % 3600) // 60} мин в голосовых (баллы: {score})\n"
    await ctx.send(response)

bot.run(token)
