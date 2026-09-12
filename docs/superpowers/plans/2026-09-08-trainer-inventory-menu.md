# Trainer 顶层菜单 + 库存管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** trainer TUI 改为两级菜单（车组管理 / 库存管理 / 选择存档 / 恢复备份 / 退出），新增配件库存管理：按配件种类勾选（默认全选）、设定目标数量（默认 200，可自定义）、预览并写入存档，并提供按类别分组、按等级（价格）排序的库存一览表格。

**Architecture:** 新增两个纯逻辑模块（`artefact_db.py` 配件目录 + JSON 缓存，`inventory_stock.py` 库存读写/表格），TUI（`tui.py`）只做展示与交互；现有车组功能整体迁入 `crew_menu()`，逻辑不变。

**Tech Stack:** Python 3 标准库（无第三方依赖），unittest。

## Global Constraints

- 运行目录为仓库根目录；测试命令 `python -m unittest discover -s trainer.tests -t .`
- 不执行任何 git 提交/变更操作（仓库已有未提交改动；除非用户明确要求）
- 客户端数据目录 `C:\games\wot_0.9.22_cn`（`apply_crew_templates.DEFAULT_CLIENT_DIR`），首次运行生成 `trainer/artefact_db.json` 缓存并纳入版本管理（与 `vehicle_db.json` 同策略）
- 存档结构：配件库存在 `state['owned']['9'][str(compact_descr)] = count`；数量为 0 的条目删除该键（与 `GarageState._set_owned` 语义一致）
- 配件 compact descriptor 公式（已对真实存档验证）：`cd = (artefact_id << 8) | 0xF0 | item_type`，配件 item_type = 9 → `(id << 8) | 0xF9`
- 默认目标数量 200，与客户端 `bootstrap.OFFLINE_ARTEFACT_STOCK` 一致
- 仅覆盖配件（optional devices，item type 9），不含给养/指令（item type 11）

## 已确认的用户决策

1. 库存管理的选择对象 = 配件种类（账户级库存，不按车辆），默认全选，勾选界面仿照选择车辆
2. 仅配件（optional_devices.xml 全部 49 种，含 delux 高级配件）
3. 数量允许自定义，默认 200
4. 顶层菜单：车组管理 / 库存管理 / 选择存档 / 恢复备份 / 退出
5. 库存表格：分组列表表格（类别分组，组内按价格=等级排序；列：配件 | 单价 | 当前库存）

---

### Task 1: `trainer/artefact_db.py` — 配件目录模块

**Files:**
- Create: `trainer/artefact_db.py`
- Test: `trainer/tests/test_artefact_db.py`
- Generate: `trainer/artefact_db.json`（构建产物，纳入版本管理）

**Interfaces:**
- Consumes: `trainer.vehicle_db` 的 `read_packed_xml/_children/_element_fields/_text/_load_mo_catalog/_resolve_name`
- Produces: `ArtefactInfo(compact_descr, item_type, artefact_id, key, user_string, name, price, tags)`；`artefact_compact_descr(artefact_id, item_type=9) -> int`；`build_artefact_db(scripts_pkg_path, text_dir) -> {int: ArtefactInfo}`；`artefact_db_to_json/artefact_db_from_json/save_artefact_db/load_artefact_db`；`ensure_artefact_db(path=DEFAULT_ARTEFACT_DB, client_dir=None, rebuild=False)`；常量 `OPTIONAL_DEVICE_ITEM_TYPE = 9`、`DEFAULT_ARTEFACT_DB`

- [ ] **Step 1: 写失败测试** `trainer/tests/test_artefact_db.py`

```python
"""Tests for trainer.artefact_db against the real client packages."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.artefact_db import (  # noqa: E402
    artefact_compact_descr, artefact_db_from_json, artefact_db_to_json,
    build_artefact_db)

CLIENT_DIR = os.environ.get('WOT_CLIENT_DIR', r'C:\games\wot_0.9.22_cn')
SCRIPTS_PKG = os.path.join(CLIENT_DIR, 'res', 'packages', 'scripts.pkg')
TEXT_DIR = os.path.join(CLIENT_DIR, 'res', 'text')


class CompactDescrTest(unittest.TestCase):

    def test_formula_anchors_from_live_save(self):
        # 真实存档 owned['9'] 的库存键: (id << 8) | 0xF9
        self.assertEqual(5625, artefact_compact_descr(21))
        self.assertEqual(11769, artefact_compact_descr(45))
        # 给养(item type 11)同一公式: largeRepairkit id=5 -> 1531
        self.assertEqual(1531, artefact_compact_descr(5, 11))


class JsonRoundTripTest(unittest.TestCase):

    def test_round_trip(self):
        payload = {'5625': {
            'compact_descr': 5625, 'item_type': 9, 'artefact_id': 21,
            'key': 'mediumCaliberTankRammer',
            'user_string': '#artefacts:mediumCaliberTankRammer/name',
            'name': '中型坦克炮输弹机', 'price': 200000,
            'tags': ['rammer']}}
        restored = artefact_db_from_json(payload)
        info = restored[5625]
        self.assertEqual('rammer', info.tags[0])
        self.assertEqual(200000, info.price)
        self.assertEqual(payload, artefact_db_to_json(restored))


@unittest.skipUnless(os.path.isfile(SCRIPTS_PKG), 'no client package')
class LiveBuildTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.artefacts = build_artefact_db(SCRIPTS_PKG, TEXT_DIR)

    def test_covers_every_optional_device(self):
        self.assertEqual(49, len(self.artefacts))

    def test_names_resolve_to_chinese(self):
        self.assertEqual('中型坦克炮输弹机', self.artefacts[5625].name)
        self.assertEqual('改进型装填系统', self.artefacts[11769].name)

    def test_delux_devices_have_no_xml_price(self):
        self.assertIsNone(self.artefacts[11769].price)

    def test_live_save_owned_keys_are_known(self):
        garage = os.path.join(
            os.environ.get('APPDATA', ''), 'Wargaming.net', 'WorldOfTanks',
            'offline_lan_0922', 'saves', 'default', 'garage_state.json')
        if not os.path.isfile(garage):
            self.skipTest('no live garage state')
        with open(garage, 'rb') as stream:
            owned = json.load(stream).get('owned', {}).get('9', {})
        # 目录必须能解释存档里出现的每个配件键 (数量可以不同)
        self.assertTrue(set(int(k) for k in owned) <= set(self.artefacts))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest trainer.tests.test_artefact_db -v`
