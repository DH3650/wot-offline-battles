# 乘员技能模板工具 (Crew Skill Template Trainer) 实施计划

**Goal:** 独立 Python 3 CLI（`trainer/`），读取离线存档 `garage_state.json`，对筛选出的、尚未学技能的乘员，按其角色组合模板写入 7 个生效技能（100%）+ 1 个在练技能（0%），原子写回。

**Architecture:** 纯离线路线（路线A）。纯 Python 实现的 `TankmanDescr` 二进制编解码器 + 从客户端 `scripts.pkg` 静态提取的车辆库 + 内嵌 24 技能表。不依赖 BigWorld 运行时。

**Tech Stack:** Python 3.12，仅标准库；pytest 做测试；复用 `tools/packed_xml.py` 读取 Packed XML。

## Global Constraints

- 存档路径: `C:\Users\Dougl\AppData\Roaming\Wargaming.net\WorldOfTanks\offline_lan_0922\garage_state.json` (schema 4)
- 客户端路径: `C:\games\wot_0.9.22_cn`，数据在 `res\packages\scripts.pkg`
- 零第三方运行时依赖（开发期可用 decompyle3/xdis 反编译，仅放在隔离 venv）
- 跳过策略：乘员已有任意技能（count>0）→ 跳过该乘员，不影响同车其他乘员
- 模板按**角色组合**定义（如 `commander+radioman`），每条 = 7 个生效技能 + 1 个在练技能，内部技能名
- 写入形态 = `count=8`，技能字节 = 7 生效 + 第 8 在练，`lastSkillLevel=0`（与存档中 120 个原生样本同构）
- 筛选维度：`--nation` / `--tier` / `--class` / `--vehicles` 可组合
- 安全：默认 dry-run；写前时间戳备份；原子替换；客户端进程在运行则拒绝写入

## 已核实的关键事实（调查阶段结论）

1. `garage_state.json` 结构：`vehicles[vehicleTypeCompactDescr].crew[slotIndex] = base64(TankmanDescr strCompactDescr)`；588 辆车，乘员 2~6 人。
2. 描述符布局（实证）：`[2B id][1B role][1B roleLevel=100][1B count][count×1B 技能索引][2B lastSkillLevel][2B×3][4B]`，总长 = 19 + count。180 个有技能样本全部满足。
3. `lastSkillLevel` 只描述最后一个技能的进度，前面的隐含 100%。
4. 技能表 24 个（`item_defs/tankmen/tankmen.xml`）：4 通用 + 车长5/驾驶员5/炮手4/装填手3/通信兵4（含 driver_badRoadsKing=如履平地, brotherhood=兄弟连, camouflage=隐蔽, repair=维修）。
5. `vehicleTypeCompactDescr = nationID<<12 | vehicleID`。
6. 车辆 `<crew>` 段给出每槽位主角色；兼职角色写在角色元素的文本值里（空白分隔，如雷诺 FT 车长 = `gunner radioman loader`）。
7. 客户端加载存档时用原生解析器逐条校验乘员描述符，坏字节 → 整档回退默认车库（不会半损坏）；`.bak` 兜底；写入为原子替换（`config.py: write_json`）。
8. 战斗侧 `loadout.py` 按客户端投影的 `level/isActive/isEnable` 判定技能生效，0% 在练技能不生效。

## 文件结构

```
trainer/
  tankman_codec.py    # TankmanDescr 二进制编解码
  skills_db.py        # 24 技能表 + 中文对照 + 角色归属
  vehicle_db.py       # scripts.pkg → 车辆库 JSON 缓存（系别/等级/车型/槽位角色组合）
  apply_crew_templates.py  # CLI 主程序
  templates.json      # 用户角色组合模板
  tests/              # pytest；真实存档 2567 blob 做往返语料
```

## 任务

- [ ] Task 0: 反编译 `scripts.pkg:scripts/common/items/tankmen.pyc`（隔离 venv + decompyle3/xdis），钉死字段布局（角色枚举值、count 字节语义、lastSkillLevel 偏移、XP/freeXP 字段位置、newSkillCount 对 freeXP 的依赖）。产出：布局注释写入 `tankman_codec.py` 模块 docstring。
- [ ] Task 1: `tankman_codec.py` 编解码器（TDD）。验收：对真实存档全部 2567 个乘员 blob 往返字节一致；能正确读出技能名列表与在练进度。
- [ ] Task 2: `skills_db.py`（24 技能、中文名、角色归属、通用技能集合）。
- [ ] Task 3: `vehicle_db.py`：解析 `item_defs/vehicles/<nation>/list.xml` + 各车 XML 的 `<crew>`/tags/level；兼职角色解析；生成 `vehicle_db.json` 缓存；四维筛选器。
- [ ] Task 4: 模板应用：校验模板（技能对角色组合合法、无重复、生效≠在练）→ 对目标乘员写 8 条目 + lastSkillLevel=0，其余字段原样保留 → 跳过 count>0 乘员并记录原因。
- [ ] Task 5: 写前时间戳备份到 `garage_state.json.trainer-backup-<ts>`；原子替换写回；检测 `WorldOfTanks.exe` 运行中则拒绝写入；默认 `--dry-run` 打印变更清单。
- [ ] Task 6: 端到端验收：真实存档 dry-run 报告 → 单系别小范围应用 → 启动客户端确认车库加载正常、技能显示 7 生效 + 1 在练。
