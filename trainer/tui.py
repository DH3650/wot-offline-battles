"""Full-screen TUI for the crew skill trainer (stdlib only).

Uses the Windows console's virtual-terminal mode for drawing and ``msvcrt``
for key input — no third-party packages.  All garage logic lives in
``trainer.apply_crew_templates`` / ``trainer.tankman_codec``; this module is
only presentation and interaction.

Run from the repository root::

    python -m trainer.tui
"""

from __future__ import annotations

import base64
import ctypes
import glob
import json
import msvcrt
import os
import re
import shutil
import sys

from trainer.apply_crew_templates import (
    DEFAULT_CLIENT_DIR, DEFAULT_SLOT, DEFAULT_TEMPLATES, DEFAULT_VEHICLE_DB,
    CLIENT_PROCESSES, LEGACY_GARAGE, TemplateError, client_running,
    list_save_slots, load_templates, normalize_combo, plan_vehicle,
    save_templates, slot_garage_path, write_garage)
from trainer.artefact_db import DEFAULT_ARTEFACT_DB, ensure_artefact_db
from trainer.inventory_stock import (
    DEFAULT_STOCK_COUNT, apply_stock, plan_stock, stock_rows,
    stock_table_text)
from trainer.skills_db import (
    ROLE_NAMES_CN, SKILL_NAMES_CN, SKILLS_BY_ROLES, describe_role,
    describe_skill, skills_for_roles)
from trainer.tankman_codec import TankmanFormatError, parse_tankman
from trainer.vehicle_db import (
    CLASS_NAMES_CN, NATION_NAMES_CN, VEHICLE_CLASS_TAGS)
from trainer import gun_marks
from trainer import vehicle_modifications
from launcher import vehicle_overlays

ESC = '\x1b'
ALT_SCREEN_ON = ESC + '[?1049h'
ALT_SCREEN_OFF = ESC + '[?1049l'
HIDE_CURSOR = ESC + '[?25l'
SHOW_CURSOR = ESC + '[?25h'
CLEAR = ESC + '[2J' + ESC + '[H'
RESET = ESC + '[0m'
REVERSE = ESC + '[7m'
DIM = ESC + '[2m'
BOLD = ESC + '[1m'
BLUE = ESC + '[34m'
PURPLE = ESC + '[35m'
GOLD = ESC + '[33m'
GREEN = ESC + '[32m'

VEHICLE_CLASSES = VEHICLE_CLASS_TAGS
_ANSI_STYLE = re.compile(r'\x1b\[[0-9;]*m')


# ---- console plumbing -----------------------------------------------------

def _enable_virtual_terminal():
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x4)
    except (AttributeError, OSError):
        pass
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass


class Screen(object):
    """Context manager: enter alt screen, restore everything on exit."""

    def __enter__(self):
        _enable_virtual_terminal()
        sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
        sys.stdout.flush()
        return self

    def __exit__(self, *exc_info):
        sys.stdout.write(RESET + SHOW_CURSOR + ALT_SCREEN_OFF)
        sys.stdout.flush()
        return False


def read_key():
    """Return a logical key name; reads one (possibly two-byte) keypress."""
    ch = msvcrt.getwch()
    if ch in ('\x00', '\xe0'):
        code = msvcrt.getwch()
        return {
            'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right',
            'I': 'pageup', 'Q': 'pagedown', 'G': 'home', 'O': 'end',
        }.get(code, 'unknown')
    if ch == '\r':
        return 'enter'
    if ch == ESC:
        return 'esc'
    if ch == '\x08':
        return 'backspace'
    if ch == '\t':
        return 'tab'
    return ch


def terminal_size():
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 120, 40


def draw(lines):
    sys.stdout.write(CLEAR + '\r\n'.join(lines))
    sys.stdout.flush()


def _fit(text, width):
    text = str(text)
    return text if len(text) <= width else text[:max(0, width - 1)] + '…'


def _fit_ansi(text, width):
    """Fit colored text without splitting an ANSI sequence or leaking style."""
    result = []
    visible = 0
    position = 0
    for match in _ANSI_STYLE.finditer(text):
        plain = text[position:match.start()]
        room = max(0, width - visible)
        if len(plain) > room:
            result.append(plain[:max(0, room - 1)] + ('…' if room else ''))
            return ''.join(result) + RESET
        result.extend((plain, match.group(0)))
        visible += len(plain)
        position = match.end()
    tail = text[position:]
    room = max(0, width - visible)
    if len(tail) > room:
        result.append(tail[:max(0, room - 1)] + ('…' if room else ''))
        return ''.join(result) + RESET
    result.append(tail)
    return ''.join(result)


# ---- generic widgets ------------------------------------------------------

def menu(title, items, subtitle=''):
    """Vertical menu; ``items`` = [(key, label)]. Returns key or None (Esc)."""
    index = 0
    while True:
        width, height = terminal_size()
        lines = [BOLD + title + RESET]
        if subtitle:
            lines.append(DIM + subtitle + RESET)
        lines.append('')
        for i, (key, label) in enumerate(items):
            prefix = '▶ ' if i == index else '  '
            line = prefix + label
            lines.append((REVERSE if i == index else '') + _fit_ansi(
                line, width - 1) + (RESET if i == index else ''))
        lines.append('')
        lines.append(DIM + '↑↓ 移动  Enter 确认  Esc 返回' + RESET)
        draw(lines)
        pressed = read_key()
        if pressed == 'up':
            index = (index - 1) % len(items)
        elif pressed == 'down':
            index = (index + 1) % len(items)
        elif pressed == 'enter':
            return items[index][0]
        elif pressed == 'esc':
            return None
        elif len(pressed) == 1:
            for i, (key, unused) in enumerate(items):
                if str(key).lower() == pressed.lower():
                    return key
            index = 0


def text_view(title, text):
    """Scrollable read-only viewer. Returns on Esc/Enter/q."""
    all_lines = text.splitlines() or ['(空)']
    top = 0
    while True:
        width, height = terminal_size()
        body = height - 4
        top = max(0, min(top, max(0, len(all_lines) - body)))
        lines = [BOLD + _fit(title, width - 1) + RESET, '']
        lines.extend(_fit(line, width - 1)
                     for line in all_lines[top:top + body])
        lines.append('')
        lines.append(DIM + '↑↓/PgUp/PgDn 滚动 (%d/%d)  Esc 返回' % (
            min(top + body, len(all_lines)), len(all_lines)) + RESET)
        draw(lines)
        pressed = read_key()
        if pressed in ('esc', 'enter', 'q'):
            return
        if pressed == 'up':
            top -= 1
        elif pressed == 'down':
            top += 1
        elif pressed == 'pageup':
            top -= body
        elif pressed == 'pagedown':
            top += body
        elif pressed == 'home':
            top = 0
        elif pressed == 'end':
            top = len(all_lines)