Expected: FAIL `ModuleNotFoundError: No module named 'trainer.artefact_db'`

- [ ] **Step 3: 实现** `trainer/artefact_db.py`

```python
"""Optional-device (配件) catalogue extracted from the client's scripts.pkg.

Mirrors vehicle_db.py: reads
``scripts/item_defs/vehicles/common/optional_devices.xml`` (packed XML),
resolves Chinese names via ``artefacts.mo``, and caches the result to
``artefact_db.json`` next to this module so later runs need no client.

Compact descriptors follow the client's artefact layout, verified against a
live save (``owned['9']`` keys): ``(artefact_id << 8) | 0xF0 | item_type``.
"""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass, field

from trainer.vehicle_db import (
    _children, _element_fields, _load_mo_catalog, _resolve_name, _text,
    read_packed_xml)

OPTIONAL_DEVICE_ITEM_TYPE = 9
OPTIONAL_DEVICES_XML = 'scripts/item_defs/vehicles/common/optional_devices.xml'
ARTEFACTS_TEXT_DOMAIN = 'artefacts'
DEFAULT_ARTEFACT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'artefact_db.json')


@dataclass
class ArtefactInfo:
    compact_descr: int
    item_type: int
    artefact_id: int
    key: str
    user_string: str
    name: str
    price: int = None
    tags: list = field(default_factory=list)


def artefact_compact_descr(artefact_id, item_type=OPTIONAL_DEVICE_ITEM_TYPE):
    """The client's artefact layout: ``id << 8 | 0xF0 | item_type``."""
    return (int(artefact_id) << 8) | 0xF0 | int(item_type)


def build_artefact_db(scripts_pkg_path, text_dir):
    """Extract every optional device into ``{compact_descr: ArtefactInfo}``."""
    artefacts = {}
    catalog = _load_mo_catalog(text_dir, ARTEFACTS_TEXT_DOMAIN)
    with zipfile.ZipFile(scripts_pkg_path) as pkg:
        root = read_packed_xml(pkg.read(OPTIONAL_DEVICES_XML))
        for key, entry in _children(root):
            if not hasattr(entry.value, 'children'):
                continue
            fields = _element_fields(entry)
            try:
                artefact_id = int(fields.get('id', -1))
            except (TypeError, ValueError):
                continue
            if artefact_id < 0:
                continue
            key = _text(key)
            user_string = _text(fields.get('userString') or b'')
            raw_price = fields.get('price')
            try:
                price = int(raw_price) if raw_price is not None else None
            except (TypeError, ValueError):
                price = None
            compact_descr = artefact_compact_descr(artefact_id)
            artefacts[compact_descr] = ArtefactInfo(
                compact_descr=compact_descr,
                item_type=OPTIONAL_DEVICE_ITEM_TYPE,
                artefact_id=artefact_id,
                key=key,
                user_string=user_string,
                name=_resolve_name(catalog, user_string, key),
                price=price,
                tags=_text(fields.get('tags') or b'').split(),
            )
    return artefacts


def artefact_db_to_json(artefacts):
    return {
        str(compact_descr): {
            'compact_descr': info.compact_descr,
            'item_type': info.item_type,
            'artefact_id': info.artefact_id,
            'key': info.key,
            'user_string': info.user_string,
            'name': info.name,
            'price': info.price,
            'tags': list(info.tags),
        }
        for compact_descr, info in artefacts.items()
    }


def artefact_db_from_json(payload):
    artefacts = {}
    for compact_descr, row in payload.items():
        artefacts[int(compact_descr)] = ArtefactInfo(
            compact_descr=int(row['compact_descr']),
            item_type=int(row.get('item_type', OPTIONAL_DEVICE_ITEM_TYPE)),
            artefact_id=int(row['artefact_id']),
            key=row['key'],
            user_string=row.get('user_string', ''),
            name=row.get('name', row['key']),
            price=row.get('price'),
            tags=list(row.get('tags', ())),
        )
    return artefacts


def save_artefact_db(artefacts, path):
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(artefact_db_to_json(artefacts), stream,
                  ensure_ascii=False, indent=1, sort_keys=True)
        stream.write('\n')


def load_artefact_db(path):
    with open(path, 'r', encoding='utf-8') as stream:
        return artefact_db_from_json(json.load(stream))


def ensure_artefact_db(path=DEFAULT_ARTEFACT_DB, client_dir=None,
                       rebuild=False):
    """Load the cached catalogue; build it from the client when missing."""
    if os.path.isfile(path) and not rebuild:
        return load_artefact_db(path)
    if client_dir is None:
        raise IOError(
            '配件目录缓存 %s 不存在, 且未提供客户端目录用于重建' % path)
    scripts_pkg = os.path.join(client_dir, 'res', 'packages', 'scripts.pkg')
    text_dir = os.path.join(client_dir, 'res', 'text')
    artefacts = build_artefact_db(scripts_pkg, text_dir)
    save_artefact_db(artefacts, path)
    return artefacts
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest trainer.tests.test_artefact_db -v`
Expected: PASS（LiveBuildTest 在本机有客户端时全部通过，否则 skip）

