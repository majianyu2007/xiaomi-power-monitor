# ⚡ 小米智能插座 功耗监控

类 SmokePing 风格的实时功耗监控面板，自动适配系统主题（浅色/深色/跟随系统）。

支持多插座同时监控，设备不可达时优雅降级。

![Python](https://img.shields.io/badge/Python-3.11-3776ab?style=flat-square)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ed?style=flat-square)

## 🌟 功能

- ⚡ 每分钟采集实时功率 (W)、温度 (°C)、开关状态
- 📊 今日分钟级实时功率曲线
- 📈 24h min/avg/max 柱状图（SmokePing 风格）
- 🌡️ 功率热力图（近14天，行=天 列=小时）
- 📅 7天用电趋势 (kWh)
- 💰 峰谷分时电费估算（默认陕西居民电价）
- 💤 待机功耗检测与占比统计
- ⚡ 今日峰值标注（倍率对比均值）
- 🌡️ 插头温度趋势
- 🔌 **多插座支持** — 一个面板监控所有插座
- 🎨 **三档主题** — 浅色 / 深色 / 跟随系统，一键切换
- 📱 **响应式布局** — 手机 / 平板 / 桌面自适应
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

使用 [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor) 获取设备 Token：

```bash
pip install micloud
python3 -m micloud
# 按提示登录小米账号，即可看到所有设备的 IP 和 Token
```

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
| `PEAK_RATE` | `0.56` | 峰段电价 (元/kWh) |
| `VALLEY_RATE` | `0.36` | 谷段电价 (元/kWh) |
| `STANDBY_THRESHOLD` | `5` | 待机功率阈值 (W) |

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
│  响应式前端  │◄────────────│   FastAPI      │
│  (主题切换)  │────────────►│   REST API    │
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