# 乘员技能模板工具 (Crew Skill Trainer)

独立离线脚本：把离线车库存档中**尚未学任何技能**的乘员，按其角色的预设模板设置为 7 个生效技能（100%）+ 1 个在练技能（0%）。

- 存档： `%APPDATA%\Wargaming.net\WorldOfTanks\offline_lan_0922\garage_state.json`
- 客户端数据： `C:\games\wot_0.9.22_cn`（车辆库/技能名静态提取，首次运行生成 `vehicle_db.json` 缓存）
- 已学技能的乘员一律跳过，不影响同车其他乘员。

## 用法

在仓库根目录运行（Python 3，零第三方依赖）：

### TUI（推荐）

```bat
python -m trainer.tui
```

全屏菜单：

```
主菜单
 ├─ 1 选择车辆   勾选列表: ↑↓/PgUp/PgDn 滚动, 空格勾选, 直接输入增量搜索,
 │               A 全选筛选结果, N 取消, / 清空搜索, F 系别/等级/车型筛选
 │               (默认已选中全部白板乘员所在的车辆)
 ├─ 2 设置筛选   系别/等级/车型 勾选子菜单
 ├─ 3 预览变更   dry-run 报告 (仅选中车辆)
 ├─ 4 应用写入   先展示完整变更清单 → 按 Y 显式确认 → 进程检测 → 备份 → 原子写入
 ├─ 5 编辑技能模板 查看/修改每个角色组合的 7+1 技能, 保存到 templates.json
 ├─ 6 技能对照表  25 技能 内部名/中文名
 ├─ 7 恢复备份   选择 trainer-backup-* 回滚 (原文件另存 .before-restore)
 └─ q 退出
```

**日常流程**: 模板一次设好（菜单 5，持久保存在 `trainer/templates.json`）→
想玩什么车时打开 trainer → 菜单 1 搜索并勾选车辆 → 菜单 3 预览 → 菜单 4 写入。

**筛选语义**: 预览和写入只作用于「勾选 ∩ 当前筛选」的车辆；主菜单的
"已选"计数即为筛选后的有效数量，筛选条件会显示在主菜单副标题中。

**已学技能的乘员**: 默认跳过（选择车辆列表中标记为"已有技能"，可勾选）。
应用写入时若选中范围内含已学技能的乘员，会弹出提示菜单：
仅写白板（默认）/ 覆盖重写 / 取消。覆盖模式下该乘员的全部技能被模板替换。

### CLI

```bat
:: 查看全部技能 内部名/中文名 对照
python -m trainer.apply_crew_templates --list-skills

:: 全车库 dry-run（默认，不写盘，打印每辆车的变更清单）
python -m trainer.apply_crew_templates

:: 按条件筛选（可组合；中文别名亦可）
python -m trainer.apply_crew_templates --nation china --tier 8-10
python -m trainer.apply_crew_templates --class 中型坦克
python -m trainer.apply_crew_templates --vehicles "T-34,IS-7"

:: 实际写入（写前自动备份，客户端运行时拒绝写入）
python -m trainer.apply_crew_templates --apply

:: 覆盖已学技能的乘员（默认跳过他们）
python -m trainer.apply_crew_templates --apply --overwrite
```

写入前会复制 `garage_state.json.trainer-backup-<时间戳>`；写入为原子替换。**请务必先关闭游戏再 `--apply`**（工具会检测 `WorldOfTanks.exe`，可用 `--force` 跳过，不推荐）。

## 模板 (templates.json)

模板持久保存在 `trainer/templates.json`，推荐用 TUI 菜单 5「编辑技能模板」修改（勾选式编辑、保存即校验）。
也可直接手编该文件，规则：

键 = 角色组合：主角色在前、兼职角色按字母序，用 `+` 连接，如
`commander+gunner+radioman`（车长兼炮手/通信兵）。每条：

- `trained`：恰好 7 个生效技能（内部名，全部 100%）
- `training`：第 8 个在练技能（0%）；若该角色组合总共只有 7 个可学技能（纯装填手），必须为 `null`

技能必须对该组合合法（通用技能 repair/fireFighting/camouflage/brotherhood 全员可学）。模板不合法时工具拒绝运行并逐条报告原因。

## 结构

| 文件 | 职责 |
|---|---|
| `tankman_codec.py` | TankmanDescr 二进制编解码（布局逆向自客户端 `items/tankmen.pyc`，对全存档 2500+ 样本往返字节一致） |
| `skills_db.py` | 25 个技能表 + 中文名（取自国服客户端本地化） |
| `vehicle_db.py` | 从 `scripts.pkg` 提取车辆库（系别/等级/车型/槽位角色含兼职） |
| `apply_crew_templates.py` | CLI 主程序：筛选 → 跳过策略 → 写入 |
| `tui.py` | 全屏菜单界面（ANSI + msvcrt，纯标准库），复用 CLI 的全部逻辑 |
| `tests/` | `python -m unittest discover -s trainer.tests -t .` |