- [ ] **Step 5: 生成缓存 `trainer/artefact_db.json`**

Run: `python -c "from trainer.artefact_db import ensure_artefact_db; d = ensure_artefact_db(client_dir=r'C:\games\wot_0.9.22_cn', rebuild=True); print(len(d))"`
Expected: 输出 `49`，生成 `trainer/artefact_db.json`

---

### Task 2: `trainer/inventory_stock.py` — 库存读写与表格逻辑

**Files:**
- Create: `trainer/inventory_stock.py`
- Test: `trainer/tests/test_inventory_stock.py`

**Interfaces:**
- Consumes: `trainer.artefact_db.ArtefactInfo`、`OPTIONAL_DEVICE_ITEM_TYPE`；存档 dict（`json.load` 结果）
- Produces: `DEFAULT_STOCK_COUNT = 200`；`category_of(key) -> str`；`current_stock(state, compact_descr) -> int`；`stock_rows(state, artefacts) -> [dict(cd, key, name, price, category, current)]`；`plan_stock(state, artefacts, selected, count) -> [(ArtefactInfo, old, new)]`；`apply_stock(state, changes) -> int`；`stock_table_text(state, artefacts) -> str`

- [ ] **Step 1: 写失败测试** `trainer/tests/test_inventory_stock.py`

```python
"""Tests for trainer.inventory_stock depot editing logic."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.artefact_db import ArtefactInfo  # noqa: E402
from trainer.inventory_stock import (  # noqa: E402
    apply_stock, category_of, current_stock, plan_stock, stock_rows,
    stock_table_text)


def make_artefacts():
    return {
        5625: ArtefactInfo(
            compact_descr=5625, item_type=9, artefact_id=21,
            key='mediumCaliberTankRammer',
            user_string='#artefacts:mediumCaliberTankRammer/name',
            name='中型坦克炮输弹机', price=200000, tags=['rammer']),
        5881: ArtefactInfo(
            compact_descr=5881, item_type=9, artefact_id=23,
            key='mediumCaliberHowitzerRammer',
            user_string='#artefacts:mediumCaliberHowitzerRammer/name',
            name='中型火炮输弹机', price=300000, tags=['rammer']),
        11769: ArtefactInfo(
            compact_descr=11769, item_type=9, artefact_id=45,
            key='deluxRammer', user_string='#artefacts:deluxRammer/name',
            name='改进型装填系统', price=None, tags=['rammer', 'deluxe']),
    }


class CategoryTest(unittest.TestCase):

    def test_known_categories(self):
        self.assertEqual('输弹机', category_of('mediumCaliberTankRammer'))
        self.assertEqual('输弹机', category_of('deluxRammer'))
        self.assertEqual('通风系统', category_of('improvedVentilation_class2'))
        self.assertEqual('垂直稳定器', category_of('aimingStabilizer_Mk1'))
        self.assertEqual('炮控系统', category_of('deluxEnhancedAimDrives'))

    def test_unknown_falls_back(self):
        self.assertEqual('其他', category_of('somethingExotic'))


class StockTest(unittest.TestCase):

    def test_current_stock_handles_missing_owned(self):
        self.assertEqual(0, current_stock({}, 5625))
        self.assertEqual(0, current_stock({'owned': {}}, 5625))
        self.assertEqual(5, current_stock({'owned': {'9': {'5625': 5}}}, 5625))

    def test_stock_rows_sorted_with_counts(self):
        state = {'owned': {'9': {'11769': 7}}}
        rows = stock_rows(state, make_artefacts())
        self.assertEqual([5625, 5881, 11769], [row['cd'] for row in rows])
        self.assertEqual(0, rows[0]['current'])
        self.assertEqual(7, rows[2]['current'])
        self.assertEqual('输弹机', rows[0]['category'])

    def test_plan_stock_only_reports_changes(self):
        state = {'owned': {'9': {'5625': 200, '5881': 3}}}
        changes = plan_stock(state, make_artefacts(), {5625, 5881, 11769}, 200)
        self.assertEqual([(5881, 3, 200), (11769, 0, 200)],
                         [(i.compact_descr, old, new) for i, old, new in changes])

    def test_plan_stock_skips_unknown_compact_descr(self):
        self.assertEqual([], plan_stock({}, make_artefacts(), {999}, 200))

    def test_apply_stock_writes_and_drops_zero(self):
        state = {'owned': {'9': {'5625': 5, '5881': 8}}}
        artefacts = make_artefacts()
        changes = plan_stock(state, artefacts, {5625, 5881, 11769}, 0)
        # 5625 与 5881 变为 0 -> 删除键; 11769 已是 0 -> 不在变更里
        self.assertEqual(2, apply_stock(state, changes))
        self.assertEqual({}, state['owned']['9'])

    def test_apply_stock_creates_owned_when_missing(self):
        state = {}
        changes = plan_stock(state, make_artefacts(), {5625}, 200)
        self.assertEqual(1, apply_stock(state, changes))
        self.assertEqual(200, state['owned']['9']['5625'])


class TableTest(unittest.TestCase):

    def test_table_groups_by_category_with_counts(self):
        state = {'owned': {'9': {'5625': 5}}}
        text = stock_table_text(state, make_artefacts())
        self.assertIn('输弹机:', text)
        self.assertIn('中型坦克炮输弹机', text)
        self.assertIn('库存 5', text)
        self.assertIn('高级', text)  # deluxRammer 无 XML 单价
        # 组内按价格升序: 中型坦克炮(200000) 在 中型火炮(300000) 之前
        self.assertLess(text.index('中型坦克炮输弹机'),
                        text.index('中型火炮输弹机'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest trainer.tests.test_inventory_stock -v`
