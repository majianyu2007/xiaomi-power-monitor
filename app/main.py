"""
小米智能插座功耗监控 - FastAPI 主服务 (多设备版)
支持: 日/周/月/年/自定义范围视图
"""

import os
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.background import BackgroundScheduler

from .db import PowerDB
from .collector import collect_once

# ============ 配置 ============
PLUG_IP = os.environ.get("PLUG_IP", "")
PLUG_TOKEN = os.environ.get("PLUG_TOKEN", "")
COLLECT_INTERVAL = max(int(os.environ.get("COLLECT_INTERVAL", "30")), 5)  # 最低5秒, 防止过快
DB_PATH = os.environ.get("DB_PATH", "/data/power_data.db")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0"))  # 数据保留天数, 0=永不清理
PORT = int(os.environ.get("PORT", "8080"))

# 多设备配置: DEVICES_JSON = [{"id":"plug1",...}, ...]
DEVICES_JSON = os.environ.get("DEVICES_JSON", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "cuco.plug.v3")

# 电价配置 (居民阶梯/峰谷)
ELECTRICITY_RATE = float(os.environ.get("ELECTRICITY_RATE", "0.5109"))
PEAK_RATE = float(os.environ["PEAK_RATE"]) if "PEAK_RATE" in os.environ else None
VALLEY_RATE = float(os.environ["VALLEY_RATE"]) if "VALLEY_RATE" in os.environ else None
# 峰谷时段：默认 8:00-21:59 为峰段，其余为谷段
PEAK_HOURS_START = int(os.environ.get("PEAK_HOURS_START", "8"))
PEAK_HOURS_END = int(os.environ.get("PEAK_HOURS_END", "21"))
STANDBY_THRESHOLD = float(os.environ.get("STANDBY_THRESHOLD", "5.0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("power_monitor")

db = PowerDB(DB_PATH, collect_interval=COLLECT_INTERVAL, retention_days=RETENTION_DAYS)

# ============ 设备管理 ============

def load_devices() -> list[dict]:
    devices = []

    if DEVICES_JSON:
        try:
            devices = json.loads(DEVICES_JSON)
            log.info(f"从 DEVICES_JSON 加载 {len(devices)} 个设备")
        except json.JSONDecodeError as e:
            log.error(f"DEVICES_JSON 解析失败: {e}")

    if not devices and PLUG_IP and PLUG_TOKEN:
        devices = [{
            "id": "plug_default",
            "name": "米家智能插座3",
            "model": DEFAULT_MODEL,
            "ip": PLUG_IP,
            "token": PLUG_TOKEN,
        }]
        log.info("使用 PLUG_IP/PLUG_TOKEN 单设备配置")

    for d in devices:
        db.upsert_device(
            device_id=d["id"],
            name=d.get("name", d["id"]),
            model=d.get("model", DEFAULT_MODEL),
            ip=d.get("ip", ""),
            token=d.get("token", ""),
        )

    return devices


devices_config = load_devices()

if not devices_config:
    log.warning("⚠️ 未配置任何设备！请设置 DEVICES_JSON 或 PLUG_IP+PLUG_TOKEN")


# ============ 定时采集 ============

def collect_and_store():
    for dev in devices_config:
        try:
            data = collect_once(dev["ip"], dev["token"], dev.get("model", DEFAULT_MODEL))
            ts = db.insert(device_id=dev["id"], **data)
            if data.get("reachable"):
                log.info(
                    f"✓ [{dev['name']}] {ts} | "
                    f"{'ON' if data.get('power_on') else 'OFF'} "
                    f"| {data.get('power_w', 0):.1f}W "
                    f"| {data.get('temperature', 0)}°C"
                )
            else:
                log.info(f"○ [{dev['name']}] {ts} | 不可达")
        except Exception as e:
            log.error(f"[{dev['name']}] 采集异常: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if devices_config:
        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(
            collect_and_store,
            "interval",
            seconds=COLLECT_INTERVAL,
            id="power_collect",
            name="功率采集",
            max_instances=1,
        )
        # 每天凌晨4点自动清理旧数据 (RETENTION_DAYS=0 时跳过)
        if RETENTION_DAYS > 0:
            scheduler.add_job(
                db.purge_old, "cron", hour=4, minute=0, id="db_purge", name="数据清理"
            )
        collect_and_store()
        scheduler.start()
        log.info(f"调度器已启动，采集间隔 {COLLECT_INTERVAL}s，共 {len(devices_config)} 个设备")
    yield
    if scheduler:
        scheduler.shutdown()
        log.info("调度器已停止")


app = FastAPI(title="⚡ 小米插座功耗监控", lifespan=lifespan)


# ============ API ============

@app.get("/api/devices")
def api_devices():
    return db.get_devices()


@app.get("/api/latest")
def api_latest_all():
    return db.get_all_latest()


@app.get("/api/latest/{device_id}")
def api_latest(device_id: str):
    row = db.get_latest(device_id)
    if not row:
        return {"error": "no data", "device_id": device_id}
    return row


@app.get("/api/stats/{device_id}/today")
def api_today_stats(device_id: str, date: Optional[str] = None):
    stats = db.get_today_stats(device_id, date=date)
    stats["device_id"] = device_id
    stats["uptime_pct"] = db.get_uptime_today(device_id, date=date)
    return stats


@app.get("/api/stats/{device_id}/today/readings")
def api_today_readings(device_id: str, date: Optional[str] = None):
    return db.get_today_readings(device_id, date=date)


@app.get("/api/stats/{device_id}/hourly")
def api_hourly_stats(device_id: str, days: int = 2,
                     start_date: Optional[str] = None, end_date: Optional[str] = None):
    return db.get_hourly_stats(device_id, days=days, start_date=start_date, end_date=end_date)


@app.get("/api/stats/{device_id}/daily")
def api_daily_stats(device_id: str, days: int = 7,
                    start_date: Optional[str] = None, end_date: Optional[str] = None):
    return db.get_daily_stats(device_id, days=days, start_date=start_date, end_date=end_date)


@app.get("/api/stats/{device_id}/monthly")
def api_monthly_stats(device_id: str, months: int = 12,
                      start_date: Optional[str] = None, end_date: Optional[str] = None):
    return db.get_monthly_stats(device_id, months=months, start_date=start_date, end_date=end_date)


@app.get("/api/stats/{device_id}/heatmap")
def api_heatmap(device_id: str, days: int = 14,
                start_date: Optional[str] = None, end_date: Optional[str] = None):
    return db.get_heatmap_data(device_id, days=days, start_date=start_date, end_date=end_date)


@app.get("/api/stats/{device_id}/cost")
def api_cost(device_id: str, days: int = 30,
             start_date: Optional[str] = None, end_date: Optional[str] = None):
    # 只有峰谷都设置了才用分时计价
    if PEAK_RATE is not None and VALLEY_RATE is not None:
        peak, valley = PEAK_RATE, VALLEY_RATE
        is_tou = True
    else:
        peak, valley = ELECTRICITY_RATE, ELECTRICITY_RATE
        is_tou = False
    result = db.get_cost_estimate(device_id, days=days, peak_rate=peak, valley_rate=valley,
                                    peak_start=PEAK_HOURS_START, peak_end=PEAK_HOURS_END,
                                    start_date=start_date, end_date=end_date)
    result["flat_rate"] = ELECTRICITY_RATE
    result["is_tou"] = is_tou
    return result


@app.get("/api/stats/{device_id}/standby")
def api_standby(device_id: str, days: int = 30,
                start_date: Optional[str] = None, end_date: Optional[str] = None):
    return db.get_standby_stats(device_id, threshold_w=STANDBY_THRESHOLD, days=days,
                                 start_date=start_date, end_date=end_date)


@app.get("/api/stats/{device_id}/peak")
def api_peak(device_id: str, date: Optional[str] = None):
    return db.get_peak_annotation(device_id, date=date) or {}


@app.get("/api/config")
def api_config():
    return {
        "electricity_rate": ELECTRICITY_RATE,
        "peak_rate": PEAK_RATE or None,
        "valley_rate": VALLEY_RATE or None,
        "is_tou": PEAK_RATE is not None and VALLEY_RATE is not None,
        "standby_threshold": STANDBY_THRESHOLD,
        "collect_interval": COLLECT_INTERVAL,
        "peak_hours_start": PEAK_HOURS_START,
        "peak_hours_end": PEAK_HOURS_END,
        "devices": [{"id": d["id"], "name": d.get("name", d["id"])}
                     for d in devices_config],
    }


@app.get("/api/dashboard/{device_id}")
def api_dashboard(device_id: str, view: str = "today", start_date: Optional[str] = None, end_date: Optional[str] = None):
    """聚合接口：一次请求返回当前视图所需的所有数据，减少前端并发请求数"""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _CST = _tz(_td(hours=8))
    today_str = _dt.now(_CST).strftime("%Y-%m-%d")
    if not end_date:
        end_date = today_str

    result = {"view": view, "device_id": device_id}

    # 实时状态 + 配置（所有视图都需要）
    result["latest"] = db.get_latest(device_id) or {"device_id": device_id, "reachable": 0}
    result["config"] = {
        "electricity_rate": ELECTRICITY_RATE,
        "peak_rate": PEAK_RATE or None,
        "valley_rate": VALLEY_RATE or None,
        "is_tou": PEAK_RATE is not None and VALLEY_RATE is not None,
        "standby_threshold": STANDBY_THRESHOLD,
        "collect_interval": COLLECT_INTERVAL,
        "peak_hours_start": PEAK_HOURS_START,
        "peak_hours_end": PEAK_HOURS_END,
    }

    if view == "day" or view == "today":
        date = start_date or today_str
        s, e = date, date
        result["today_stats"] = db.get_today_stats(device_id, date=date)
        result["today_stats"]["device_id"] = device_id
        result["today_stats"]["uptime_pct"] = db.get_uptime_today(device_id, date=date)
        result["readings"] = db.get_today_readings(device_id, date=date)
        result["hourly"] = db.get_hourly_stats(device_id, start_date=s, end_date=e)
        result["peak"] = db.get_peak_annotation(device_id, date=date) or {}
        result["cost"] = _calc_cost(device_id, s, e)

    elif view == "year":
        s = start_date or f"{_dt.now(_CST).year}-01-01"
        result["monthly"] = db.get_monthly_stats(device_id, start_date=s, end_date=end_date)
        result["cost"] = _calc_cost(device_id, s, end_date)

    else:  # range view: 7d, 30d, 90d, custom
        s = start_date or today_str
        e = end_date or today_str
        result["daily"] = db.get_daily_stats(device_id, start_date=s, end_date=e)
        result["cost"] = _calc_cost(device_id, s, e)
        result["standby"] = db.get_standby_stats(device_id, threshold_w=STANDBY_THRESHOLD, start_date=s, end_date=e)
        result["heatmap"] = db.get_heatmap_data(device_id, start_date=s, end_date=e)

    return result


def _calc_cost(device_id: str, start_date: str, end_date: str) -> dict:
    peak = PEAK_RATE if PEAK_RATE is not None else ELECTRICITY_RATE
    valley = VALLEY_RATE if VALLEY_RATE is not None else ELECTRICITY_RATE
    is_tou = PEAK_RATE is not None and VALLEY_RATE is not None
    result = db.get_cost_estimate(device_id, peak_rate=peak, valley_rate=valley,
                                    peak_start=PEAK_HOURS_START, peak_end=PEAK_HOURS_END,
                                    start_date=start_date, end_date=end_date)
    result["flat_rate"] = ELECTRICITY_RATE
    result["is_tou"] = is_tou
    return result


# ============ 前端 ============

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))