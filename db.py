import sqlite3
from datetime import datetime


# ------------------------------------------------------------
#  CONNECTION
# ------------------------------------------------------------

def get_connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------
#  INIT DATABASE
# ------------------------------------------------------------

def init_db(path):
    conn = get_connection(path)
    cur = conn.cursor()

    # --- USERS ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            full_name TEXT,
            role TEXT DEFAULT 'user'
        )
    """)

    # --- CARS ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vin TEXT UNIQUE,
            mileage INTEGER,
            year INTEGER,
            owner_company TEXT,
            model TEXT,
            plate TEXT,
            fuel_type TEXT
        )
    """)

    # --- SERVICES ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER,
            mechanic_tg_id INTEGER,
            admin_tg_id INTEGER,
            description TEXT,
            desired_at TEXT,
            status TEXT DEFAULT 'pending',
            final_mileage INTEGER,
            cost_net REAL,
            comments TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (car_id) REFERENCES cars(id)
        )
    """)

    conn.commit()
    conn.close()


# ------------------------------------------------------------
#  USERS
# ------------------------------------------------------------

def add_user(path, tg_id, full_name):
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (tg_id, full_name) VALUES (?, ?)",
                (tg_id, full_name))
    conn.commit()
    conn.close()


def set_user_role(path, tg_id, role):
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role = ? WHERE tg_id = ?", (role, tg_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_user_role(path, tg_id):
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return row["role"] if row else None


def promote_to_admin_if_first(path, tg_id):
    """Если это первый юзер в системе — он становится админом"""
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    cnt = cur.fetchone()["cnt"]
    if cnt == 1:
        cur.execute("UPDATE users SET role = 'admin' WHERE tg_id = ?", (tg_id,))
    conn.commit()
    conn.close()


# ------------------------------------------------------------
#  CARS
# ------------------------------------------------------------

def add_car(path, vin, mileage, year, owner_company, model, plate, fuel_type):
    conn = get_connection(path)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO cars (vin, mileage, year, owner_company, model, plate, fuel_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (vin, mileage, year, owner_company, model, plate, fuel_type))

    conn.commit()
    car_id = cur.lastrowid
    conn.close()
    return car_id


def list_cars(path, limit=50):
    conn = get_connection(path)
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM cars
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()
    return rows


def get_car_by_vin(path, vin):
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM cars WHERE vin = ?", (vin,))
    row = cur.fetchone()
    conn.close()
    return row


def get_car_by_id(path, car_id):
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM cars WHERE id = ?", (car_id,))
    row = cur.fetchone()
    conn.close()
    return row


# ------------------------------------------------------------
#  SERVICES
# ------------------------------------------------------------

def create_service(path, car_id, mechanic_tg_id, admin_tg_id, description, desired_at):
    conn = get_connection(path)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO services (car_id, mechanic_tg_id, admin_tg_id, description, desired_at)
        VALUES (?, ?, ?, ?, ?)
    """, (car_id, mechanic_tg_id, admin_tg_id, description, desired_at))

    conn.commit()
    svc_id = cur.lastrowid
    conn.close()
    return svc_id


def update_service_status(path, svc_id, status):
    conn = get_connection(path)
    cur = conn.cursor()
    cur.execute("UPDATE services SET status = ? WHERE id = ?", (status, svc_id))
    conn.commit()
    conn.close()


# ❗❗❗ ВАЖНО: эта версия возвращает ВСЁ, что нужно
def get_service(path, svc_id):
    conn = get_connection(path)
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            s.*,
            c.plate,
            c.vin,
            c.owner_company
        FROM services s
        LEFT JOIN cars c ON c.id = s.car_id
        WHERE s.id = ?
    """, (svc_id,))

    row = cur.fetchone()
    conn.close()
    return row


def set_service_result(path, svc_id, final_mileage, cost_net, comments):
    conn = get_connection(path)
    cur = conn.cursor()

    cur.execute("""
        UPDATE services
        SET 
            final_mileage = ?,
            cost_net = ?,
            comments = ?,
            status = 'done'
        WHERE id = ?
    """, (final_mileage, cost_net, comments, svc_id))

    conn.commit()
    conn.close()


# ------------------------------------------------------------
#  REPORTS
# ------------------------------------------------------------

def monthly_report(path, year, month):
    conn = get_connection(path)
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(cost_net) AS total
        FROM services
        WHERE status = 'done'
          AND strftime('%Y', created_at) = ?
          AND strftime('%m', created_at) = ?
    """, (str(year), f"{month:02d}"))

    row = cur.fetchone()
    total = row["total"] if row["total"] is not None else 0

    commission = round(total * 0.10, 2)

    conn.close()
    return total, commission