Expected: FAIL `ModuleNotFoundError: No module named 'trainer.inventory_stock'`

- [ ] **Step 3: 实现** `trainer/inventory_stock.py`

```python
"""Depot stock editing for optional devices (配件) in the offline garage save.

存档结构: ``state['owned'][str(item_type)][str(compact_descr)] = count``
(配件 item_type = 9)。数量为 0 的条目会被游戏丢弃
(``GarageState._set_owned``), 本模块沿用同一语义: 设为 0 即删除该键。
"""

from __future__ import annotations

from trainer.artefact_db import OPTIONAL_DEVICE_ITEM_TYPE

DEFAULT_STOCK_COUNT = 200  # 与客户端 bootstrap.OFFLINE_ARTEFACT_STOCK 一致

# 类别规则: (小写 needle, 中文类别)。按声明顺序匹配, 首个命中生效;
# tags 字段在部分条目上是压缩串, 不可靠, 因此按内部名归类。
_CATEGORY_RULES = (
    ('rammer', '输弹机'),
    ('ventilation', '通风系统'),
    ('aimdrives', '炮控系统'),
    ('stabilizer', '垂直稳定器'),
    ('optics', '光学观察镜'),
    ('stereoscope', '炮队镜'),
    ('camouflagenet', '伪装网'),
    ('toolbox', '工具箱'),
    ('lining', '防崩落内衬'),
    ('suspension', '增强悬挂'),
    ('torsions', '增强扭杆'),
    ('springs', '悬挂弹簧/板簧'),
    ('vertical', '悬挂弹簧/板簧'),
    ('horizontal', '悬挂弹簧/板簧'),
    ('wetcombatpack', '“水套”'),
    ('carbondioxide', 'CO2油箱'),
    ('filtercyclone', '燃油滤清器'),
    ('grousers', '履带齿'),
    ('levers', '增强操纵杆'),
    ('washers', '增强垫圈'),
)


def category_of(key):
    """配件内部名 → 中文类别 (表格分组用)。"""
    lowered = str(key).lower()
    for needle, label in _CATEGORY_RULES:
        if needle in lowered:
            return label
    return '其他'


def _owned_devices(state):
    owned = state.get('owned')
    if not isinstance(owned, dict):
        return {}
    items = owned.get(str(OPTIONAL_DEVICE_ITEM_TYPE))
    return items if isinstance(items, dict) else {}


def current_stock(state, compact_descr):
    """仓库中该配件的当前数量 (不含已安装在车上的)。"""
    try:
        return int(_owned_devices(state).get(str(int(compact_descr)), 0))
    except (TypeError, ValueError):
        return 0


def stock_rows(state, artefacts):
    """每个已知配件一行的勾选列表数据, 按 compact descriptor 排序。"""
    rows = []
    for compact_descr in sorted(artefacts):
        info = artefacts[compact_descr]
        rows.append({
            'cd': compact_descr,
            'key': info.key,
            'name': info.name,
            'price': info.price,
            'category': category_of(info.key),
            'current': current_stock(state, compact_descr),
        })
    return rows


def plan_stock(state, artefacts, selected, count):
    """``[(info, old, new)]``: 选中配件中数量会有变化的条目。"""
    changes = []
    for compact_descr in sorted(int(cd) for cd in selected):
        info = artefacts.get(compact_descr)
        if info is None:
            continue
        old = current_stock(state, compact_descr)
        if old != count:
            changes.append((info, old, count))
    return changes


def apply_stock(state, changes):
    """按 plan_stock 的结果写入库存; 返回写入的条目数。"""
    owned = state.setdefault('owned', {})
    items = owned.setdefault(str(OPTIONAL_DEVICE_ITEM_TYPE), {})
    for info, _old, new in changes:
        key = str(info.compact_descr)
        if new > 0:
            items[key] = int(new)
        else:
            items.pop(key, None)
    return len(changes)


def stock_table_text(state, artefacts):
    """分组列表表格: 类别分组, 组内按价格(=等级)升序, 显示当前库存。"""
    groups = {}
    for compact_descr in sorted(artefacts):
        info = artefacts[compact_descr]
        groups.setdefault(category_of(info.key), []).append(info)
    rule_order = [label for _needle, label in _CATEGORY_RULES] + ['其他']
    lines = []
    for category in sorted(groups, key=rule_order.index):
        lines.append('%s:' % category)
        entries = sorted(groups[category], key=lambda info: (
            info.price is None, info.price or 0, info.key))
        width = max(len(info.name) for info in entries)
        for info in entries:
            price = '高级' if info.price is None else str(info.price)
            lines.append('  %s  单价 %-7s 库存 %d' % (
                info.name.ljust(width), price,
                current_stock(state, info.compact_descr)))
        lines.append('')
    return '\n'.join(lines).rstrip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest trainer.tests.test_inventory_stock -v`
