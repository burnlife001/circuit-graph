# easyeda-agent 器件快照

本目录是 easyeda-agent 仓库中两份器件参考数据的**原样离线快照**，供
`scripts/import_easyeda_parts.py` 导入 KB 使用。运行时不需要 EasyEDA Pro、
`.eext` 连接器或 daemon 60832——只读这两份 JSON。

## 来源

- 源仓库：`E:/__electric/__workspace/stacking/easyeda-agent`
- 快照 commit：`af76cd7ea73cd9789a68782cb4ad18d921fd0074`
- 相对路径：
  - `standard-parts.json` ← `.agents/skills/easyeda-agent/references/standard-parts.json`
  - `symbol-pins.json` ← `.agents/skills/easyeda-agent/references/symbol-pins.json`

## 数据口径

- `standard-parts.json` 每条形如 `{"value", "mpn", "lcsc", "manufacturer", "desc", ...}`，
  其中 `desc` 为**单件测量/核对**记录（single-part measurement），
  不代表整板验证，也不保证全工况参数。
- `symbol-pins.json` 的引脚来自"已放置器件回读"，`parts` 的键是
  `standard-parts.json` 的统一 slug；只有部分条目有引脚表。
- 导入规则见 `scripts/import_easyeda_parts.py`：无源前缀 `res` / `cap` / `ind`
  不导入；`deviceUuid`、`footprint` 不进 KB。

## 漂移检查

`manifest.json` 记录两份 JSON 的 sha256、字节数与上游相对路径：

```bash
python scripts/import_easyeda_parts.py --check-snapshot   # 有漂移则退出码 1
python scripts/import_easyeda_parts.py --update-manifest --source-repo <easyeda-agent 路径>
```

`--check-snapshot` 校验"本地快照是否被改动"，并在上游仓库就在本机时比对其
文件内容与 commit。重新取快照后必须重跑 `--update-manifest`，否则检查会报
`本地快照已被修改`。

## 许可

MIT License, Copyright (c) 2026 zhoushoujianwork and contributors