def confirm(question):
    """Yes/No prompt; returns True only on explicit yes."""
    width, height = terminal_size()
    draw(['', BOLD + question + RESET, '',
          '按 Y 确认, 其他键取消'])
    return read_key().lower() == 'y'


# ---- garage-facing helpers (pure, unit-testable) ---------------------------

def crew_status(record):
    """(skill-less slots, total slots, broken slots) of one saved vehicle."""
    crew = record.get('crew') or {}
    ready = broken = 0
    for encoded in crew.values():
        try:
            tankman = parse_tankman(base64.b64decode(encoded))
        except (TankmanFormatError, ValueError):
            broken += 1
            continue
        if not tankman.skills:
            ready += 1
    return ready, len(crew), broken


def vehicle_rows(state, vehicles):
    """One display row per saved vehicle, sorted by nation/tier/name."""
    rows = []
    for key in sorted(state.get('vehicles', {}), key=int):
        info = vehicles.get(int(key))
        record = state['vehicles'][key]
        ready, total, broken = crew_status(record)
        rows.append({
            'key': key,
            'info': info,
            'ready': ready,
            'total': total,
            'broken': broken,
        })
    rows.sort(key=lambda row: (
        row['info'].nation_id if row['info'] else 99,
        -(row['info'].tier if row['info'] else 0),
        row['info'].name if row['info'] else row['key']))
    return rows


def row_matches(row, nations, tiers, classes, text):
    info = row['info']
    if info is None:
        return False
    if nations and info.nation not in nations:
        return False
    if tiers and info.tier not in tiers:
        return False
    if classes and info.clazz not in classes:
        return False
    if text:
        wanted = text.lower()
        haystacks = (info.key.lower(), info.name.lower(),
                     info.user_string.lower())
        if not any(wanted in hay for hay in haystacks):
            return False
    return True


def effective_selection(rows, selected, filters):
    """勾选集合 ∩ 当前筛选: 预览/写入只对这部分车辆生效。"""
    return {row['key'] for row in rows
            if row['key'] in selected and row_matches(
                row, filters['nations'], filters['tiers'],
                filters['classes'], filters.get('text', ''))}


def format_row(row, selected, width):
    info = row['info']
    if info is None:
        return ' %s [%s] 未知车型 typeCD=%s' % (
            '[x]' if selected else '[ ]', '!' * 1, row['key'])
    mark = '[x]' if selected else '[ ]'
    status = '白板 %d/%d' % (row['ready'], row['total'])
    if row['ready'] == 0:
        status = '已有技能'
    nation = NATION_NAMES_CN.get(info.nation, info.nation)
    clazz = CLASS_NAMES_CN.get(info.clazz, info.clazz)
    return _fit(
        ' %s %-3s %2d级 %-5s %-18s %s' % (
            mark, nation, info.tier, clazz, info.name, status), width)


# ---- screens ----------------------------------------------------------------

def vehicle_picker(rows, selected, filters):
    """Checkbox list with incremental search. Returns (selected, filters).

    Works on a copy of ``selected`` so Esc truly discards changes.
    """
    selected = set(selected)
    index = 0
    top = 0
    text = filters.get('text', '')
    while True:
        visible = [row for row in rows if row_matches(
            row, filters['nations'], filters['tiers'], filters['classes'],
            text)]
        index = max(0, min(index, len(visible) - 1))
        width, height = terminal_size()
        body = height - 7
        if index < top:
            top = index
        elif index >= top + body:
            top = index - body + 1
        chosen = sum(1 for row in visible if row['key'] in selected)
        lines = [BOLD + '选择车辆 (筛选结果 %d 辆, 其中已选 %d / 全部 %d)' % (
            len(visible), chosen, len(rows)) + RESET]
        lines.append(DIM + '过滤: %s' % (text or '(输入即搜索)') + RESET)
        lines.append('')
        for i, row in enumerate(visible[top:top + body]):
            actual = top + i
            line = format_row(row, row['key'] in selected, width - 2)
            lines.append((REVERSE if actual == index else '') + line +
                         (RESET if actual == index else ''))
        lines.append('')
        lines.append(DIM + '空格 勾选  A 全选结果  N 取消结果  F 筛选  '
                           '/ 清空搜索  Enter 确认  Esc 取消  (直接输入小写'
                           '字母/数字即搜索)' + RESET)
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
            key = visible[index]['key']
            selected.symmetric_difference_update({key})
            if index < len(visible) - 1:
                index += 1
        elif pressed in ('a', 'A') and not text:
            selected.update(row['key'] for row in visible)
        elif pressed in ('n', 'N') and not text:
            selected.difference_update(row['key'] for row in visible)
        elif pressed == '/':
            text = ''
            index = top = 0
        elif pressed in ('f', 'F') and not text:
            filters = filter_menu(filters)
        elif pressed == 'backspace':
            text = text[:-1]
            index = top = 0
        elif pressed == 'enter':
            filters['text'] = text
            return selected, filters
        elif pressed == 'esc':
            return None, filters
        elif len(pressed) == 1 and pressed.isprintable():
            text += pressed
            index = top = 0


def filter_menu(filters):
    """Checkbox sub-menus for nation / tier / class filters."""
    while True:
        nations = ', '.join(sorted(
            NATION_NAMES_CN.get(n, n) for n in filters['nations'])) or '(全部)'
        tiers = ', '.join(str(t) for t in sorted(filters['tiers'])) or '(全部)'
        classes = ', '.join(sorted(
            CLASS_NAMES_CN.get(c, c) for c in filters['classes'])) or '(全部)'
        choice = menu('设置筛选', [
            ('n', '系别: %s' % nations),
            ('t', '等级: %s' % tiers),
            ('c', '车型: %s' % classes),
            ('x', '清空全部筛选'),
        ])
        if choice is None:
            return filters
        if choice == 'x':
            filters['nations'].clear()
            filters['tiers'].clear()
            filters['classes'].clear()
            continue
        if choice == 'n':
            _toggle_set('选择系别 (空格切换, Enter 确认)',
                        [(nation, NATION_NAMES_CN.get(nation, nation))
                         for nation in
                         ('ussr', 'germany', 'usa', 'china', 'france', 'uk',
                          'japan', 'czech', 'sweden', 'poland')],
                        filters['nations'])
        elif choice == 't':
            _toggle_set('选择等级 (空格切换, Enter 确认)',
                        [(tier, '%d 级' % tier) for tier in range(1, 11)],
                        filters['tiers'])
        elif choice == 'c':
            _toggle_set('选择车型 (空格切换, Enter 确认)',
                        [(clazz, CLASS_NAMES_CN.get(clazz, clazz))
                         for clazz in VEHICLE_CLASSES],
                        filters['classes'])


