"""
小米智能插座 功耗监控 - 采集器 (多设备版)
处理宿舍断电场景 (23:30-06:00)
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

CST = timezone(timedelta(hours=8))
log = logging.getLogger("power_monitor.collector")

# cuco.plug.v3 MIoT 属性
PROPERTIES = [
    {"did": "power", "siid": 2, "piid": 1},
    {"did": "electric_power", "siid": 11, "piid": 2},
    {"did": "power_consumption", "siid": 11, "piid": 1},
    {"did": "temperature", "siid": 12, "piid": 2},
]

MAX_RETRIES = 3
RETRY_DELAY = 2

# 预编译 MIoT 属性映射，支持不同型号的属性表
DEVICE_PROPS = {
    "default": PROPERTIES,
    "chuangmi.plug.v3": [
        {"did": "power", "siid": 2, "piid": 1},
        {"did": "electric_power", "siid": 11, "piid": 2},
        {"did": "power_consumption", "siid": 11, "piid": 1},
        {"did": "temperature", "siid": 12, "piid": 2},
    ],
    "cuco.plug.v3": PROPERTIES,  # 米家智能插座3
}


def get_properties_for_model(model: str) -> list[dict]:
    return DEVICE_PROPS.get(model, DEVICE_PROPS["default"])


def is_night_outage() -> bool:
    """判断当前是否在宿舍断电时段 (23:30-06:00)"""
    now = datetime.now(CST)
    h, m = now.hour, now.minute
    if h >= 23 and m >= 30:
        return True
    if h < 6:
        return True
    return False


def collect_once(ip: str, token: str, model: str = "") -> dict:
    """采集一次功率数据，带重试"""
    from miio import Device

    plug = Device(ip=ip, token=token)
    props = get_properties_for_model(model)

    for attempt in range(MAX_RETRIES):
        try:
            result = plug.send("get_properties", props)
            vals = {}
            for r in result:
                if r.get("code") == 0:
                    vals[r["did"]] = r["value"]

            return {
                "reachable": True,
                "power_on": 1 if vals.get("power") else 0,
                "power_w": float(vals.get("electric_power", 0)),
                "temperature": int(vals.get("temperature", 0)),
                "power_consumption": int(vals.get("power_consumption", 0)),
            }
        except Exception as e:
            log.warning(f"[{ip}] 采集失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    return {"reachable": False}


def smart_collect(ip: str, token: str, model: str = "") -> dict:
    """智能采集：断电时段只试一次，失败标记不可达"""
    if is_night_outage():
        log.debug(f"[{ip}] 断电时段，单次尝试...")
        try:
            result = collect_once(ip, token, model)
            if not result.get("reachable"):
                return {"reachable": False}
            return result
        except Exception:
            log.info(f"[{ip}] 宿舍断电中，不可达")
            return {"reachable": False}
    else:
        return collect_once(ip, token, model)