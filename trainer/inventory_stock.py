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