def _toggle_set(title, options, target):
    index = 0
    while True:
        width, height = terminal_size()
        lines = [BOLD + title + RESET, '']
        for i, (value, label) in enumerate(options):
            mark = '[x]' if value in target else '[ ]'
            line = ' %s %s' % (mark, label)
            lines.append((REVERSE if i == index else '') + _fit(
                line, width - 1) + (RESET if i == index else ''))
        draw(lines)
        pressed = read_key()
        if pressed == 'up':
            index = (index - 1) % len(options)
        elif pressed == 'down':
            index = (index + 1) % len(options)
        elif pressed == ' ':
            target.symmetric_difference_update({options[index][0]})
        elif pressed in ('enter', 'esc'):
            return


# ---- vehicle profile enhancement ------------------------------------------

def enhancement_rows(choices):
    """Normalize launcher's complete editable roster for trainer filtering."""
    rows = []
    for choice in choices:
        rows.append({
            'key': choice['member'],
            'choice': choice,
            'nation': choice['nation'],
            'tier': choice.get('level'),
            'clazz': choice.get('vehicleClass'),
            'name': choice.get('label') or choice['vehicle'],
        })
    return sorted(rows, key=lambda row: (
        row['nation'], -(row['tier'] or 0), row['name']))


def enhancement_row_matches(row, filters, text=''):
    if filters['nations'] and row['nation'] not in filters['nations']:
        return False
    if filters['tiers'] and row['tier'] not in filters['tiers']:
        return False
    if filters['classes'] and row['clazz'] not in filters['classes']:
        return False
    if text:
        wanted = text.lower()
        choice = row['choice']
        values = (row['name'], choice['vehicle'], choice['member'])
        if not any(wanted in str(value).lower() for value in values):
            return False
    return True


def _colored_tags(tags, presets):
    parts = []
    colors = {'1': BLUE, '2': PURPLE, '3': GOLD}
    for tag in tags:
        level = str(tag['level'])
        if level in ('custom', 'S'):
            parts.append(GREEN + '[%s Lv.S]' % tag['tag'] + RESET)
            continue
        style = presets['level_styles'][level]
        suffix = '' if tag.get('exact') else '·混合'
        value = '[%s %s%s]' % (tag['tag'], style['name'], suffix)
        parts.append(colors.get(level, '') + value + RESET)
    return ' '.join(parts)


def format_enhancement_row(row, width, tag_index=None, presets=None,
                           evaluate=False):
    nation = NATION_NAMES_CN.get(row['nation'], row['nation'])
    clazz = CLASS_NAMES_CN.get(row['clazz'], row['clazz'] or '?')
    tags = ''
    if tag_index is not None:
        try:
            values = (tag_index.tags(row['choice']) if evaluate else
                      tag_index.cached_tags(row['choice']))
            tags = _colored_tags(values, presets)
        except vehicle_modifications.VehicleModificationError:
            tags = '[Tag评估失败]'
    return _fit_ansi(' %-4s %2s级 %-8s %-22s %s' % (
        nation, row['tier'] or '?', clazz, row['name'], tags), width)


def enhancement_vehicle_picker(rows, filters, tag_index, presets):
    """Single-select complete roster with crew picker's filters/search."""
    index = top = 0
    text = filters.get('text', '')
    while True:
        visible = [row for row in rows
                   if enhancement_row_matches(row, filters, text)]
        index = max(0, min(index, len(visible) - 1))
        width, height = terminal_size()
        body = max(1, height - 7)
        if index < top:
            top = index
        elif index >= top + body:
            top = index - body + 1
        lines = [BOLD + '选择一辆车辆 (筛选结果 %d / 全部 %d)' % (
            len(visible), len(rows)) + RESET,
                 DIM + '搜索: %s' % (text or '(直接输入)') + RESET, '']
        for offset, row in enumerate(visible[top:top + body]):
            actual = top + offset
            line = format_enhancement_row(
                row, width - 2, tag_index, presets,
                evaluate=(actual == index))
            lines.append((REVERSE if actual == index else '') + line +
                         (RESET if actual == index else ''))
        lines.extend(['', DIM +
                      '↑↓/PgUp/PgDn 移动  F 筛选  / 清空搜索  '
                      'Enter 单选  Esc 返回' + RESET])
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
        elif pressed == '/' :
            text = ''
            index = top = 0
        elif pressed in ('f', 'F') and not text:
            filters = filter_menu(filters)
        elif pressed == 'backspace':
            text = text[:-1]
            index = top = 0
        elif pressed == 'enter' and visible:
            filters['text'] = text
            return visible[index]['choice'], filters
        elif pressed == 'esc':
            return None, filters
        elif len(pressed) == 1 and pressed.isprintable():
            text += pressed
            index = top = 0


def decimal_input(title, current=1.0):
    text = str(current)
    while True:
        draw([BOLD + title + RESET, '', '> ' + text, '',
              DIM + '输入非负倍率，例如 1.5；Enter 确认，Esc 取消' + RESET])
        pressed = read_key()
        if pressed == 'esc':
            return None
        if pressed == 'enter':
            try:
                value = float(text)
            except ValueError:
                continue
            if value >= 0 and value != float('inf'):
                return value
        elif pressed == 'backspace':
            text = text[:-1]
        elif len(pressed) == 1 and pressed in '0123456789.eE+-':
            text += pressed


