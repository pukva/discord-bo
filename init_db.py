import sqlite3

conn = sqlite3.connect('user_stats.db')
c = conn.cursor()
# Создай таблицу, если не существует
c.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    messages INTEGER DEFAULT 0,
    voice_time INTEGER DEFAULT 0,
    timer_start TEXT
)''')
# Добавь столбец timer_start, если его нет
try:
    c.execute('''ALTER TABLE users ADD COLUMN timer_start TEXT''')
    print("Столбец timer_start добавлен.")
except sqlite3.OperationalError:
    print("Столбец timer_start уже существует.")
conn.commit()
conn.close()
print("База данных и таблица users созданы или уже существуют.")