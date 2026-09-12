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

VEHICLE_CLASSES = VEHICLE_CLASS_TAGS


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
            lines.append((REVERSE if i == index else '') + _fit(
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


if __name__ == '__main__':
    sys.exit(main())