def custom_factor_editor(fields, initial=None):
    """Set an independent multiplier for every concrete category field."""
    factors = dict(((field['member'], field['fieldPath']), 1.0)
                   for field in fields)
    if initial:
        factors.update(dict((key, value) for key, value in initial.items()
                            if key in factors))
    index = top = 0
    while True:
        width, height = terminal_size()
        body = max(1, height - 6)
        index = max(0, min(index, len(fields) - 1))
        if index < top:
            top = index
        elif index >= top + body:
            top = index - body + 1
        lines = [BOLD + '自定义逐字段倍率' + RESET, '']
        for offset, field in enumerate(fields[top:top + body]):
            actual = top + offset
            key = (field['member'], field['fieldPath'])
            line = ' ×%-7g %s' % (factors[key], field.get(
                'fieldLabel', field['fieldPath']))
            lines.append((REVERSE if actual == index else '') +
                         _fit(line, width - 1) +
                         (RESET if actual == index else ''))
        lines.extend(['', DIM +
                      'Enter 设置当前项  S 完成  R 全部重置为1  Esc 取消' + RESET])
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
        elif pressed == 'enter' and fields:
            field = fields[index]
            key = (field['member'], field['fieldPath'])
            value = decimal_input('设置倍率: %s' % field.get(
                'fieldLabel', field['fieldPath']), factors[key])
            if value is not None:
                factors[key] = value
        elif pressed in ('r', 'R'):
            factors = dict((key, 1.0) for key in factors)
        elif pressed in ('s', 'S'):
            return factors
        elif pressed == 'esc':
            return None


def vehicle_profile_menu(current, game_root):
    try:
        names = vehicle_overlays.list_vehicle_profiles(game_root)
    except vehicle_overlays.VehicleOverlayError as error:
        text_view('车辆属性方案错误', str(error))
        return current
    items = [(name, name + ('  ◀ 当前' if name == current else ''))
             for name in names]
    if not items:
        text_view('车辆属性方案', '没有可编辑的车辆属性方案。请先在 launcher 创建方案。')
        return current
    return menu('选择共享车辆属性方案', items,
                subtitle='vehicle_profiles.json 跨存档共享') or current


def vehicle_backup_menu(game_root):
    backups = vehicle_modifications.list_backups()
    if not backups:
        text_view('恢复车辆方案', '没有找到车辆属性方案备份。')
        return
    items = []
    for index, backup in enumerate(backups):
        label = '%s  %s  before %s' % (
            backup.get('createdAt', ''), backup.get('profileName', ''),
            backup.get('vehicleName', ''))
        items.append((str(index), label))
    choice = menu('恢复车辆属性方案', items,
                  subtitle='只恢复备份中的同名方案；恢复前会再次备份')
    if choice is None:
        return
    backup = backups[int(choice)]
    if not confirm('确定恢复方案 %s 的这个版本?' % backup['profileName']):
        return
    try:
        name = vehicle_modifications.restore_backup(
            vehicle_overlays, game_root, backup)
    except (vehicle_modifications.VehicleModificationError,
            vehicle_overlays.VehicleOverlayError, OSError) as error:
        text_view('恢复失败', str(error))
        return
    text_view('完成', '已恢复车辆属性方案 %s。' % name)


def _level_label(level, presets, mixed=False):
    """Render one level name with the same color used by vehicle tags."""
    level = str(level)
    if level in ('custom', 'S'):
        name = 'Lv.S'
    else:
        name = presets['level_styles'][level]['name']
    if mixed:
        name += '·混合'
    color = {'1': BLUE, '2': PURPLE, '3': GOLD,
             'custom': GREEN, 'S': GREEN}.get(level, '')
    return color + name + (RESET if color else '')


def _selection_label(selection, presets):
    level = str(selection['level'])
    return '%s %s' % (
        presets['categories'][selection['category']]['tag'],
        _level_label(level, presets, bool(selection.get('mixed'))))


def _applied_selections(tags):
    """Convert evaluated profile tags into category selections for display."""
    result = {}
    for tag in tags or ():
        category = tag.get('category')
        if not category:
            continue
        level = tag.get('level')
        result[category] = {
            'category': category,
            'level': 'S' if level in ('custom', 'S') else str(level),
            'mixed': (level not in ('custom', 'S') and
                      not tag.get('exact', False)),
        }
    return result


def _build_combined_vehicle_plan(game_root, profile, vehicle, presets,
                                 selections):
    try:
        fields = vehicle_overlays.list_vehicle_profile_field_choices(
            game_root, profile, vehicle['member'])
        plans = []
        for category, selection in selections.items():
            if selection['level'] == 'S':
                plans.append(vehicle_modifications.plan_custom_vehicle(
                    vehicle_overlays, game_root, profile, vehicle, presets,
                    category, selection['factors'], fields=fields))
            else:
                plans.append(vehicle_modifications.plan_vehicle(
                    vehicle_overlays, game_root, profile, vehicle, presets,
                    category, selection['level'], fields=fields))
        return vehicle_modifications.combine_plans(plans)
    except (vehicle_modifications.VehicleModificationError,
            vehicle_overlays.VehicleOverlayError) as error:
        text_view('生成强化方案失败', str(error))
        return None


def _choose_category_removals(plan):
    """Keep direct fields and ask once for every affected shared module."""
    selected = [change for change in plan['changes']
                if not change.get('shared')]
    groups = {}
    order = []
    for change in plan['changes']:
        if not change.get('shared'):
            continue
        key = (change['member'], change.get('component') or
               change['fieldPath'])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(change)
    for key in order:
        changes = groups[key]
        component = changes[0].get('component') or changes[0]['label']
        affected = sorted(set(
            name for change in changes
            for name in change.get('affectedVehicles', ())))
        subtitle = '字段数: %d\n受影响车辆: %s' % (
            len(changes), ', '.join(affected) if affected else '(未知)')
        decision = menu('共享模块: %s' % component, [
            ('keep', '保留该共享模块的修改（安全默认）'),
            ('remove', '取消该共享模块的修改并恢复原值'),
            ('x', '取消整个分类操作'),
        ], subtitle=subtitle)
        if decision in (None, 'x'):
            return None
        if decision == 'remove':
            selected.extend(changes)
    return vehicle_modifications.select_category_removals(plan, selected)


