---
name: serial-monitor
description: 通过 CH340 串口发送 k1-k4 按键命令、读取 MCU 调试输出。触发：串口, 连接串口, 读串口, 看串口数据, serial monitor, COM口, CH340, MCU调试输出, UART, 发送按键, k1, k2。
---

# MM32 Serial Monitor

pyserial 连接 CH340（COM10，115200 8N1），发送命令 + 监听输出。

## 用法

```bash
source E:/__electric/tools/pyserial/.venv/Scripts/activate
SM="C:/Users/yg/.claude/skills/__myskills/02.电子工具/serial-monitor/scripts/serial_monitor.py"

python "$SM" --list                                # 列端口（CH340 自动标 ★）
python "$SM" --port COM10 --send k1 --timeout 1    # 发命令并监听 1s
```

- `--send k1|k2|k3|k4` 发送 `"k1\r\n"` 等，随后进入监听；`--timeout N` 空闲 N 秒自动断开
- 日志默认写 `C:/Users/yg/AppData/Local/Temp/yg8_uart.txt`
- 硬件事实：调试口 = COM10(CH340) → CN3 PA1/PB1(UART2)；COM5 是 DAP 虚拟串口，**别用**

## 流程纪律：捕获 + 按键必须同一条 bash

跨工具调用发按键 = 每次新 shell + python 冷启动，按键会落在逻辑分析仪采集窗口之外（2026-10-25 实踩，r1 白抓一次）。联合验证标准写法：

```bash
source E:/__electric/tools/pyserial/.venv/Scripts/activate
SM="C:/Users/yg/.claude/skills/__myskills/02.电子工具/serial-monitor/scripts/serial_monitor.py"
python ".../atk_cli.py" capture start --ch 0,1,2,3,4,5 --duration 12s --output "E:/__electric/atk-logic-data/<name>" > cap.log 2>&1 &
sleep 4 && python "$SM" --port COM10 --send k1 --timeout 1   # t≈4s
sleep 4 && python "$SM" --port COM10 --send k1 --timeout 1   # t≈8s
wait   # 等捕获结束
```

注意：`capture &` 后的第一个 sleep 要把 atk_cli 自身 python 启动耗时（~2s）算进窗口对齐。

k1 实时性已由用户 sscom 复测确认（MCU 即时响应）；若真出现响应异常，用 `ccc minimax` 独立验证本脚本实时性。

## 故障排除

| 症状 | 原因 | 解决 |
|------|------|------|
| `PermissionError` | 端口占用（sscom/PuTTY） | 关闭其它串口工具 |
| `No CH340 port found` | 设备未连接 | `--list` 确认 |
| 乱码 | 波特率不符 | 确认固件波特率 |
