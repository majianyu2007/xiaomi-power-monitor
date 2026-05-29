"""
小米智能插座 功耗监控 - 采集器 (多设备版)

修复:
- 缺失传感器值存为 None 而非 0（区分「没读到」和「0W」）
- power_on 严格布尔判断
- 添加网络超时
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

CST = timezone(timedelta(hours=8))
log = logging.getLogger("power_monitor.collector")

# 缓存 Device 连接，避免每次采集都创建新连接
_device_cache: dict[str, object] = {}


def _get_device(ip: str, token: str) -> object:
    """获取或缓存的 Device 实例"""
    from miio import Device
    cache_key = f"{ip}:{token}"
    if cache_key not in _device_cache:
        _device_cache[cache_key] = Device(ip=ip, token=token, timeout=SOCKET_TIMEOUT)
    return _device_cache[cache_key]


# cuco.plug.v3 MIoT 属性
PROPERTIES = [
    {"did": "power", "siid": 2, "piid": 1},
    {"did": "electric_power", "siid": 11, "piid": 2},
    {"did": "power_consumption", "siid": 11, "piid": 1},
    {"did": "temperature", "siid": 12, "piid": 2},
]

MAX_RETRIES = 3
RETRY_DELAY = 2
SOCKET_TIMEOUT = 5  # 秒

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


def _parse_int(val) -> int | None:
    """安全解析整数，失败返回 None"""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_float(val) -> float | None:
    """安全解析浮点数，失败返回 None"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def collect_once(ip: str, token: str, model: str = "") -> dict:
    """采集一次功率数据，带重试和超时。
    
    Returns:
        reachable=True 时: 完整数据 dict，缺失值为 None
        reachable=False 时: {"reachable": False}
    """
    from miio import Device

    props = get_properties_for_model(model)

    for attempt in range(MAX_RETRIES):
        try:
            plug = _get_device(ip, token)
            result = plug.send("get_properties", props)
            vals = {}
            for r in result:
                if r.get("code") == 0:
                    vals[r["did"]] = r["value"]

            # power: 严格布尔判断 — "on"/True/1 → 1, 其余 → 0
            power_val = vals.get("power")
            if power_val is True or power_val == 1 or power_val == "on":
                power_on = 1
            elif power_val is False or power_val == 0 or power_val == "off" or power_val is None:
                power_on = 0
            else:
                power_on = 1 if bool(power_val) else 0

            return {
                "reachable": True,
                "power_on": power_on,
                "power_w": _parse_float(vals.get("electric_power")),      # None 而非 0
                "temperature": _parse_int(vals.get("temperature")),        # None 而非 0
                "power_consumption": _parse_int(vals.get("power_consumption")),  # None 而非 0
            }
        except Exception as e:
            log.warning(f"[{ip}] 采集失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            # 连接出错时清除缓存，下次重建
            cache_key = f"{ip}:{token}"
            _device_cache.pop(cache_key, None)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    return {"reachable": False}