def vehicle_edit_session(game_root, profile, vehicle, presets,
                         applied_tags=None):
    """Collect category choices, then preview and commit them as one edit."""
    selections = {}
    applied = _applied_selections(applied_tags)
    categories = presets['categories']
    while True:
        current_summary = '  '.join(
            _selection_label(applied[category], presets)
            for category in categories if category in applied) or '(无)'
        pending_summary = '  '.join(
            _selection_label(selections[category], presets)
            for category in categories if category in selections) or '(无)'
        items = []
        for category, definition in categories.items():
            saved = applied.get(category)
            pending = selections.get(category)
            saved_level = (_level_label(
                saved['level'], presets, bool(saved.get('mixed')))
                if saved else '未套用')
            if pending:
                pending_level = _level_label(pending['level'], presets)
                suffix = '  [%s → %s]' % (saved_level, pending_level)
            else:
                suffix = '  [%s]' % saved_level
            items.append((category, definition['tag'] + suffix))
        items.extend([
            ('details', '查看已启用方案细则'),
            ('apply', '套用全部已启用方案'),
            ('q', '取消本次车辆编辑'),
        ])
        choice = menu('编辑车辆: %s' % vehicle.get(
            'label', vehicle['vehicle']), items,
            subtitle=('属性方案: %s\n当前已套用: %s\n'
                      '本次待套用: %s' % (
                          profile, current_summary, pending_summary)))
        if choice in (None, 'q'):
            return False
        if choice == 'details':
            if not selections:
                text_view('方案细则', '尚未启用任何分类方案。')
                continue
            plan = _build_combined_vehicle_plan(
                game_root, profile, vehicle, presets, selections)
            if plan is not None:
                text_view('方案细则（Esc 返回车辆编辑）',
                          vehicle_modifications.plan_text(plan, presets))
            continue
        if choice == 'apply':
            if not selections:
                text_view('套用方案', '尚未启用任何分类方案。')
                continue
            plan = _build_combined_vehicle_plan(
                game_root, profile, vehicle, presets, selections)
            if plan is None:
                continue
            if not plan['changes']:
                text_view('套用方案', '当前值已经匹配全部启用方案。')
                continue
            text_view('套用前细则（Esc 返回确认菜单）',
                      vehicle_modifications.plan_text(plan, presets))
            decision = menu('套用已启用方案', [
                ('a', '统一执行 %d 个字段修改' % len(plan['changes'])),
                ('b', '返回车辆编辑'),
                ('x', '取消本次车辆编辑'),
            ], subtitle='将只创建一次备份并统一写入\n已启用: %s' %
                pending_summary)
            if decision in (None, 'x'):
                return False
            if decision == 'b':
                continue
            try:
                backup = vehicle_modifications.apply_plan(
                    vehicle_overlays, game_root, plan)
            except (vehicle_modifications.VehicleModificationError,
                    vehicle_overlays.VehicleOverlayError, OSError) as error:
                text_view('强化失败', str(error))
                continue
            text_view('强化完成', '已统一写入 %d 个字段。\n备份: %s' % (
                len(plan['changes']), backup))
            return plan

        category = choice
        current = selections.get(category)
        saved = applied.get(category)
        level = menu('%s — 选择或停用方案' % categories[category]['tag'], [
            ('1', _level_label('1', presets) + ' · 蓝色'),
            ('2', _level_label('2', presets) + ' · 紫色'),
            ('3', _level_label('3', presets) + ' · 金色'),
            ('S', _level_label('S', presets)),
            ('off', '停用本次尚未套用的选择'),
            ('remove', '取消该分类已有修改并恢复原值'),
        ], subtitle='当前已套用: %s\n本次选择: %s' % (
            _level_label(saved['level'], presets, bool(saved.get('mixed')))
            if saved else '(未套用)',
            _level_label(current['level'], presets)
            if current else '(无)'))
        # Esc only returns to the vehicle editor; it does not discard editing.
        if level is None:
            continue
        if level == 'off':
            selections.pop(category, None)
            continue
        if level == 'remove':
            try:
                fields = vehicle_overlays.list_vehicle_profile_field_choices(
                    game_root, profile, vehicle['member'])
                plan = vehicle_modifications.plan_category_removal(
                    vehicle_overlays, game_root, profile, vehicle, presets,
                    category, fields=fields)
            except (vehicle_modifications.VehicleModificationError,
                    vehicle_overlays.VehicleOverlayError) as error:
                text_view('取消分类失败', str(error))
                continue
            if not plan['changes']:
                text_view('取消分类', '该车辆的此分类没有已保存的修改。')
                continue
            plan = _choose_category_removals(plan)
            if plan is None:
                continue
            if not plan['changes']:
                text_view('取消分类', '所有共享模块均选择保留，没有需要取消的字段。')
                continue
            text_view('取消前细则（Esc 返回确认菜单）',
                      vehicle_modifications.plan_text(plan, presets))
            decision = menu('确认取消该分类', [
                ('a', '取消 %d 个字段修改并恢复原值' % len(plan['changes'])),
                ('b', '返回车辆编辑'),
                ('x', '取消本次操作'),
            ], subtitle='只移除“%s”，其他分类保持不变；将先创建一次备份' %
                categories[category]['tag'])
            if decision in (None, 'x'):
                continue
            if decision == 'b':
                continue
            try:
                backup = vehicle_modifications.apply_plan(
                    vehicle_overlays, game_root, plan)
            except (vehicle_modifications.VehicleModificationError,
                    vehicle_overlays.VehicleOverlayError, OSError) as error:
                text_view('取消分类失败', str(error))
                continue
            text_view('取消分类完成',
                      '已移除 %d 个字段修改。\n备份: %s' % (
                          len(plan['changes']), backup))
            return plan
        if level == 'S':
            try:
                fields = vehicle_modifications.custom_category_fields(
                    vehicle_overlays, game_root, profile, vehicle, presets,
                    category)
            except (vehicle_modifications.VehicleModificationError,
                    vehicle_overlays.VehicleOverlayError) as error:
                text_view('自定义方案不可用', str(error))
                continue
            if not fields:
                text_view('自定义方案不可用', '该车辆在此分类下没有可修改字段。')
                continue
            existing = current.get('factors') if current and current.get(
                'level') == 'S' else None
            factors = custom_factor_editor(fields, existing)
            if factors is None:
                continue
            selections[category] = {
                'category': category, 'level': 'S', 'factors': factors}
        else:
            selections[category] = {'category': category, 'level': level}


def _prepare_tag_cache(tag_index, rows):
    """Prune removed vehicles; individual rows are evaluated on demand."""
    vehicles = [row['choice'] for row in rows]
    tag_index.prune_vehicles(vehicles)


