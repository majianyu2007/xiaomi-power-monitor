"""
小米智能插座 功耗监控 - 数据库层 (多设备版)
SQLite 持久化，支持多插座同时监控
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

CST = timezone(timedelta(hours=8))
log = logging.getLogger("power_monitor.db")


class PowerDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    model TEXT,
                    ip TEXT,
                    token TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS power_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    reachable INTEGER NOT NULL DEFAULT 1,
                    power_on INTEGER,
                    power_w REAL,
                    temperature INTEGER,
                    power_consumption INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ts ON power_readings(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_ts
                ON power_readings(device_id, timestamp)
            """)

    # ---- Device Management ----

    def upsert_device(self, device_id: str, name: str, model: str = "",
                      ip: str = "", token: str = ""):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO devices (device_id, name, model, ip, token)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    name=excluded.name, model=excluded.model,
                    ip=excluded.ip, token=excluded.token
            """, (device_id, name, model, ip, token))

    def get_devices(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
        return [dict(r) for r in rows]

    # ---- Data Insert ----

    def insert(self, device_id: str, reachable: bool, power_on: int | None = None,
               power_w: float | None = None, temperature: int | None = None,
               power_consumption: int | None = None) -> str:
        ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO power_readings
                   (timestamp, device_id, reachable, power_on, power_w, temperature, power_consumption)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, device_id, 1 if reachable else 0, power_on, power_w, temperature, power_consumption),
            )
        return ts

    # ---- Queries ----

    def get_latest(self, device_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM power_readings WHERE device_id=? ORDER BY id DESC LIMIT 1",
                (device_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_all_latest(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT r.*, d.name as device_name, d.model as device_model
                FROM power_readings r
                JOIN devices d ON r.device_id = d.device_id
                WHERE r.id IN (
                    SELECT MAX(id) FROM power_readings GROUP BY device_id
                )
            """).fetchall()
        return [dict(r) for r in rows]

    def get_today_stats(self, device_id: str) -> dict:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total_points,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as unreachable_count,
                    SUM(CASE WHEN reachable=1 AND power_on=1 THEN 1 ELSE 0 END) as on_points,
                    ROUND(AVG(CASE WHEN reachable=1 AND power_on=1 THEN power_w END), 1) as avg_power,
                    ROUND(MAX(CASE WHEN reachable=1 THEN power_w END), 1) as max_power,
                    ROUND(MIN(CASE WHEN reachable=1 AND power_on=1 THEN power_w END), 1) as min_power,
                    ROUND(SUM(CASE WHEN reachable=1 AND power_on=1 THEN power_w END) / 60.0 / 1000.0, 4) as kwh_estimate,
                    MAX(CASE WHEN reachable=1 THEN temperature END) as max_temp,
                    ROUND(AVG(CASE WHEN reachable=1 THEN temperature END), 1) as avg_temp
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ?
            """, (device_id, f"{today}%")).fetchone()
        return dict(row) if row else {}

    def get_today_readings(self, device_id: str) -> list[dict]:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT timestamp, power_w, power_on, reachable, temperature
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ?
                ORDER BY timestamp
            """, (device_id, f"{today}%")).fetchall()
        return [dict(r) for r in rows]

    def get_hourly_stats(self, device_id: str, days: int = 2) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    strftime('%Y-%m-%d %H', timestamp) as hour_key,
                    ROUND(MIN(CASE WHEN power_on=1 AND reachable=1 THEN power_w END), 1) as min_w,
                    ROUND(AVG(CASE WHEN power_on=1 AND reachable=1 THEN power_w END), 1) as avg_w,
                    ROUND(MAX(CASE WHEN reachable=1 THEN power_w END), 1) as max_w,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as unreachable,
                    COUNT(*) as total,
                    ROUND(AVG(CASE WHEN reachable=1 THEN temperature END), 1) as avg_temp
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
                GROUP BY hour_key
                ORDER BY hour_key
            """, (device_id, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]

    def get_daily_stats(self, device_id: str, days: int = 7) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    DATE(timestamp, '+8 hours') as day,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as unreachable,
                    COUNT(*) as total,
                    ROUND(SUM(CASE WHEN power_on=1 AND reachable=1 THEN power_w END) / 60.0 / 1000.0, 4) as kwh,
                    ROUND(AVG(CASE WHEN reachable=1 AND power_on=1 THEN power_w END), 1) as avg_power,
                    ROUND(MAX(CASE WHEN reachable=1 THEN power_w END), 1) as max_power,
                    ROUND(AVG(CASE WHEN reachable=1 THEN temperature END), 1) as avg_temp,
                    MAX(CASE WHEN reachable=1 THEN temperature END) as max_temp
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
                GROUP BY day
                ORDER BY day
            """, (device_id, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]

    def get_uptime_today(self, device_id: str) -> float:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    ROUND(SUM(CASE WHEN reachable=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as uptime_pct
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ?
            """, (device_id, f"{today}%")).fetchone()
            return row[0] if row and row[0] is not None else 0.0