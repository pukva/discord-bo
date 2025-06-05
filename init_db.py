import sqlite3

DB_NAME = 'user_stats.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            messages INTEGER DEFAULT 0,
            voice_time INTEGER DEFAULT 0,
            timer_start TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print("База данных и таблица users созданы или уже существуют.")

if __name__ == "__main__":
    init_db()
