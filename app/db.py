"""
小米智能插座 功耗监控 - 数据库层 (多设备版)
支持: 热力图、峰谷电费、待机检测、异常标注

时间戳统一使用 CST (UTC+8) 存储，查询不再对存储时间做偏移。
SQLite 的 datetime('now') 返回 UTC，对比时需要 +8 hours 转为 CST。
但存储字段已经是 CST，所以对 timestamp 列做 strftime/DATE 时不再 +8。
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

CST = timezone(timedelta(hours=8))
log = logging.getLogger("power_monitor.db")


class PowerDB:
    def __init__(self, db_path: str, collect_interval: int = 60, retention_days: int = 90):
        self.db_path = db_path
        self.collect_interval = collect_interval  # 秒, 用于 kWh 计算
        self.retention_days = retention_days
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON power_readings(timestamp)")
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

    # ---- Basic Queries ----
    # 时间戳已存为 CST，所有查询中:
    # - datetime('now', '+8 hours') 用于生成 CST "现在" 时刻，与存储的 CST 时间戳做比较
    # - 对 timestamp 列做 strftime/DATE 时不再 +8 hours (因为已经是 CST)

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
        """今日统计 — kWh 按实际采集间隔计算，而非硬编码60s"""
        today = datetime.now(CST).strftime("%Y-%m-%d")
        interval_hours = self.collect_interval / 3600.0
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total_points,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as unreachable_count,
                    SUM(CASE WHEN reachable=1 AND power_on=1 THEN 1 ELSE 0 END) as on_points,
                    ROUND(AVG(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL THEN power_w END), 1) as avg_power,
                    ROUND(MAX(CASE WHEN reachable=1 AND power_w IS NOT NULL THEN power_w END), 1) as max_power,
                    ROUND(MIN(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL THEN power_w END), 1) as min_power,
                    ROUND(SUM(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL THEN power_w END) * ? / 1000.0, 4) as kwh_estimate,
                    MAX(CASE WHEN reachable=1 THEN temperature END) as max_temp,
                    ROUND(AVG(CASE WHEN reachable=1 AND temperature IS NOT NULL THEN temperature END), 1) as avg_temp
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ?
            """, (interval_hours, device_id, f"{today}%")).fetchone()
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

    def get_uptime_today(self, device_id: str) -> float:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    ROUND(SUM(CASE WHEN reachable=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as uptime_pct
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ?
            """, (device_id, f"{today}%")).fetchone()
            # sqlite3.Row supports numeric indexing
            return dict(row).get("uptime_pct") or 0.0

    def get_hourly_stats(self, device_id: str, days: int = 2) -> list[dict]:
        """按小时统计 — timestamp 已是 CST，不再 +8"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    strftime('%Y-%m-%d %H', timestamp) as hour_key,
                    ROUND(MIN(CASE WHEN power_on=1 AND reachable=1 AND power_w IS NOT NULL THEN power_w END), 1) as min_w,
                    ROUND(AVG(CASE WHEN power_on=1 AND reachable=1 AND power_w IS NOT NULL THEN power_w END), 1) as avg_w,
                    ROUND(MAX(CASE WHEN reachable=1 AND power_w IS NOT NULL THEN power_w END), 1) as max_w,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as unreachable,
                    COUNT(*) as total,
                    ROUND(AVG(CASE WHEN reachable=1 AND temperature IS NOT NULL THEN temperature END), 1) as avg_temp
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
                GROUP BY hour_key
                ORDER BY hour_key
            """, (device_id, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]

    def get_daily_stats(self, device_id: str, days: int = 7) -> list[dict]:
        """按天统计 — timestamp 已是 CST，DATE 直接取无需 +8"""
        interval_hours = self.collect_interval / 3600.0
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    DATE(timestamp) as day,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as unreachable,
                    COUNT(*) as total,
                    ROUND(SUM(CASE WHEN power_on=1 AND reachable=1 AND power_w IS NOT NULL THEN power_w END) * ? / 1000.0, 4) as kwh,
                    ROUND(AVG(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL THEN power_w END), 1) as avg_power,
                    ROUND(MAX(CASE WHEN reachable=1 AND power_w IS NOT NULL THEN power_w END), 1) as max_power,
                    ROUND(AVG(CASE WHEN reachable=1 AND temperature IS NOT NULL THEN temperature END), 1) as avg_temp,
                    MAX(CASE WHEN reachable=1 THEN temperature END) as max_temp
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
                GROUP BY day
                ORDER BY day
            """, (interval_hours, device_id, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]

    # ---- Advanced Queries ----

    def get_heatmap_data(self, device_id: str, days: int = 14) -> list[dict]:
        """功率热力图 — timestamp 已是 CST，不再 +8"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    DATE(timestamp) as day,
                    CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                    ROUND(AVG(CASE WHEN reachable=1 AND power_w IS NOT NULL THEN power_w ELSE NULL END), 1) as avg_w,
                    MAX(CASE WHEN reachable=1 AND power_w IS NOT NULL THEN power_w ELSE NULL END) as max_w,
                    COUNT(CASE WHEN reachable=1 THEN 1 END) as samples,
                    SUM(CASE WHEN reachable=0 THEN 1 ELSE 0 END) as offline
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
                GROUP BY day, hour
                ORDER BY day, hour
            """, (device_id, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]

    def get_cost_estimate(self, device_id: str, days: int = 30,
                          peak_rate: float = 0.56, valley_rate: float = 0.36,
                          peak_start: int = 8, peak_end: int = 21) -> dict:
        """电费估算 — timestamp 已是 CST，strftime('%H', timestamp) 直接取 CST 小时
        peak_start/peak_end: 峰段起止小时 (含起不含终), 默认 8-21"""
        interval_hours = self.collect_interval / 3600.0
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    ROUND(SUM(
                        CASE
                            WHEN CAST(strftime('%H', timestamp) AS INTEGER) BETWEEN ? AND ?
                            AND reachable=1 AND power_w IS NOT NULL
                            THEN power_w ELSE 0
                        END
                    ) * ? / 1000.0, 4) as peak_kwh,
                    ROUND(SUM(
                        CASE
                            WHEN CAST(strftime('%H', timestamp) AS INTEGER) NOT BETWEEN ? AND ?
                            AND reachable=1 AND power_w IS NOT NULL
                            THEN power_w ELSE 0
                        END
                    ) * ? / 1000.0, 4) as valley_kwh,
                    ROUND(SUM(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL THEN power_w END) * ? / 1000.0, 4) as total_kwh,
                    COUNT(DISTINCT DATE(timestamp)) as days_covered
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
            """, (peak_start, peak_end, interval_hours,
                  peak_start, peak_end, interval_hours,
                  interval_hours, device_id, f"-{days} days")).fetchone()

        if not row or row[2] is None:
            return {"total_kwh": 0, "peak_kwh": 0, "valley_kwh": 0,
                    "peak_cost": 0, "valley_cost": 0, "total_cost": 0, "daily_avg_cost": 0, "days_covered": 0}

        peak_kwh = row[0] or 0
        valley_kwh = row[1] or 0
        total_kwh = row[2] or 0
        days_covered = row[3] or 1

        peak_cost = round(peak_kwh * peak_rate, 2)
        valley_cost = round(valley_kwh * valley_rate, 2)
        total_cost = round(peak_cost + valley_cost, 2)
        daily_avg_cost = round(total_cost / max(days_covered, 1), 2)

        return {
            "total_kwh": round(total_kwh, 4),
            "peak_kwh": round(peak_kwh, 4),
            "valley_kwh": round(valley_kwh, 4),
            "peak_cost": peak_cost,
            "valley_cost": valley_cost,
            "total_cost": total_cost,
            "daily_avg_cost": daily_avg_cost,
            "days_covered": days_covered,
            "peak_rate": peak_rate,
            "valley_rate": valley_rate,
        }

    def get_standby_stats(self, device_id: str, threshold_w: float = 5.0, days: int = 30) -> dict:
        """待机检测 — kWh 按实际间隔计算，默认统计近30天"""
        interval_hours = self.collect_interval / 3600.0
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    SUM(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL AND power_w < ? THEN 1 ELSE 0 END) as standby_points,
                    SUM(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL AND power_w >= ? THEN 1 ELSE 0 END) as active_points,
                    ROUND(SUM(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL AND power_w < ? THEN power_w END) * ? / 1000.0, 4) as standby_kwh,
                    ROUND(AVG(CASE WHEN reachable=1 AND power_on=1 AND power_w IS NOT NULL AND power_w < ? THEN power_w END), 1) as avg_standby_w,
                    COUNT(*) as total
                FROM power_readings
                WHERE device_id=? AND timestamp >= datetime('now', ?, '+8 hours')
            """, (threshold_w, threshold_w, threshold_w, interval_hours, threshold_w, device_id, f"-{days} days")).fetchone()

        if not row:
            return {"standby_points": 0, "active_points": 0, "standby_kwh": 0,
                    "avg_standby_w": 0, "standby_pct": 0, "threshold_w": threshold_w}

        standby_points = row[0] or 0
        active_points = row[1] or 0
        standby_kwh = row[2] or 0
        avg_standby_w = row[3] or 0

        return {
            "standby_points": standby_points,
            "active_points": active_points,
            "standby_kwh": round(standby_kwh, 4),
            "avg_standby_w": avg_standby_w,
            "standby_pct": round(standby_points * 100.0 / max(standby_points + active_points, 1), 1),
            "threshold_w": threshold_w,
        }

    def get_peak_annotation(self, device_id: str) -> dict | None:
        """今日异常标注: 功率峰值时间点 — 两个查询在同一连接内完成"""
        today = datetime.now(CST).strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute("""
                SELECT timestamp, power_w, temperature
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ? AND reachable=1 AND power_w IS NOT NULL
                ORDER BY power_w DESC LIMIT 1
            """, (device_id, f"{today}%")).fetchone()

            if not row or row[1] is None:
                return None

            avg_row = conn.execute("""
                SELECT ROUND(AVG(power_w), 1) as avg_w
                FROM power_readings
                WHERE device_id=? AND timestamp LIKE ? AND reachable=1 AND power_on=1 AND power_w IS NOT NULL
            """, (device_id, f"{today}%")).fetchone()

        avg_w = dict(avg_row).get("avg_w") if avg_row else 0
        peak_w = row[1] or 0
        multiplier = round(peak_w / avg_w, 1) if avg_w and avg_w > 0 else 0

        return {
            "timestamp": row[0],
            "peak_w": peak_w,
            "avg_w": avg_w,
            "multiplier": multiplier,
            "temperature": row[2],
        }

    def purge_old(self) -> int:
        """清理超过 retention_days 天的旧数据，返回删除行数"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM power_readings WHERE timestamp < datetime('now', ?, '+8 hours')",
                (f"-{self.retention_days} days",),
            )
            deleted = cursor.rowcount
            if deleted > 0:
                log.info(f"已清理 {deleted} 条超过 {self.retention_days} 天的旧数据")
                conn.execute("VACUUM")
        return deleted