Expected: PASS

---

### Task 3: `tui.py` 顶层菜单重构（车组管理子菜单）

**Files:**
- Modify: `trainer/tui.py`（`main` 拆为顶层循环 + `crew_menu`；新增 `_load_state`）
- Test: `trainer/tests/test_tui.py`（更新 ScriptedFlowTest 按键序列）

**Interfaces:**
- Produces: `crew_menu(state, vehicles, templates, slot, garage_path) -> templates`（原主循环内容，菜单项重排为 1-6 + q 返回）；`main` 顶层循环（1 车组管理 / 2 库存管理 / 3 选择存档 / 4 恢复备份 / q 退出）；库存管理项暂为占位（Task 4 实现）

- [ ] **Step 1: 先更新测试为新的两级菜单按键序列**（`test_tui.py` ScriptedFlowTest 各用例）

修改点（其余代码不动）：
- `test_preview_then_quit`: `['3', 'esc', 'q']` → `['1', '3', 'esc', 'q', 'q']`
- `test_apply_overwrites_after_confirmation`: `['1', ' ', 'enter', '4', 'o', 'esc', 'y', 'esc', 'q']` → 前加 `'1',`，末尾加 `'q'`：`['1', '1', ' ', 'enter', '4', 'o', 'esc', 'y', 'esc', 'q', 'q']`
- `test_apply_cancelled_by_user_writes_nothing`: → `['1', '1', 'enter', '4', 'esc', 'n', 'esc', 'q', 'q']`
- `test_apply_skips_when_user_chooses_skip`: → `['1', '1', ' ', 'enter', '4', 's', 'esc', 'q', 'q']`
- `test_template_menu_opens_and_quits`: → `['1', '5', 'esc', 'q', 'q']`
- `test_quit_immediately`: `['q']` 不变；断言改为 `self.assertIn('车库助手', screen)`，并新增 `self.assertNotIn('白板', screen)`（顶层不渲染车辆行）
- `test_switch_slot_reloads_garage`: 存档选择移到顶层菜单项 3；为看到切换前后的"已选"计数，序列为 `['1', 'q', '3', 'down', 'enter', '1', 'q', 'q']`；断言不变（'选择要编辑的存档'、'已选 1, 可选 1'、'已选 0, 可选 0'、'存档: Career-Mode'）

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest trainer.tests.test_tui -v`
Expected: 多个 ScriptedFlowTest FAIL（菜单层级还是旧的）

- [ ] **Step 3: 重构 `tui.py`**

3a. 顶部 import 增加（保持现有 import 不变，追加）：

```python
from trainer.apply_crew_templates import (
    DEFAULT_CLIENT_DIR, DEFAULT_SLOT, DEFAULT_TEMPLATES, DEFAULT_VEHICLE_DB,
    CLIENT_PROCESSES, LEGACY_GARAGE, TemplateError, client_running,
    list_save_slots, load_templates, normalize_combo, plan_vehicle,
    save_templates, slot_garage_path, write_garage)
from trainer.artefact_db import DEFAULT_ARTEFACT_DB, ensure_artefact_db
from trainer.inventory_stock import (
    DEFAULT_STOCK_COUNT, apply_stock, plan_stock, stock_rows,
    stock_table_text)
```

（原 `from trainer.apply_crew_templates import (...)` 块中补 `DEFAULT_CLIENT_DIR`；其余两段为新增。）

3b. 在 `# ---- main flow ---` 区段，把 `main()` 替换为 `_load_state` + 新 `main` + `crew_menu`：