def _refresh_tag_cache(tag_index, rows, selected, plan):
    """Update cached tags for the vehicles one apply actually touched."""
    if tag_index is None:
        return
    try:
        tag_index.resync()
    except vehicle_modifications.VehicleModificationError:
        return
    affected = set(plan.get('affectedVehicles', ()))
    targets = {}
    if selected is not None:
        targets[selected['member']] = selected
    for row in rows:
        choice = row['choice']
        key = '%s:%s' % (choice['nation'], choice['vehicle'])
        if key in affected:
            targets[choice['member']] = choice
    tag_index.refresh(list(targets.values()))


def vehicle_enhancement_menu(game_root, current_profile=None):
    try:
        presets = vehicle_modifications.load_presets()
        choices = vehicle_overlays.list_vehicle_choices(game_root)
        profile = current_profile or vehicle_modifications.default_profile(
            vehicle_overlays, game_root)
    except (vehicle_modifications.VehicleModificationError,
            vehicle_overlays.VehicleOverlayError) as error:
        text_view('车辆强化不可用', str(error))
        return current_profile
    if profile is None:
        text_view('车辆强化不可用', '没有车辆属性方案；请先在 launcher 创建 SPG 方案。')
        return current_profile
    rows = enhancement_rows(choices)
    selected = None
    filters = {'nations': set(), 'tiers': set(), 'classes': set(), 'text': ''}
    tag_index = None
    try:
        while True:
            if tag_index is None:
                try:
                    tag_index = vehicle_modifications.VehicleTagIndex(
                        vehicle_overlays, game_root, profile, presets)
                except vehicle_modifications.VehicleModificationError as error:
                    text_view('Tag 索引错误', str(error))
                else:
                    _prepare_tag_cache(tag_index, rows)
            summary = _filter_summary(filters)
            subtitle = '共享方案: %s\n车辆库: %s' % (profile, game_root)
            if selected:
                subtitle += '\n当前车辆: %s' % selected.get(
                    'label', selected['vehicle'])
            if summary:
                subtitle += '\n筛选中: ' + summary
            choice = menu('车辆强化', [
                ('1', '选择车辆（完整车辆库，单选）'),
                ('2', '设置筛选（国家/等级/车型）'),
                ('3', '选择车辆属性方案（当前: %s）' % profile),
                ('4', '套用强化方案'),
                ('5', '恢复车辆属性方案备份'),
                ('q', '返回主菜单'),
            ], subtitle=subtitle)
            if choice in (None, 'q'):
                return profile
            if choice == '1':
                result, filters = enhancement_vehicle_picker(
                    rows, filters, tag_index, presets)
                if result is not None:
                    selected = result
            elif choice == '2':
                filters = filter_menu(filters)
            elif choice == '3':
                new_profile = vehicle_profile_menu(profile, game_root)
                if new_profile != profile:
                    if tag_index is not None:
                        tag_index.close()
                    tag_index = None
                    profile = new_profile
            elif choice == '5':
                vehicle_backup_menu(game_root)
                if tag_index is not None:
                    tag_index.close()
                tag_index = None
            elif choice == '4':
                if selected is None:
                    text_view('套用强化方案', '请先选择一辆车辆。')
                    continue
                try:
                    applied_tags = (tag_index.tags(selected)
                                    if tag_index is not None else [])
                except vehicle_modifications.VehicleModificationError as error:
                    text_view('Tag 索引错误', str(error))
                    applied_tags = []
                applied_plan = vehicle_edit_session(
                    game_root, profile, selected, presets, applied_tags)
                if applied_plan:
                    _refresh_tag_cache(tag_index, rows, selected,
                                       applied_plan)
                continue
    finally:
        if tag_index is not None:
            tag_index.close()


# ---- template editor --------------------------------------------------------

_ALL_ROLES = ('commander', 'radioman', 'driver', 'gunner', 'loader')


def _sorted_available(roles):
    """Valid skills for a role combo: role-specific first, common last."""
    common = {'repair', 'fireFighting', 'camouflage', 'brotherhood'}
    available = skills_for_roles(roles)
    return sorted(available, key=lambda s: (s in common, s))


def _pick_exact(title, options, current, exact_count):
    """Toggle list requiring exactly ``exact_count`` picks to confirm."""
    target = set(current)
    while True:
        _toggle_set('%s (已选 %d/%d, Enter 确认, Esc 取消)' % (
            title, len(target), exact_count), options, target)
        if len(target) == exact_count:
            return target
        if not confirm('需要恰好 %d 个技能 (当前 %d 个)。重新选择?' % (
                exact_count, len(target))):
            return None


def _edit_template(combo, entry):
    """Interactively edit one template; returns new entry or None."""
    roles = combo.split('+')
    available = _sorted_available(roles)
    options = [(skill, describe_skill(skill)) for skill in available]
    trained = _pick_exact(
        '%s — 选择 7 个生效技能' % describe_role(combo), options,
        entry.get('trained', ()), 7)
    if trained is None:
        return None
    training = None
    remaining = [skill for skill in available if skill not in trained]
    if remaining:
        current = entry.get('training')
        if current not in remaining:
            current = remaining[0]
        items = [(skill, describe_skill(skill)) for skill in remaining]
        index = [i for i, (s, _l) in enumerate(items) if s == current][0]
        # 在练技能: 单选菜单
        while True:
            width, height = terminal_size()
            lines = [BOLD + '%s — 选择第 8 个在练技能' % describe_role(combo) +
                     RESET, '']
            for i, (skill, label) in enumerate(items):
                prefix = '▶ ' if i == index else '  '
                lines.append((REVERSE if i == index else '') + _fit(
                    prefix + label, width - 1) +
                    (RESET if i == index else ''))
            lines.append('')
            lines.append(DIM + '↑↓ 移动  Enter 确认  Esc 取消' + RESET)
            draw(lines)
            pressed = read_key()
            if pressed == 'up':
                index = (index - 1) % len(items)
            elif pressed == 'down':
                index = (index + 1) % len(items)
            elif pressed == 'enter':
                training = items[index][0]
                break
            elif pressed == 'esc':
                return None
    # 保持模板中的技能顺序稳定: 生效技能按 available 顺序写回
    ordered = [skill for skill in available if skill in trained]
    return {'trained': ordered, 'training': training}


