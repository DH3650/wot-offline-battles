# 车库助手 (Trainer)

独立离线脚本，管理离线车库存档：

- **车组管理**：把存档中**尚未学任何技能**的乘员，按其角色的预设模板设置为 7 个生效技能（100%）+ 1 个在练技能（0%）。
- **库存管理**：查看并设定配件（输弹机、高级输弹机等 optional devices）的账户级库存数量（默认 200）。

- 存档： `%APPDATA%\Wargaming.net\WorldOfTanks\offline_lan_0922\saves\<存档>\garage_state.json`（默认编辑 `default` 存档）
- 客户端数据： `C:\games\wot_0.9.22_cn`（车辆库/配件目录/技能名静态提取，首次运行生成 `vehicle_db.json` / `artefact_db.json` 缓存）
- 已学技能的乘员一律跳过，不影响同车其他乘员。

## 用法

在仓库根目录运行（Python 3，零第三方依赖）：

### TUI（推荐）

```bat
python -m trainer.tui
```

全屏菜单：

```
主菜单 (车库助手)
 ├─ 1 车组管理   乘员技能模板全部功能:
 │    ├─ 1 选择车辆   勾选列表: ↑↓/PgUp/PgDn 滚动, 空格勾选, 直接输入增量搜索,
 │    │               A 全选筛选结果, N 取消, / 清空搜索, F 系别/等级/车型筛选
 │    │               (默认已选中全部白板乘员所在的车辆)
 │    ├─ 2 设置筛选   系别/等级/车型 勾选子菜单
 │    ├─ 3 预览变更   dry-run 报告 (仅选中车辆)
 │    ├─ 4 应用写入   先展示完整变更清单 → 按 Y 显式确认 → 进程检测 → 备份 → 原子写入
 │    ├─ 5 编辑技能模板 查看/修改每个角色组合的 7+1 技能, 保存到 templates.json
 │    ├─ 6 技能对照表  25 技能 内部名/中文名
 │    └─ q 返回主菜单
 ├─ 2 库存管理   配件库存数量 (账户级, 不含已安装在车上的):
 │    ├─ 1 选择配件   勾选列表(操作同选择车辆), 默认全选全部配件
 │    ├─ 2 目标数量   数字输入, 默认 200; 0 表示删除该库存记录
 │    ├─ 3 预览变更   dry-run (仅列出数量会变化的配件)
 │    ├─ 4 应用写入   清单确认 → 进程检测 → 备份 → 原子写入
 │    ├─ 5 库存一览   按类别分组、组内按价格(=等级)排序的当前库存表格
 │    └─ q 返回主菜单
 ├─ 3 选择存档   切换当前编辑的存档槽位 (默认 default; 切换后重新载入车库)
 ├─ 4 恢复备份   选择 trainer-backup-* 回滚 (原文件另存 .before-restore)
 └─ q 退出
```

**日常流程（车组）**: 模板一次设好（车组管理菜单 5，持久保存在 `trainer/templates.json`）→
想玩什么车时打开 trainer → 车组管理 → 菜单 1 搜索并勾选车辆 → 菜单 3 预览 → 菜单 4 写入。

**日常流程（库存）**: 库存管理 → 菜单 1 勾选要改的配件（默认全选）→ 菜单 2 设数量（默认 200）→
菜单 3 预览 → 菜单 4 写入。菜单 5 可随时查看当前库存表。

**筛选语义**: 预览和写入只作用于「勾选 ∩ 当前筛选」的车辆；主菜单的
"已选"计数即为筛选后的有效数量，筛选条件会显示在菜单副标题中。

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

:: 编辑其它存档槽位（默认 default）
python -m trainer.apply_crew_templates --slot Career-Mode

