---
name: serial-monitor
description: Monitor and capture serial port outputs, Triggers on: 串口, 连接串口,读串口, 读取串口,看串口数据, serial monitor, COM口, CH340, MCU调试输出,UART,USART capture, read serial, monitor serial, capture serial, 抓取串口数据, 串口日志.
---

# MM32 Serial Monitor

通过 pyserial 连接 CH340 USB 串口，读取捕获调试输出、发送指令。

## 核心能力

- **自动检测** CH340 端口，115200,n,8,1
- **输出格式** — ASCII / Hex (`--hex`)
- **日志保存** — `--log` 写入文件（默认 `C:/Users/yg/AppData/Local/Temp/yg8_uart.txt`）
- **空闲超时** — `--timeout N` 秒后自动断开
- **发送指令** — `--send k1|k2|k3|k4` 向 MCU 发送命令字串

## 工作流程

### 1. 激活虚拟环境

```bash
source E:/__electric/tools/pyserial/.venv/Scripts/activate
```

### 2. 运行

```bash
python $HOME/.claude/skills/eetool/serial-monitor/scripts/serial_monitor.py [选项]
```

| 选项 | 说明 |
|------|------|
| `--list` | 列出所有 COM 端口 |
| `--port COM7` | 指定端口（默认自动检测 CH340） |
| `--baud 115200` | 波特率 |
| `--hex` | Hex 显示 |
| `--timeout N` | N 秒无数据自动断开 |
| `--send k1` | 发送命令 (k1-k4)，然后进入监听模式 |

### 3. 发送命令

```bash
python $HOME/.claude/skills/eetool/serial-monitor/scripts/serial_monitor.py --send k1
```

| 命令 | 发送内容 |
|------|---------|
| k1 | `"k1\r\n"` |
| k2 | `"k2\r\n"` |
| k3 | `"k3\r\n"` |
| k4 | `"k4\r\n"` |

## 故障排除

| 症状 | 原因 | 解决 |
|------|------|------|
| `PermissionError` | 端口占用 | 关闭其他串口工具（PuTTY 等） |
| `No CH340 port found` | 设备未连接 | `--list` 确认，或手动 `--port` |
| 输出乱码 | 波特率不匹配 | 确认 MCU 固件波特率 |
| 无输出 | MCU 未发数据 | 检查固件运行状态和接线 |
