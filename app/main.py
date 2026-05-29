"""
小米智能插座功耗监控 - FastAPI 主服务 (多设备版)
"""

import os
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.background import BackgroundScheduler

from .db import PowerDB
from .collector import collect_once

# ============ 配置 ============
PLUG_IP = os.environ.get("PLUG_IP", "")
PLUG_TOKEN = os.environ.get("PLUG_TOKEN", "")
COLLECT_INTERVAL = int(os.environ.get("COLLECT_INTERVAL", "60"))
DB_PATH = os.environ.get("DB_PATH", "/data/power_data.db")
PORT = int(os.environ.get("PORT", "8080"))

# 多设备配置: DEVICES_JSON = [{"id":"plug1",...}, ...]
DEVICES_JSON = os.environ.get("DEVICES_JSON", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "cuco.plug.v3")

# 电价配置 (居民阶梯/峰谷)
PEAK_RATE = float(os.environ.get("PEAK_RATE", "0.56"))
VALLEY_RATE = float(os.environ.get("VALLEY_RATE", "0.36"))
STANDBY_THRESHOLD = float(os.environ.get("STANDBY_THRESHOLD", "5.0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("power_monitor")

db = PowerDB(DB_PATH)

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
def api_today_stats(device_id: str):
    stats = db.get_today_stats(device_id)
    stats["device_id"] = device_id
    stats["uptime_pct"] = db.get_uptime_today(device_id)
    return stats


@app.get("/api/stats/{device_id}/today/readings")
def api_today_readings(device_id: str):
    return db.get_today_readings(device_id)


@app.get("/api/stats/{device_id}/hourly")
def api_hourly_stats(device_id: str, days: int = 2):
    return db.get_hourly_stats(device_id, days=days)


@app.get("/api/stats/{device_id}/daily")
def api_daily_stats(device_id: str, days: int = 7):
    return db.get_daily_stats(device_id, days=days)


@app.get("/api/stats/{device_id}/heatmap")
def api_heatmap(device_id: str, days: int = 14):
    return db.get_heatmap_data(device_id, days=days)


@app.get("/api/stats/{device_id}/cost")
def api_cost(device_id: str, days: int = 30):
    return db.get_cost_estimate(device_id, days=days,
                                peak_rate=PEAK_RATE, valley_rate=VALLEY_RATE)


@app.get("/api/stats/{device_id}/standby")
def api_standby(device_id: str):
    return db.get_standby_stats(device_id, threshold_w=STANDBY_THRESHOLD)


@app.get("/api/stats/{device_id}/peak")
def api_peak(device_id: str):
    return db.get_peak_annotation(device_id) or {}


@app.get("/api/config")
def api_config():
    return {
        "peak_rate": PEAK_RATE,
        "valley_rate": VALLEY_RATE,
        "standby_threshold": STANDBY_THRESHOLD,
        "collect_interval": COLLECT_INTERVAL,
        "devices": [{"id": d["id"], "name": d.get("name", d["id"])}
                     for d in devices_config],
    }


# ============ 前端 ============

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))