:: 覆盖已学技能的乘员（默认跳过他们）
python -m trainer.apply_crew_templates --apply --overwrite
```

写入前会复制 `garage_state.json.trainer-backup-<时间戳>`；写入为原子替换。**请务必先关闭游戏再 `--apply`**（工具会检测 `WorldOfTanks.exe`，可用 `--force` 跳过，不推荐）。

## 模板 (templates.json)

模板持久保存在 `trainer/templates.json`，推荐用 TUI 车组管理菜单 5「编辑技能模板」修改（勾选式编辑、保存即校验）。
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
| `artefact_db.py` | 从 `scripts.pkg` 提取配件目录（optional devices，含中文名/单价，缓存 `artefact_db.json`） |
| `inventory_stock.py` | 配件库存读写（`owned['9']`）与按类别分组的库存表格 |
| `apply_crew_templates.py` | CLI 主程序：筛选 → 跳过策略 → 写入 |
| `tui.py` | 全屏菜单界面（ANSI + msvcrt，纯标准库），复用 CLI 的全部逻辑 |
| `tests/` | `python -m unittest discover -s trainer.tests -t .` |

## 车辆强化

顶层菜单的“车辆强化”编辑 launcher 已有的共享车辆属性方案。车辆方案位于
`%APPDATA%\Wargaming.net\WorldOfTanks\offline_lan_0922\vehicle_profiles.json`，
不属于任何单独存档；trainer 中选择的当前存档只影响车组和库存功能。方案选择
默认优先使用 `SPG`，也可以在车辆强化菜单内切换其他已有方案。

车辆选择来自完整的可编辑车辆库，并支持国家、等级、车型和文字筛选，最终单选
一辆车。车辆行用 Tag 显示当前方案推导出的强化状态：蓝色 Lv1、紫色 Lv2、金色
Lv3，无法精确归入预设的修改显示为 Lv.S。共享火炮、发动机和炮弹造成的连带车辆
会在写入前的预览中完整列出。

预设保存在 `vehicle_presets.json`，默认分类包括机动、火控、俯角、伤害、装甲、
观察、生存和隐蔽。普通倍率挡位为 1.5 / 2 / 3；越小越强的项目使用其倒数。
俯角分别在原始正数配置上增加 5 / 10 / 15 度。配置规则支持 `multiply`、
`subtract`、`add` 和 `equal`，以及可选的最小值、最大值和整数舍入。Lv.S 自定义
模式允许对所选分类的每个实际字段分别输入倍率，倍率 1 表示不改。

分类下直接保存 `rules`，不再使用 `levels`、继承或缩放配置。每条规则的 `value`
固定为三个值的数组，第 1、2、3 项分别对应 Lv1、Lv2、Lv3；`equal` 也遵循相同
规则。通常三个挡位使用同一个 `operation`；装甲等各挡位运算方式不同的规则可将
`operation` 同样写成三个值的数组。规则的 `name` 是便于人工修改配置的中文说明。

每条规则还通过 `rounding` 和 `digits` 约束结果格式：`round` 使用常规四舍五入，
`truncate` 朝零去尾，`digits: 0` 表示整数。默认策略按现有车辆字段归纳：整数型
属性使用 0 位，装填/瞄准/转速与俯角等使用 3 位，精度与扩圈使用 4 位，隐蔽使用
6 位；履带地形适应性固定去尾到 3 位。Lv.S 自定义修改沿用对应规则的格式策略。

机动分类的前进/倒车速度不使用倍率：三个挡位分别在原始值上增加 10/20/30，
前进速度最高 90、倒车速度最高 60；履带地面阻力计算结果最多保留 3 位小数。
单辆车可以同时启用多个分类方案，编辑菜单会持续显示当前选择；“查看方案细则”
按 Esc 只返回编辑菜单，最后进入“套用全部已启用方案”才会统一预览、创建一次备份
并写入。该确认菜单按 Esc 会取消本次车辆编辑。

“火控”严格对应 launcher 车辆编辑器的“火炮（Gun）”目录，包括装填、弹夹射速、
瞄准、精度、火炮扩圈、穿深、弹速、火炮转速和备弹量；不包含底盘扩圈、炮塔转速
或炮弹伤害。备弹量 `maxAmmo` 独立使用 2/3/4 倍并按整数写入。

每次写入前会把当前方案备份到仓库根目录：

```text
.user/trainer/vehicle-backups/<日期>_<时间>_before_<车辆名>/
```

备份恢复只替换其中的同名车辆方案，不覆盖 `vehicle_profiles.json` 中的其他方案，
且恢复前会再次备份。`.user/trainer/vehicle_data_cache.sqlite3` 同时保存车辆数据缓存与可重建的 Trainer 索引；
trainer 会比较方案和预设哈希，在发现外部修改时自动作废旧 Tag 并重新评估。
