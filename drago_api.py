# ============================================================
# DRAGO MINI APP API — Flask routes for the Telegram Web App
# Імпортуй цей модуль у своєму головному файлі або скопіюй маршрути
# ============================================================
# Підключення: у головному файлі додай:
#   from drago_api import register_drago_routes
#   register_drago_routes(app)
# ============================================================

import os
import threading
import time
from flask import request, jsonify

# ── Конфіг ──
ADMIN_TELEGRAM_ID = 5512316636
MAX_OFFLINE_SECONDS = 8 * 3600  # ліміт фоновой регенерації — 8 годин

# ── Лок для потокобезпеки БД ──
drago_db_lock = threading.Lock()


def _get_conn():
    """Повертає psycopg2 з'єднання. Використовує DATABASE_URL."""
    import psycopg2
    return psycopg2.connect(os.environ.get("DATABASE_URL"))


def _create_table_if_needed():
    """Створює таблицю drago_users якщо її ще немає."""
    with drago_db_lock:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS drago_users (
                    telegram_id    BIGINT PRIMARY KEY,
                    username       TEXT DEFAULT 'Анонім',
                    first_name     TEXT DEFAULT 'Гравець',
                    money          DOUBLE PRECISION DEFAULT 0,
                    tap_power      INTEGER DEFAULT 1,
                    energy         DOUBLE PRECISION DEFAULT 1000,
                    max_energy     INTEGER DEFAULT 1000,
                    energy_drain   INTEGER DEFAULT 5,
                    energy_regen   INTEGER DEFAULT 3,
                    passive_income DOUBLE PRECISION DEFAULT 0,
                    total_taps     INTEGER DEFAULT 0,
                    player_level   INTEGER DEFAULT 1,
                    player_xp      INTEGER DEFAULT 0,
                    max_combo      INTEGER DEFAULT 1,
                    login_streak   INTEGER DEFAULT 0,
                    last_daily_claim TEXT DEFAULT '',
                    is_banned      BOOLEAN DEFAULT FALSE,
                    is_admin       BOOLEAN DEFAULT FALSE,
                    cards          JSONB DEFAULT NULL,
                    collection_items JSONB DEFAULT NULL,
                    upgrades       JSONB DEFAULT NULL,
                    last_update    TIMESTAMP DEFAULT NOW()
                );
            """)
        conn.commit()
        conn.close()


def _apply_offline_regen(cur, telegram_id):
    """Нараховує енергію + пасивний дохід за час відсутності."""
    cur.execute(
        "SELECT energy, max_energy, energy_regen, passive_income, money, last_update "
        "FROM drago_users WHERE telegram_id = %s",
        (telegram_id,)
    )
    row = cur.fetchone()
    if not row or not row[4]:  # no last_update
        return

    energy, max_energy, energy_regen, passive_income, money, last_update = row
    if last_update is None:
        return

    now_ts = time.time()
    try:
        last_ts = last_update.timestamp()
    except Exception:
        return

    elapsed = int(now_ts - last_ts)
    if elapsed <= 0:
        return

    capped = min(elapsed, MAX_OFFLINE_SECONDS)
    new_money = money
    new_energy = energy

    if energy is not None and max_energy is not None and energy < max_energy:
        regen = (energy_regen or 3) * capped
        new_energy = min(max_energy, energy + regen)

    if passive_income and passive_income > 0:
        new_money += (passive_income / 3600.0) * capped

    cur.execute(
        "UPDATE drago_users SET energy = %s, money = %s WHERE telegram_id = %s",
        (new_energy, new_money, telegram_id)
    )


def _row_to_dict(row):
    """Конвертує рядок БД у словник для API відповіді."""
    if not row:
        return None
    return {
        "telegramId": row[0],
        "username": row[1],
        "firstName": row[2],
        "money": row[3] or 0,
        "tapPower": row[4] or 1,
        "energy": row[5] if row[5] is not None else 1000,
        "maxEnergy": row[6] or 1000,
        "energyDrain": row[7] or 5,
        "energyRegen": row[8] or 3,
        "passiveIncome": row[9] or 0,
        "totalTaps": row[10] or 0,
        "playerLevel": row[11] or 1,
        "playerXP": row[12] or 0,
        "maxCombo": row[13] or 1,
        "loginStreak": row[14] or 0,
        "lastDailyClaim": row[15] or "",
        "isBanned": row[16] or False,
        "isAdmin": row[17] or False,
        "cards": row[18] or [],
        "collectionItems": row[19] or [],
        "upgrades": row[20] or {},
        "lastUpdate": row[21].isoformat() if row[21] else None,
    }


def register_drago_routes(app):
    """Реєструє всі маршрути Mini App на Flask app."""
    _create_table_if_needed()

    # ────────────────────────────────────────────
    # 1. Синхронізація користувача (POST /api/user/sync)
    # ────────────────────────────────────────────
    @app.route("/api/user/sync", methods=["POST"])
    def drago_user_sync():
        try:
            data = request.get_json() or {}
            telegram_id = data.get("telegramId")
            if not telegram_id:
                return jsonify({"error": "Немає Telegram ID"}), 400

            username = data.get("username") or "Анонім"
            first_name = data.get("firstName") or "Гравець"
            is_admin = int(telegram_id) == ADMIN_TELEGRAM_ID

            with drago_db_lock:
                conn = _get_conn()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT telegram_id FROM drago_users WHERE telegram_id = %s",
                        (telegram_id,)
                    )
                    exists = cur.fetchone()

                    if not exists:
                        cur.execute("""
                            INSERT INTO drago_users
                                (telegram_id, username, first_name, is_admin, last_update)
                            VALUES (%s, %s, %s, %s, NOW())
                        """, (telegram_id, username, first_name, is_admin))
                    else:
                        cur.execute("""
                            UPDATE drago_users
                            SET username = %s, first_name = %s,
                                is_admin = %s OR is_admin
                            WHERE telegram_id = %s
                        """, (username, first_name, is_admin, telegram_id))

                    # Нарахування офлайн-регенерації
                    _apply_offline_regen(cur, telegram_id)

                    cur.execute(
                        "UPDATE drago_users SET last_update = NOW() WHERE telegram_id = %s",
                        (telegram_id,)
                    )

                    cur.execute("""
                        SELECT telegram_id, username, first_name, money, tap_power,
                               energy, max_energy, energy_drain, energy_regen,
                               passive_income, total_taps, player_level, player_xp,
                               max_combo, login_streak, last_daily_claim, is_banned,
                               is_admin, cards, collection_items, upgrades, last_update
                        FROM drago_users WHERE telegram_id = %s
                    """, (telegram_id,))
                    row = cur.fetchone()
                conn.commit()
                conn.close()

            if row and row[16]:  # isBanned
                return jsonify({"banned": True}), 403

            return jsonify(_row_to_dict(row)), 200

        except Exception as e:
            print(f"❌ /api/user/sync error: {e}")
            return jsonify({"error": str(e)}), 500

    # ────────────────────────────────────────────
    # 2. Збереження прогресу (POST /api/user/save)
    # ────────────────────────────────────────────
    @app.route("/api/user/save", methods=["POST"])
    def drago_user_save():
        try:
            data = request.get_json() or {}
            telegram_id = data.get("telegramId")
            if not telegram_id:
                return jsonify({"error": "Немає Telegram ID"}), 400

            import json
            with drago_db_lock:
                conn = _get_conn()
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE drago_users SET
                            money = %s, tap_power = %s, energy = %s, max_energy = %s,
                            energy_drain = %s, energy_regen = %s, passive_income = %s,
                            total_taps = %s, player_level = %s, player_xp = %s,
                            max_combo = %s, login_streak = %s, last_daily_claim = %s,
                            cards = %s, collection_items = %s, upgrades = %s,
                            last_update = NOW()
                        WHERE telegram_id = %s
                    """, (
                        data.get("money", 0),
                        data.get("tapPower", 1),
                        data.get("energy", 1000),
                        data.get("maxEnergy", 1000),
                        data.get("energyDrain", 5),
                        data.get("energyRegen", 3),
                        data.get("passiveIncome", 0),
                        data.get("totalTaps", 0),
                        data.get("playerLevel", 1),
                        data.get("playerXP", 0),
                        data.get("maxCombo", 1),
                        data.get("loginStreak", 0),
                        data.get("lastDailyClaim", ""),
                        json.dumps(data.get("cards", [])),
                        json.dumps(data.get("collectionItems", [])),
                        json.dumps(data.get("upgrades", {})),
                        telegram_id
                    ))
                conn.commit()
                conn.close()

            return jsonify({"success": True}), 200

        except Exception as e:
            print(f"❌ /api/user/save error: {e}")
            return jsonify({"error": str(e)}), 500

    # ────────────────────────────────────────────
    # 3. Таблиця лідерів (GET /api/leaderboard)
    # ────────────────────────────────────────────
    @app.route("/api/leaderboard", methods=["GET"])
    def drago_leaderboard():
        try:
            with drago_db_lock:
                conn = _get_conn()
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT telegram_id, first_name, username, money, player_level, total_taps
                        FROM drago_users
                        WHERE is_banned = FALSE
                        ORDER BY money DESC
                        LIMIT 50
                    """)
                    rows = cur.fetchall()
                conn.close()

            users = [{
                "telegramId": r[0],
                "firstName": r[1] or "Гравець",
                "username": r[2],
                "money": r[3] or 0,
                "playerLevel": r[4] or 1,
                "totalTaps": r[5] or 0
            } for r in rows]

            return jsonify(users), 200

        except Exception as e:
            print(f"❌ /api/leaderboard error: {e}")
            return jsonify([]), 200

    # ────────────────────────────────────────────
    # 4. Адмін-панель: список гравців (GET /api/admin/users/<adminId>)
    # ────────────────────────────────────────────
    @app.route("/api/admin/users/<int:admin_id>", methods=["GET"])
    def drago_admin_users(admin_id):
        try:
            if admin_id != ADMIN_TELEGRAM_ID:
                return jsonify({"error": "Не адмін"}), 403

            with drago_db_lock:
                conn = _get_conn()
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT telegram_id, username, first_name, money, tap_power,
                               energy, max_energy, energy_drain, energy_regen,
                               passive_income, total_taps, player_level, player_xp,
                               max_combo, login_streak, last_daily_claim, is_banned,
                               is_admin, cards, collection_items, upgrades, last_update
                        FROM drago_users
                        ORDER BY money DESC
                    """)
                    rows = cur.fetchall()
                conn.close()

            users = [_row_to_dict(r) for r in rows]
            return jsonify(users), 200

        except Exception as e:
            print(f"❌ /api/admin/users error: {e}")
            return jsonify({"error": str(e)}), 500

    # ────────────────────────────────────────────
    # 5. Адмін: додати гроші (POST /api/admin/add-money)
    # ────────────────────────────────────────────
    @app.route("/api/admin/add-money", methods=["POST"])
    def drago_admin_add_money():
        try:
            data = request.get_json() or {}
            admin_id = data.get("adminId")
            target_id = data.get("targetTelegramId")
            amount = data.get("amount", 100000)

            if int(admin_id or 0) != ADMIN_TELEGRAM_ID:
                return jsonify({"error": "Не адмін"}), 403

            with drago_db_lock:
                conn = _get_conn()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE drago_users SET money = money + %s WHERE telegram_id = %s",
                        (amount, target_id)
                    )
                    cur.execute(
                        "SELECT money FROM drago_users WHERE telegram_id = %s",
                        (target_id,)
                    )
                    row = cur.fetchone()
                conn.commit()
                conn.close()

            if not row:
                return jsonify({"error": "Не знайдено"}), 404

            return jsonify({"success": True, "newBalance": row[0]}), 200

        except Exception as e:
            print(f"❌ /api/admin/add-money error: {e}")
            return jsonify({"error": str(e)}), 500

    # ────────────────────────────────────────────
    # 6. Адмін: бан/розбан (POST /api/admin/toggle-ban)
    # ────────────────────────────────────────────
    @app.route("/api/admin/toggle-ban", methods=["POST"])
    def drago_admin_toggle_ban():
        try:
            data = request.get_json() or {}
            admin_id = data.get("adminId")
            target_id = data.get("targetTelegramId")

            if int(admin_id or 0) != ADMIN_TELEGRAM_ID:
                return jsonify({"error": "Не адмін"}), 403

            with drago_db_lock:
                conn = _get_conn()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE drago_users SET is_banned = NOT is_banned WHERE telegram_id = %s",
                        (target_id,)
                    )
                    cur.execute(
                        "SELECT is_banned FROM drago_users WHERE telegram_id = %s",
                        (target_id,)
                    )
                    row = cur.fetchone()
                conn.commit()
                conn.close()

            if not row:
                return jsonify({"error": "Не знайдено"}), 404

            return jsonify({"success": True, "isBanned": row[0]}), 200

        except Exception as e:
            print(f"❌ /api/admin/toggle-ban error: {e}")
            return jsonify({"error": str(e)}), 500