def template_menu(templates_path):
    """View / edit / create skill templates; saves to ``templates_path``."""
    while True:
        templates = load_templates(templates_path)
        items = []
        for combo in sorted(templates):
            entry = templates[combo]
            summary = ', '.join(
                SKILL_NAMES_CN.get(s, s) for s in entry['trained'])
            items.append((combo, '%s\n      %s%s' % (
                combo, summary,
                ' + 在练 %s' % SKILL_NAMES_CN.get(entry['training'], '')
                if entry.get('training') else '')))
        items.append(('+', '新增角色组合…'))
        choice = menu('编辑技能模板 (保存在 %s)' %
                      os.path.basename(templates_path), items,
                      subtitle='Enter 编辑该组合的模板')
        if choice is None:
            return load_templates(templates_path)
        if choice == '+':
            primary = menu('选择主角色', [
                (role, describe_role(role)) for role in _ALL_ROLES])
            if primary is None:
                continue
            secondaries = set()
            _toggle_set('选择兼职角色 (空格切换, Enter 确认)', [
                (role, describe_role(role))
                for role in _ALL_ROLES if role != primary], secondaries)
            combo = normalize_combo([primary] + sorted(secondaries))
            if combo in templates:
                text_view('提示', '模板 %s 已存在, 请直接编辑。' % combo)
                continue
            entry = {'trained': [], 'training': None}
        else:
            combo = choice
            entry = templates[combo]
        updated = _edit_template(combo, entry)
        if updated is None:
            continue
        templates[combo] = updated
        try:
            save_templates(templates_path, templates)
            load_templates(templates_path)  # 立即校验落盘内容
        except (TemplateError, ValueError) as error:
            text_view('保存失败', str(error))
        else:
            text_view('已保存', '模板 %s 已写入 %s' % (
                combo, os.path.basename(templates_path)))


def skill_table_text():
    lines = []
    for role in ('commander', 'radioman', 'driver', 'gunner', 'loader'):
        lines.append(describe_role(role) + ':')
        for skill in sorted(SKILLS_BY_ROLES[role]):
            lines.append('  %s' % describe_skill(skill))
        lines.append('')
    return '\n'.join(lines)


def slot_menu(current_slot):
    """Pick which save slot to edit. Returns the chosen slot id or None."""
    items = []
    for entry in list_save_slots():
        label = entry['id']
        if entry['name'] != entry['id']:
            label += ' (%s)' % entry['name']
        if not entry['has_garage']:
            label += '  — 无 garage_state.json'
        if entry['id'] == current_slot:
            label += '  ◀ 当前'
        items.append((entry['id'], label))
    return menu('选择要编辑的存档', items,
                subtitle='当前: %s' % (current_slot or '(自定义路径)'))


def backup_menu(garage_path):
    backups = sorted(glob.glob(garage_path + '.trainer-backup-*'))
    if not backups:
        text_view('恢复备份', '没有找到备份文件 (%s.trainer-backup-*)' % garage_path)
        return
    items = [(str(i), os.path.basename(path))
             for i, path in enumerate(backups)]
    choice = menu('恢复备份 (选择要还原的备份)', items,
                  subtitle='当前文件会先另存为 .before-restore')
    if choice is None:
        return
    source = backups[int(choice)]
    if not confirm('确定用 %s 覆盖当前存档?' % os.path.basename(source)):
        return
    running = client_running()
    if running:
        text_view('错误', '客户端正在运行 (%s), 请先关闭游戏。' %
                  ', '.join(running))
        return
    shutil.copyfile(garage_path, garage_path + '.before-restore')
    shutil.copyfile(source, garage_path)
    text_view('完成', '已恢复 %s\n原文件另存为 %s.before-restore' % (
        os.path.basename(source), garage_path))


# ---- main flow --------------------------------------------------------------

def build_report(state, vehicles, templates, selected_keys, overwrite=False):
    """(report_text, change_count) for the selected vehicles."""
    lines = []
    if overwrite:
        lines.append('!! 覆盖模式: 已学技能的乘员将被模板重写 !!')
        lines.append('')
    vehicles_hit = members = 0
    for key in sorted(selected_keys, key=int):
        info = vehicles.get(int(key))
        record = state['vehicles'].get(key)
        if info is None or record is None:
            continue
        changes, skips = plan_vehicle(info, record, templates, overwrite)
        if not changes:
            continue
        vehicles_hit += 1
        members += len(changes)
        nation = NATION_NAMES_CN.get(info.nation, info.nation)
        clazz = CLASS_NAMES_CN.get(info.clazz, info.clazz)
        lines.append('%s %d级 %s %s [%s]' % (
            nation, info.tier, clazz, info.name, info.key))
        for slot, combo, trained, training, _ in changes:
            lines.append('  槽位%s %s: %s' % (
                slot, combo, ', '.join(
                    SKILL_NAMES_CN.get(s, s) for s in trained)))
            if training:
                lines.append('    在练: %s' % describe_skill(training))
        for slot, reason in skips:
            lines.append('  槽位%s 跳过: %s' % (slot, reason))
    lines.append('')
    lines.append('共 %d 辆车, %d 个乘员槽位将写入模板' % (vehicles_hit, members))
    return '\n'.join(lines), members


def apply_changes(state, vehicles, templates, selected_keys, garage_path,
                  overwrite=False):
    running = client_running()
    if running:
        text_view('错误', '客户端正在运行 (%s), 请先关闭游戏再写入。' %
                  ', '.join(running))
        return
    changed = 0
    for key in selected_keys:
        info = vehicles.get(int(key))
        record = state['vehicles'].get(key)
        if info is None or record is None:
            continue
        changes, unused = plan_vehicle(info, record, templates, overwrite)
        for slot, _c, _t, _tr, encoded in changes:
            record['crew'][str(slot)] = encoded
            changed += 1
    if not changed:
        text_view('应用', '没有可应用的改动。')
        return
    backup = write_garage(garage_path, state)
    text_view('完成', '已写入 %d 个乘员槽位。\n备份: %s\n\n请启动客户端进车库确认。' % (
        changed, os.path.basename(backup)))