```python
def _load_state(garage_path):
    with open(garage_path, 'rb') as stream:
        return json.load(stream)


def crew_menu(state, vehicles, templates, slot, garage_path):
    """车组管理子菜单 (原主循环); 返回可能更新过的模板。"""
    rows = vehicle_rows(state, vehicles)
    selected = {row['key'] for row in rows if row['ready'] > 0}
    filters = {'nations': set(), 'tiers': set(), 'classes': set(), 'text': ''}
    while True:
        ready_total = sum(1 for row in rows if row['ready'] > 0)
        effective = effective_selection(rows, selected, filters)
        subtitle = '存档: %s\n%s' % (slot or '(自定义路径)', garage_path)
        summary = _filter_summary(filters)
        if summary:
            subtitle += '\n筛选中: ' + summary
        choice = menu('乘员技能模板工具 — 车组管理', [
            ('1', '选择车辆 (已选 %d, 可选 %d)' % (len(effective), ready_total)),
            ('2', '设置筛选 (系别/等级/车型)'),
            ('3', '预览变更'),
            ('4', '应用写入'),
            ('5', '编辑技能模板'),
            ('6', '技能对照表'),
            ('q', '返回主菜单'),
        ], subtitle=subtitle)
        if choice in (None, 'q'):
            return templates
        if choice == '1':
            result, filters = vehicle_picker(rows, selected, filters)
            if result is not None:
                selected = result
        elif choice == '2':
            filters = filter_menu(filters)
        elif choice == '3':
            text, count = build_report(state, vehicles, templates, effective)
            text_view('预览变更 (dry-run)', text)
        elif choice == '4':
            conflicted = sum(
                row['total'] - row['ready'] - row['broken']
                for row in rows if row['key'] in effective)
            overwrite = False
            if conflicted:
                decision = menu(
                    '提示: 选中范围内有 %d 个乘员已学技能' % conflicted, [
                        ('s', '仅写白板乘员 (跳过他们, 默认)'),
                        ('o', '覆盖: 已学技能的乘员也按模板重写'),
                        ('x', '取消'),
                    ], subtitle='已学技能的乘员默认不会被改动')
                if decision in (None, 'x'):
                    continue
                overwrite = decision == 'o'
            text, count = build_report(state, vehicles, templates,
                                       effective, overwrite)
            if count == 0:
                text_view('应用写入', '选中车辆没有可应用的改动。')
                continue
            text_view('应用前请确认以下变更 (dry-run)', text)
            question = '将对 %d 个乘员槽位写入模板 (筛选后 %d 辆车), 确认写入?' % (
                count, len(effective))
            if overwrite:
                question = ('覆盖模式! %d 个已学技能的乘员将被重写。' %
                            conflicted) + question
            if confirm(question):
                apply_changes(state, vehicles, templates, effective,
                              garage_path, overwrite)
            else:
                text_view('已取消', '未做任何修改。')
                # 写入后刷新行状态与默认选择 (保持原实现: 仅在取消分支)
                rows = vehicle_rows(state, vehicles)
                selected &= {row['key'] for row in rows if row['ready'] > 0}
        elif choice == '5':
            templates = template_menu(DEFAULT_TEMPLATES)
        elif choice == '6':
            text_view('技能对照表 (内部名 → 中文名)', skill_table_text())


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        slot = None  # 显式路径, 不属于任何槽位
        garage_path = args[0]
    else:
        slot = DEFAULT_SLOT
        garage_path = slot_garage_path(slot)
    if not os.path.isfile(garage_path):
        print('找不到存档: %s' % garage_path)
        if slot == DEFAULT_SLOT and os.path.isfile(LEGACY_GARAGE):
            print('提示: 发现旧版存档 %s, 但游戏现在使用 saves\\<存档>\\ 目录;'
                  ' 请先启动一次游戏完成迁移。' % LEGACY_GARAGE)
        return 1
    import argparse
    from trainer.apply_crew_templates import _ensure_vehicle_db
    templates = load_templates(DEFAULT_TEMPLATES)
    vehicles = _ensure_vehicle_db(argparse.Namespace(
        vehicle_db=DEFAULT_VEHICLE_DB, rebuild_db=False,
        client_dir=DEFAULT_CLIENT_DIR))
    artefacts = ensure_artefact_db(
        DEFAULT_ARTEFACT_DB, client_dir=DEFAULT_CLIENT_DIR)
    state = _load_state(garage_path)

    with Screen():
        while True:
            choice = menu('车库助手 (Trainer)', [
                ('1', '车组管理  按模板设置乘员技能'),
                ('2', '库存管理  配件库存数量'),
                ('3', '选择存档 (当前: %s)' % (slot or '(自定义路径)')),
                ('4', '恢复备份'),
                ('q', '退出'),
            ], subtitle='存档: %s\n%s' % (slot or '(自定义路径)', garage_path))
            if choice in (None, 'q'):
                return 0
            if choice == '1':
                templates = crew_menu(state, vehicles, templates, slot,
                                      garage_path)
            elif choice == '2':
                inventory_menu(state, artefacts, garage_path)
            elif choice == '3':
                new_slot = slot_menu(slot)
                if new_slot is None or new_slot == slot:
                    continue
                new_path = slot_garage_path(new_slot)
                if not os.path.isfile(new_path):
                    text_view('错误', '该存档还没有 garage_state.json:\n%s\n\n'
                              '请先以此存档启动游戏进一次车库。' % new_path)
                    continue
                slot = new_slot
                garage_path = new_path
                state = _load_state(garage_path)
            elif choice == '4':
                backup_menu(garage_path)
                state = _load_state(garage_path)
```

注意：原 '7'/'8' 分支逻辑原样提升到顶层 '4'/'3'；'4' 分支末尾的行刷新保持原实现位置（仅在取消分支内，含原注释）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest trainer.tests.test_tui -v`
Expected: PASS（`inventory_menu` 尚未实现，需在 Task 4 前给 `main` 的 '2' 分支临时占位或先行完成 Task 4 的函数骨架——推荐：本任务先放骨架 `def inventory_menu(state, artefacts, garage_path): text_view('库存管理', '尚未实现')`，Task 4 替换为完整实现）

---

### Task 4: `tui.py` 库存管理子菜单（勾选 + 数量 + 预览 + 写入 + 库存一览）

**Files:**
- Modify: `trainer/tui.py`（新增 `artefact_row_matches/format_artefact_row/artefact_picker/number_input/stock_report`，替换 `inventory_menu` 骨架）
- Test: `trainer/tests/test_tui.py`（新增库存流程用例）

**Interfaces:**
- Consumes: Task 2 的 `stock_rows/plan_stock/apply_stock/stock_table_text/DEFAULT_STOCK_COUNT`；Task 3 顶层循环
- Produces: `artefact_picker(rows, selected) -> set | None`；`number_input(title, current, maximum=9999) -> int | None`；`stock_report(changes, count) -> str`；`inventory_menu(state, artefacts, garage_path) -> None`

- [ ] **Step 1: 写失败测试**（追加到 `test_tui.py` 的 ScriptedFlowTest，并加 helper）

```python
def make_state_with_stock():
    state = make_state(NO_SKILL_BLOB)
    state['owned'] = {'9': {'5625': 5}}
    return state
