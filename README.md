# ⚡ 小米智能插座 功耗监控

类 SmokePing 风格的实时功耗监控面板，Material Design 3 深色主题。

支持多插座同时监控，设备不可达时优雅降级（标记为 offline，不刷日志）。

![Dashboard Preview](https://img.shields.io/badge/MD3-暗色主题-9c27b0?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-3776ab?style=flat-square)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ed?style=flat-square)

## 🌟 功能

- ⚡ 每分钟采集实时功率 (W)、温度 (°C)、开关状态
- 📊 今日分钟级实时曲线 + 24h min/avg/max 柱状图 + 7天 kWh 趋势
- 🌡️ 插头温度趋势追踪
- 🔌 **多插座支持** — 一个面板监控所有插座
- 📱 响应式 MD3 深色 UI
- 🐳 Docker Compose 一键部署
- 💾 SQLite 持久化（Docker Volume）

## 🚀 快速开始

### 单插座

```bash
cp .env.example .env
# 编辑 .env 填入插座 IP 和 Token
docker compose up -d
```

访问 `http://<IP>:8080`

### 多插座

在 `.env` 中配置 `DEVICES_JSON`：

```bash
DEVICES_JSON=[{"id":"plug1","name":"书桌插座","model":"cuco.plug.v3","ip":"192.168.10.203","token":"xxx"},{"id":"plug2","name":"台灯插座","model":"chuangmi.plug.v3","ip":"192.168.10.204","token":"yyy"}]
```

> 多设备模式下无需设置 `PLUG_IP` / `PLUG_TOKEN`

### 获取 Token

1. 打开 https://miio2.miio.link 或 https://xiaomi-iot-token.vercel.app
2. 用小米账号登录，即可看到所有设备的 Token

## ⚙️ 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PLUG_IP` | `192.168.10.203` | 单插座 IP（多设备时忽略） |
| `PLUG_TOKEN` | (无) | 单插座 Token |
| `DEVICES_JSON` | (空) | 多设备 JSON 配置 |
| `COLLECT_INTERVAL` | `60` | 采集间隔（秒） |
| `DB_PATH` | `/data/power_data.db` | 数据库路径 |
| `PORT` | `8080` | Web 服务端口 |
| `DEFAULT_MODEL` | `cuco.plug.v3` | 默认设备型号 |

## 🏗️ 架构

```
┌─────────────┐    每60s     ┌──────────────┐
│  python-miio │◄────────────│  collector    │
│  (MIoT协议)  │             └──────┬───────┘
└─────────────┘                     │
                              ┌──────▼───────┐
                              │   SQLite DB   │
                              └──────┬───────┘
                                     │
┌─────────────┐    每30s刷新  ┌──────▼───────┐
│  MD3 前端    │◄────────────│   FastAPI      │
│  (index.html) │────────────►│   REST API    │
└─────────────┘             └──────────────┘
```

## 🛠️ 手动运行（无 Docker）

```bash
pip install -r requirements.txt
export PLUG_IP=192.168.10.203
export PLUG_TOKEN=your_token
python3 -m app.main
```

## 📋 支持设备

| 型号 | 名称 | 功率 | 温度 |
|------|------|------|------|
| `cuco.plug.v3` | 米家智能插座3 | ✅ | ✅ |
| `chuangmi.plug.v3` | 米家智能插座3 (楚微版) | ✅ | ✅ |

其他 MIoT 插座可自行添加属性映射，见 `app/collector.py` 中的 `DEVICE_PROPS`。

## 📄 License

MIT