def _filter_summary(filters):
    parts = []
    if filters['nations']:
        parts.append('系别:%s' % ','.join(sorted(
            NATION_NAMES_CN.get(n, n) for n in filters['nations'])))
    if filters['tiers']:
        parts.append('等级:%s' % ','.join(
            str(t) for t in sorted(filters['tiers'])))
    if filters['classes']:
        parts.append('车型:%s' % ','.join(sorted(
            CLASS_NAMES_CN.get(c, c) for c in filters['classes'])))
    if filters.get('text'):
        parts.append('搜索:"%s"' % filters['text'])
    return '  '.join(parts)


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
            text, count = build_report(state, vehicles, templates,
                                       effective)
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
            # 写入前: 先展示完整变更清单, 再要求显式确认
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
                selected &= {row['key'] for row in rows
                             if row['ready'] > 0}
        elif choice == '5':
            templates = template_menu(DEFAULT_TEMPLATES)
        elif choice == '6':
            text_view('技能对照表 (内部名 → 中文名)', skill_table_text())


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


def gun_marks_row_matches(state, text):
    """Incremental-search predicate for the single-vehicle marks picker."""
    if not text:
        return True
    wanted = text.lower()
    info = state['info']
    return any(wanted in value.lower() for value in (
        info.name, info.key, state['typeName']))


def format_gun_marks_row(state, width):
    info = state['info']
    nation = NATION_NAMES_CN.get(info.nation, info.nation)
    clazz = CLASS_NAMES_CN.get(info.clazz, info.clazz)
    return _fit(
        ' %-3s %2d级 %-5s %-18s  场均 %5d  %d环  %d场' % (
            nation, info.tier, clazz, info.name,
            state['movingAvgDamage'], state['marksOnGun'], state['battles']),
        width)


def gun_marks_vehicle_picker(states):
    """Searchable, single-choice list of eligible owned vehicles."""
    index = top = 0
    text = ''
    while True:
        visible = [state for state in states
                   if gun_marks_row_matches(state, text)]
        index = max(0, min(index, len(visible) - 1))
        width, height = terminal_size()
        body = max(1, height - 6)
        if index < top:
            top = index
        elif index >= top + body:
            top = index - body + 1
        lines = [BOLD + '伤害标记计算器 — 选择车辆 (%d / %d)' % (
            len(visible), len(states)) + RESET]
        lines.append(DIM + '搜索: %s' % (text or '(直接输入车辆名称)') + RESET)
        lines.append('')
        for offset, state in enumerate(visible[top:top + body]):
            actual = top + offset
            line = format_gun_marks_row(state, width - 2)
            lines.append((REVERSE if actual == index else '') + line +
                         (RESET if actual == index else ''))
        if not visible:
            lines.append('  没有匹配车辆')
        lines.append('')
        lines.append(DIM +
                     '↑↓/PgUp/PgDn 移动  Enter 选择  / 清空搜索  Esc 返回' +
                     RESET)
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
        elif pressed == 'enter' and visible:
            return visible[index]
        elif pressed == 'esc':
            return None
        elif pressed == '/':
            text = ''
            index = top = 0
        elif pressed == 'backspace':
            text = text[:-1]
            index = top = 0
        elif len(pressed) == 1 and pressed.isprintable():
            text += pressed
            index = top = 0


def gun_marks_menu(state, vehicles, garage_path):
    """Read the current save and display a repeatable marks projection."""
    planned_damage = 3000
    while True:
        try:
            progress = gun_marks.load_progress(
                gun_marks.postbattle_path(garage_path))
            states = gun_marks.vehicle_states(
                vehicles, progress,
                owned_type_cds=state.get('vehicles', {}).keys())
        except gun_marks.GunMarksError as error:
            text_view('伤害标记计算器 — 错误', str(error))
            return
        if not states:
            text_view(
                '伤害标记计算器',
                '当前车库没有可计算伤害标记的 V–X 级车辆。')
            return
        selected = gun_marks_vehicle_picker(states)
        if selected is None:
            return
        entered = number_input(
            '预计以后每场综合伤害（直接伤害 + 最大一项协助）',
            planned_damage, maximum=99999)
        if entered is None:
            continue
        planned_damage = entered
        result = gun_marks.projection(selected, planned_damage)
        text_view('伤害标记计算结果',
                  gun_marks.report_text(selected, result))


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
        elif (len(pressed) == 1 and pressed.isdigit() and
              len(buffer) < len(str(maximum))):
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


def _legacy_main(argv=None):
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


def main(argv=None):
    """Run the trainer with save data and vehicle profiles as separate scopes."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        slot = None
        garage_path = args[0]
    else:
        slot = DEFAULT_SLOT
        garage_path = slot_garage_path(slot)
    if not os.path.isfile(garage_path):
        print('找不到存档: %s' % garage_path)
        if slot == DEFAULT_SLOT and os.path.isfile(LEGACY_GARAGE):
            print('提示: 发现旧版存档 %s；请先启动一次游戏完成迁移。' %
                  LEGACY_GARAGE)
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
    current_vehicle_profile = None

    with Screen():
        while True:
            choice = menu('车库助手 (Trainer)', [
                ('1', '车组管理  按模板设置乘员技能'),
                ('2', '库存管理  配件库存数量'),
                ('3', '车辆强化  分类挡位 / 自定义倍率'),
                ('4', '选择存档 (当前: %s)' % (slot or '(自定义路径)')),
                ('5', '恢复存档备份'),
                ('6', '伤害标记计算器  当前场均 / 下一环 / 预计所需场次'),
                ('q', '退出'),
            ], subtitle='存档: %s\n%s\n车辆属性方案跨存档共享' % (
                slot or '(自定义路径)', garage_path))
            if choice in (None, 'q'):
                return 0
            if choice == '1':
                templates = crew_menu(
                    state, vehicles, templates, slot, garage_path)
            elif choice == '2':
                inventory_menu(state, artefacts, garage_path)
            elif choice == '3':
                current_vehicle_profile = vehicle_enhancement_menu(
                    DEFAULT_CLIENT_DIR, current_vehicle_profile)
            elif choice == '4':
                new_slot = slot_menu(slot)
                if new_slot is None or new_slot == slot:
                    continue
                new_path = slot_garage_path(new_slot)
                if not os.path.isfile(new_path):
                    text_view('错误', '该存档还没有 garage_state.json:\n%s\n\n'
                              '请先以此存档启动游戏进入一次车库。' % new_path)
                    continue
                slot = new_slot
                garage_path = new_path
                state = _load_state(garage_path)
            elif choice == '5':
                backup_menu(garage_path)
                state = _load_state(garage_path)
            elif choice == '6':
                gun_marks_menu(state, vehicles, garage_path)


if __name__ == '__main__':
    sys.exit(main())