```

```python
    def _unlink_with_backups(self, path):
        import glob as glob_module
        for candidate in [path] + glob_module.glob(
                path + '.trainer-backup-*'):
            os.unlink(candidate)

    def test_inventory_apply_writes_stock(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 库存管理 → 预览 → 应用 → 看完清单 → y 确认 → 完成 → 返回 → 退出
            result, screen = self._run_main(
                ['2', '3', 'esc', '4', 'esc', 'y', 'esc', 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            self._unlink_with_backups(path)
        self.assertEqual(0, result)
        self.assertIn('库存管理', screen)
        self.assertIn('中型坦克炮输弹机', screen)
        # 默认全选: 已有 5 的改为 200, 没有的(deluxRammer)补为 200
        self.assertEqual(200, written['owned']['9']['5625'])
        self.assertEqual(200, written['owned']['9']['11769'])

    def test_inventory_custom_count(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 目标数量改为 50 → 应用 → 确认
            result, screen = self._run_main(
                ['2', '2', '5', '0', 'enter', '4', 'esc', 'y', 'esc',
                 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            self._unlink_with_backups(path)
        self.assertEqual(0, result)
        self.assertIn('目标数量', screen)
        self.assertEqual(50, written['owned']['9']['5625'])

    def test_inventory_deselect_all_has_no_changes(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 选择配件 → N 取消全部 → 应用 → 无改动提示
            result, screen = self._run_main(
                ['2', '1', 'n', 'enter', '4', 'esc', 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('选择配件', screen)
        self.assertIn('没有可应用的改动', screen)
        self.assertEqual(5, written['owned']['9']['5625'])

    def test_inventory_stock_table(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            result, screen = self._run_main(
                ['2', '5', 'esc', 'q', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('库存一览', screen)
        self.assertIn('输弹机:', screen)
        self.assertIn('库存 5', screen)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest trainer.tests.test_tui.ScriptedFlowTest -v`
Expected: 4 个新用例 FAIL（库存菜单还是占位/骨架）

- [ ] **Step 3: 实现**（追加到 `tui.py` 的 `# ---- main flow ---` 之前或之后均可，保持与 vehicle_picker 相邻区域的风格）

```python
# ---- inventory (库存管理) -------------------------------------------------

def artefact_row_matches(row, text):
    text = (text or '').lower()
    if not text:
        return True
    return text in row['name'].lower() or text in row['key'].lower()


def format_artefact_row(row, chosen, width):
    mark = '[x]' if chosen else '[ ]'
    return _fit(' %s %s  [%s]  库存:%d' % (
        mark, row['name'], row['key'], row['current']), width)


def artefact_picker(rows, selected):
    """配件勾选列表 (增量搜索)。Esc 放弃改动, Enter 确认。"""
    selected = set(selected)
    index = 0
    top = 0
    text = ''
    while True:
        visible = [row for row in rows if artefact_row_matches(row, text)]
        index = max(0, min(index, len(visible) - 1))
        width, height = terminal_size()
        body = height - 7
        if index < top:
            top = index
        elif index >= top + body:
            top = index - body + 1
        chosen = sum(1 for row in visible if row['cd'] in selected)
        lines = [BOLD + '选择配件 (筛选结果 %d 种, 其中已选 %d / 全部 %d)' % (
            len(visible), chosen, len(rows)) + RESET]
        lines.append(DIM + '过滤: %s' % (text or '(输入即搜索)') + RESET)
        lines.append('')
        for i, row in enumerate(visible[top:top + body]):
            actual = top + i
            line = format_artefact_row(row, row['cd'] in selected, width - 2)
            lines.append((REVERSE if actual == index else '') + line +
                         (RESET if actual == index else ''))
        lines.append('')
        lines.append(DIM + '空格 勾选  A 全选结果  N 取消结果  / 清空搜索  '
                           'Enter 确认  Esc 取消  (直接输入即搜索)' + RESET)
        draw(lines)
        pressed = read_key()
        if pressed == 'up':
            index -= 1
        elif pressed == 'down':
            index += 1
        elif pressed == 'pageup':
            index -= body
        elif pressed == 'pagedown':
            index += body
        elif pressed == 'home':
            index = 0
        elif pressed == 'end':
            index = len(visible) - 1
        elif pressed == ' ' and visible:
            selected.symmetric_difference_update({visible[index]['cd']})
            if index < len(visible) - 1:
                index += 1
        elif pressed in ('a', 'A') and not text:
            selected.update(row['cd'] for row in visible)
        elif pressed in ('n', 'N') and not text:
            selected.difference_update(row['cd'] for row in visible)
        elif pressed == '/':
            text = ''
            index = top = 0
        elif pressed == 'backspace':
            text = text[:-1]
            index = top = 0
        elif pressed == 'enter':
            return selected
        elif pressed == 'esc':
            return None
        elif len(pressed) == 1 and pressed.isprintable():
            text += pressed
            index = top = 0


def number_input(title, current, maximum=9999):
    """数字输入框: Enter 确认 (空输入保持 current), Esc 取消。"""
    buffer = ''
    while True:
        width, height = terminal_size()
        lines = [BOLD + title + RESET, '']
        shown = buffer if buffer else '(回车保持 %d)' % current
        lines.append('  数量: ' + shown)
        lines.append('')
        lines.append(DIM + '0-9 输入  Backspace 删除  Enter 确认  Esc 取消'
                     + RESET)
        draw(lines)
        pressed = read_key()
        if pressed == 'enter':
            if not buffer:
                return current
            return max(0, min(maximum, int(buffer)))
        if pressed == 'esc':
            return None
        if pressed == 'backspace':
            buffer = buffer[:-1]
        elif len(pressed) == 1 and pressed.isdigit() and len(buffer) < 4:
            buffer += pressed


def stock_report(changes, count):
    lines = []
    for info, old, new in changes:
        lines.append('%s [%s]: %d → %d' % (info.name, info.key, old, new))
    lines.append('')
    lines.append('共 %d 种配件的库存将设为 %d' % (len(changes), count))
    return '\n'.join(lines)


def inventory_menu(state, artefacts, garage_path):
    """库存管理: 选择配件 (默认全选) → 设定数量 → 预览 → 写入。"""
    selected = set(artefacts)  # 修改范围默认全选
    count = DEFAULT_STOCK_COUNT
    while True:
        rows = stock_rows(state, artefacts)
        choice = menu('库存管理 — 配件库存', [
            ('1', '选择配件 (已选 %d / 共 %d)' % (len(selected), len(rows))),
            ('2', '目标数量 (当前: %d)' % count),
            ('3', '预览变更'),
            ('4', '应用写入'),
            ('5', '库存一览 (按类别/价格排序)'),
            ('q', '返回主菜单'),
        ], subtitle='库存为账户级, 作用于整个存档; 数量 0 表示删除该记录')
        if choice in (None, 'q'):
            return
        if choice == '1':
            result = artefact_picker(rows, selected)
            if result is not None:
                selected = result
        elif choice == '2':
            value = number_input(
                '目标数量 (默认 %d)' % DEFAULT_STOCK_COUNT, count)
            if value is not None:
                count = value
        elif choice == '3':
            changes = plan_stock(state, artefacts, selected, count)
            if changes:
                text_view('预览变更 (dry-run)', stock_report(changes, count))
            else:
                text_view('预览变更',
                          '选中配件的库存均已等于 %d, 无需改动。' % count)
        elif choice == '4':
            changes = plan_stock(state, artefacts, selected, count)
            if not changes:
                text_view('应用写入', '选中配件没有可应用的改动。')
                continue
            running = client_running()
            if running:
                text_view('错误', '客户端正在运行 (%s), 请先关闭游戏再写入。' %
                          ', '.join(running))
                continue
            text_view('应用前请确认以下变更 (dry-run)',
                      stock_report(changes, count))
            if not confirm('将把 %d 种配件的库存设为 %d, 确认写入?' % (
                    len(changes), count)):
                text_view('已取消', '未做任何修改。')
                continue
            apply_stock(state, changes)
            backup = write_garage(garage_path, state)
            text_view('完成', '已写入 %d 种配件的库存。\n备份: %s\n\n'
                      '请启动客户端进车库确认。' % (
                          len(changes), os.path.basename(backup)))
        elif choice == '5':
            text_view('库存一览 (仓库数量, 不含已安装在车上的)',
                      stock_table_text(state, artefacts))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest trainer.tests.test_tui -v`
Expected: PASS（全部，含 Task 3 更新过的用例）

- [ ] **Step 5: 全量回归**

Run: `python -m unittest discover -s trainer.tests -t .`
Expected: 全部 PASS（含既有 apply_crew_templates/tankman_codec/skills_db/templates_io/vehicle_db 测试）

---

### Task 5: 文档更新

**Files:**
- Modify: `trainer/README.md`

- [ ] **Step 1: 更新 README**

- 标题/首段：从"乘员技能模板工具"改为车库助手，含车组管理与库存管理两个功能
- TUI 菜单图替换为两级菜单：

```
主菜单 (车库助手)
 ├─ 1 车组管理   原乘员技能模板全部功能 (选择车辆/筛选/预览/应用/编辑模板/对照表)
 ├─ 2 库存管理   配件库存数量
 │    ├─ 1 选择配件   勾选列表(同选择车辆操作), 默认全选 49 种配件
 │    ├─ 2 目标数量   数字输入, 默认 200, 0 表示删除该库存记录
 │    ├─ 3 预览变更   dry-run
 │    ├─ 4 应用写入   清单确认 → 进程检测 → 备份 → 原子写入
 │    └─ 5 库存一览   按类别分组、按价格(等级)排序的当前库存表格
 ├─ 3 选择存档
 ├─ 4 恢复备份
 └─ q 退出
```

- 补充说明：库存为账户级（`owned['9']`），不含已安装在车上的配件；配件目录首次运行从客户端 `optional_devices.xml` 生成 `artefact_db.json` 缓存
- 结构表新增两行：`artefact_db.py`（配件目录提取）、`inventory_stock.py`（库存读写/表格逻辑）

- [ ] **Step 2: 最终验证**

Run: `python -m unittest discover -s trainer.tests -t .`
Expected: 全部 PASS

---

## Self-Review 记录

- 规格覆盖：顶层菜单重组(决策4)→Task 3；库存管理/默认全选勾选(决策1,2)→Task 2+4；自定义数量默认200(决策3)→Task 4 number_input；库存表格(决策5)→Task 2 stock_table_text + Task 4 菜单项5
- 类型一致性：`ArtefactInfo` 字段在 Task 1/2/4 间一致（compact_descr/item_type/artefact_id/key/user_string/name/price/tags）；`stock_rows` 行键 cd/key/name/price/category/current 与 picker/表格使用一致
- 已知取舍：中文全角字符在等宽终端占两格，ljust 对齐近似即可（与现有 format_row 一致）；tags 压缩串不可靠故类别按内部名归类
