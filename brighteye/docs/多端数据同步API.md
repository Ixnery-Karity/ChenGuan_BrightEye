# 多端数据同步 API（建议5）

> PC 端起局域网 HTTP 服务，手机端（同一 Wi-Fi）定期上报聚合用眼指标，
> 报告合并出**全天候用眼负荷**。纯 Python 标准库实现，零新增依赖。

## 一、开启方式

```bash
python -m brighteye.main --sync              # 默认端口 8765
# 或 config.py → SyncConfig(enabled=True, port=8765, token="共享口令")
```

启动后控制台打印监听地址。手机端需与 PC 在**同一局域网**。

## 二、接口定义

### 1. `GET /api/ping` —— 发现 / 联通性测试
```json
{"app": "宸观 BrightEye", "version": "1.9.0-demo"}
```

### 2. `POST /api/usage` —— 设备上报（手机端定期调用）
```http
POST /api/usage
Content-Type: application/json
X-Sync-Token: <口令，仅配置了 token 时必需>

{"device": "android-xiaomi13",
 "screen_time_min": 87.5,          // 必填：今日累计亮屏/用眼分钟
 "blink_rate_avg": 12.3,           // 可选
 "dominant_emotion": "tired"}      // 可选
```
响应：`{"ok": true, "other_total_min": 87.5}`

- 按设备名去重取最新、按自然日聚合；`device` 缺省用来源 IP；
- 落盘 `data/sync_devices.json`，PC 重启不丢。

### 3. `GET /api/summary` —— 全局概览（手机端展示 PC 数据也走这里）
```json
{"devices": {"android-xiaomi13": {"screen_time_min": 87.5, "date": "2026-07-16", ...}},
 "other_total_min": 87.5,
 "pc": {"screen_time_min": 42.0, "blink_rate_avg": 14.2, "dominant_emotion": "neutral"}}
```

## 三、与健康报告的合并

`--sync` 开启后，会话报告（文本 + HTML）自动增加：

- `其它设备用眼 : 87.5 分钟（全天候合计 129.5 分钟）`
- AI 行为洞察的输入 facts 同样带上跨设备时长，大模型据此给出
  「离开电脑去玩手机 ≠ 眼睛休息」类的真实负荷建议。

## 四、快速自测（无手机也能验）

```bash
# 另开终端模拟手机上报
curl -X POST http://127.0.0.1:8765/api/usage \
     -H "Content-Type: application/json" \
     -d '{"device":"test-phone","screen_time_min":33.5}'
curl http://127.0.0.1:8765/api/summary
```

## 五、安全与隐私

- 仅监听局域网；可选共享口令（`X-Sync-Token` 请求头必须一致，否则 401）；
- **只传聚合指标**（分钟数/频率/情绪标签），不传任何画面帧——隐私友好，
  与商业计划书「本地推理、数据不出户」承诺一致；
- 端口被占用等异常 → 静默降级不启用，绝不阻塞主程序（离线铁律）。

## 六、Android 端对接建议（路线）

- MVP：UsageStatsManager 取今日亮屏时长，WorkManager 每 15 分钟 POST 一次；
- 发现 PC：局域网扫描 `/api/ping` 或手输 IP（演示期用后者，最稳）。
