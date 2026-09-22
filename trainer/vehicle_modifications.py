"""Preset-driven vehicle profile editing for the trainer TUI.

The launcher owns the strict Packed XML contract.  This module deliberately
uses that existing service without changing launcher code.  It adds presets,
preview/apply orchestration, repository-local backups, and a small SQLite
index whose contents can always be rebuilt from ``vehicle_profiles.json``.
"""

from __future__ import annotations

import copy
import datetime
import decimal
import hashlib
import json
import math
import os
import re
import sqlite3

from launcher import vehicle_overlays


TRAINER_DIR = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.dirname(TRAINER_DIR)
DEFAULT_PRESETS = os.path.join(TRAINER_DIR, 'vehicle_presets.json')
USER_ROOT = os.path.join(REPOSITORY_ROOT, '.user', 'trainer')
USER_ROOT = os.path.abspath(os.environ.get(
    'WOT_TRAINER_USER_ROOT', USER_ROOT))
BACKUP_ROOT = os.path.join(USER_ROOT, 'vehicle-backups')
INDEX_PATH = os.path.join(USER_ROOT, 'vehicle_data_cache.sqlite3')
LEGACY_INDEX_PATH = os.path.join(USER_ROOT, 'vehicle-modifications.sqlite3')

_NUMBER = re.compile(r'(?<![^\s])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?')
_SAFE_NAME = re.compile(r'[^A-Za-z0-9._\-\u4e00-\u9fff]+')
_OPERATIONS = frozenset(('multiply', 'subtract', 'add', 'equal'))
_ROUNDING_MODES = frozenset(('round', 'truncate'))


class VehicleModificationError(ValueError):
    pass


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _section_sha1(value):
    """Stable digest for one logical sourceMember section of a profile."""
    return hashlib.sha1(_canonical_bytes(value)).hexdigest()


def load_presets(path=DEFAULT_PRESETS):
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError) as error:
        raise VehicleModificationError('无法读取车辆强化预设: %s' % error)
    if not isinstance(value, dict) or value.get('schema') != 2:
        raise VehicleModificationError('车辆强化预设 schema 必须为 2。')
    styles = value.get('level_styles')
    categories = value.get('categories')
    if not isinstance(styles, dict) or not isinstance(categories, dict):
        raise VehicleModificationError('车辆强化预设缺少 level_styles/categories。')
    for category, definition in categories.items():
        if not isinstance(definition, dict) or not definition.get('tag'):
            raise VehicleModificationError('强化类别 %s 无有效 tag。' % category)
        rules = definition.get('rules')
        if not isinstance(rules, list) or not rules:
            raise VehicleModificationError('强化类别 %s 没有规则。' % category)
        for level in ('1', '2', '3'):
            level_rules(value, category, level)
    return value


def _category_rules(presets, category):
    try:
        rules = presets['categories'][category]['rules']
    except (KeyError, TypeError):
        raise VehicleModificationError('未知强化类别: %s' % category)
    if not isinstance(rules, list) or not rules:
        raise VehicleModificationError('%s 没有规则。' % category)
    checked = []
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('field'), str):
            raise VehicleModificationError('%s 包含无效字段规则。' % category)
        operations = rule.get('operation')
        if not isinstance(operations, (list, tuple)):
            operations = [operations] * 3
        if len(operations) != 3 or any(
                operation not in _OPERATIONS for operation in operations):
            raise VehicleModificationError(
                '%s 的 operation 必须是有效运算符或三个运算符的数组。' %
                rule.get('name', rule['field']))
        values = rule.get('value')
        if not isinstance(values, (list, tuple)) or len(values) != 3:
            raise VehicleModificationError(
                '%s 的 value 必须是包含三个挡位的数组。' %
                rule.get('name', rule['field']))
        _rounding_policy(rule)
        checked.append(copy.deepcopy(rule))
    return checked


def _rule_for_level(rule, level):
    result = copy.deepcopy(rule)
    values = result.get('value')
    if not isinstance(values, (list, tuple)):
        return result
    if not values:
        raise VehicleModificationError('强化规则 value 数组不能为空。')
    try:
        index = int(level) - 1
    except (TypeError, ValueError):
        raise VehicleModificationError('强化挡位必须是数字。')
    if index < 0 or index >= 3:
        raise VehicleModificationError(
            '%s 的 value 必须包含 Lv%s 对应的值。' % (
                result.get('name', result.get('field')), level))
    result['value'] = values[index]
    operations = result.get('operation')
    if isinstance(operations, (list, tuple)):
        result['operation'] = operations[index]
    return result


def level_rules(presets, category, level):
    level = str(level)
    return [_rule_for_level(rule, level) for rule in
            _category_rules(presets, category)]


def _glob_match(value, pattern):
    # ``fnmatch`` lets * cross '/', while preset authors expect ** to do so.
    # Convert the two useful glob forms to a small path-aware regex.
    pieces = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 2] == '**':
            pieces.append('.*')
            index += 2
        elif pattern[index] == '*':
            pieces.append('[^/]*')
            index += 1
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch(''.join(pieces), value) is not None


def rule_matches(field, rule):
    categories = rule.get('component_categories')
    if categories and field.get('category') not in categories:
        return False
    return _glob_match(str(field.get('fieldPath', '')), rule['field'])


def _numbers(text):
    source = str(text).strip()
    matches = list(_NUMBER.finditer(source))
    if not matches:
        raise VehicleModificationError('字段值不是可计算的数字: %s' % text)
    # Refuse units or arbitrary text; whitespace-delimited numeric vectors are
    # the only compound form emitted by Packed XML for these allowlisted fields.
    remainder = _NUMBER.sub('', source)
    if remainder.strip():
        raise VehicleModificationError('字段值不是纯数字或数字序列: %s' % text)
    return [float(match.group(0)) for match in matches]


def _format_number(value, integer=False):
    if not math.isfinite(value):
        raise VehicleModificationError('强化结果不是有限数。')
    if integer:
        return str(int(round(value)))
    rounded = round(value, 10)
    if abs(rounded - round(rounded)) < 1e-10:
        return str(int(round(rounded)))
    return ('%.10f' % rounded).rstrip('0').rstrip('.')


def _rounding_policy(rule):
    mode = rule.get('rounding')
    digits = rule.get('digits', 0)
    # Compatibility with the first draft of the preset file.
    if mode is None and rule.get('round') == 'integer':
        mode, digits = 'round', 0
    elif mode is None and rule.get('round') == 'decimal':
        mode = 'round'
    if mode is None:
        return None
    if mode not in _ROUNDING_MODES:
        raise VehicleModificationError('未知数值整理策略: %s' % mode)
    try:
        digits = int(digits)
    except (TypeError, ValueError, OverflowError):
        raise VehicleModificationError('数值整理的小数位必须是整数。')
    if digits < 0 or digits > 10:
        raise VehicleModificationError('数值整理的小数位必须在 0 到 10 之间。')
    return mode, digits


def _tidy_number(value, mode, digits):
    if not math.isfinite(value):
        raise VehicleModificationError('强化结果不是有限数。')
    quantum = decimal.Decimal(1).scaleb(-digits)
    rounding = (decimal.ROUND_HALF_UP if mode == 'round'
                else decimal.ROUND_DOWN)
    try:
        with decimal.localcontext() as context:
            context.prec = 50
            return float(decimal.Decimal(str(value)).quantize(
                quantum, rounding=rounding))
    except decimal.InvalidOperation:
        raise VehicleModificationError('强化结果无法按指定小数位整理。')


def apply_operation(original, rule):
    operation = rule['operation']
    sequence = rule.get('sequence')
    if operation == 'equal':
        raw = rule['value']
        values = _numbers(raw)
        indexes = range(len(values))
    else:
        values = _numbers(original)
        operand = float(rule['value'])
        indexes = (range(1, len(values), 2)
                   if sequence == 'pitch_curve_values' and len(values) > 1
                   else range(len(values)))
        if operation == 'multiply':
            values = [value * operand if index in indexes else value
                      for index, value in enumerate(values)]
        elif operation == 'subtract':
            values = [value - operand if index in indexes else value
                      for index, value in enumerate(values)]
        elif operation == 'add':
            values = [value + operand if index in indexes else value
                      for index, value in enumerate(values)]
    minimum = rule.get('minimum')
    maximum = rule.get('maximum')
    if minimum is not None:
        values = [max(float(minimum), value) if (
            sequence != 'pitch_curve_values' or len(values) == 1 or
            index % 2 == 1) else value for index, value in enumerate(values)]
    if maximum is not None:
        values = [min(float(maximum), value) if (
            sequence != 'pitch_curve_values' or len(values) == 1 or
            index % 2 == 1) else value for index, value in enumerate(values)]
    policy = _rounding_policy(rule)
    integer_indexes = set()
    if policy is not None:
        mode, digits = policy
        selected = set(indexes)
        values = [_tidy_number(value, mode, digits)
                  if index in selected else value
                  for index, value in enumerate(values)]
        if digits == 0:
            integer_indexes = selected
    return ' '.join(_format_number(
        value, index in integer_indexes)
        for index, value in enumerate(values))


def _same_numeric(left, right, tolerance=1e-7):
    try:
        a, b = _numbers(left), _numbers(right)
    except VehicleModificationError:
        return str(left).strip() == str(right).strip()
    return len(a) == len(b) and all(
        abs(x - y) <= tolerance * max(1.0, abs(x), abs(y))
        for x, y in zip(a, b))


def plan_vehicle(service, game_root, profile_name, vehicle, presets,
                 category, level, fields=None):
    """Return an idempotent field plan calculated from stock values."""
    rules = level_rules(presets, category, level)
    if fields is None:
        try:
            fields = service.list_vehicle_profile_field_choices(
                game_root, profile_name, vehicle['member'])
        except service.VehicleOverlayError as error:
            raise VehicleModificationError(str(error))
    fields = _elite_fields(service, game_root, vehicle, fields)
    changes = []
    affected = set()
    for field in fields:
        matches = [rule for rule in rules if rule_matches(field, rule)]
        if not matches:
            continue
        if len(matches) != 1:
            raise VehicleModificationError(
                '字段 %s 同时匹配多条预设规则。' % field['fieldPath'])
        replacement = apply_operation(field['originalValue'], matches[0])
        current = field.get('currentValue', field['originalValue'])
        for name in field.get('affectedVehicles', ()):
            affected.add('%s:%s' % (field.get('nation', vehicle['nation']), name))
        if _same_numeric(current, replacement):
            continue
        changes.append({
            'member': field['member'],
            'fieldPath': field['fieldPath'],
            'label': field.get('fieldLabel', field['fieldPath']),
            'originalValue': field['originalValue'],
            'currentValue': current,
            'replacementValue': replacement,
            'category': field.get('category'),
            'affectedVehicles': tuple(field.get('affectedVehicles', ())),
        })
    if not changes:
        affected.add('%s:%s' % (vehicle['nation'], vehicle['vehicle']))
    return {
        'profile': profile_name,
        'vehicle': dict(vehicle),
        'presetCategory': category,
        'presetLevel': str(level),
        'changes': changes,
        'affectedVehicles': sorted(affected),
    }


def custom_category_fields(service, game_root, profile_name, vehicle,
                           presets, category, fields=None):
    """All concrete fields covered by a category, deduplicated by key."""
    rules = []
    definition = presets['categories'].get(category)
    if definition is None:
        raise VehicleModificationError('未知强化类别: %s' % category)
    for level in ('1', '2', '3'):
        rules.extend(level_rules(presets, category, level))
    if fields is None:
        try:
            fields = service.list_vehicle_profile_field_choices(
                game_root, profile_name, vehicle['member'])
        except service.VehicleOverlayError as error:
            raise VehicleModificationError(str(error))
    fields = _elite_fields(service, game_root, vehicle, fields)
    result = []
    seen = set()
    for field in fields:
        key = (field['member'], field['fieldPath'])
        if key in seen or not any(rule_matches(field, rule) for rule in rules):
            continue
        seen.add(key)
        result.append(dict(field))
    return result


def plan_custom_vehicle(service, game_root, profile_name, vehicle, presets,
                        category, factors, fields=None):
    """Plan per-field multipliers; a factor of one leaves the field alone."""
    fields = custom_category_fields(
        service, game_root, profile_name, vehicle, presets, category, fields)
    changes = []
    affected = set()
    for field in fields:
        key = (field['member'], field['fieldPath'])
        raw_factor = factors.get(key, factors.get(field['fieldPath'], 1.0))
        try:
            factor = float(raw_factor)
        except (TypeError, ValueError):
            raise VehicleModificationError(
                '字段 %s 的倍率无效。' % field['fieldPath'])
        if not math.isfinite(factor) or factor < 0:
            raise VehicleModificationError(
                '字段 %s 的倍率必须是非负有限数。' % field['fieldPath'])
        if abs(factor - 1.0) < 1e-12:
            continue
        custom_rule = {'operation': 'multiply', 'value': factor}
        formatting_matches = [rule for rule in _category_rules(
            presets, category) if rule_matches(field, rule)]
        if len(formatting_matches) == 1:
            for key in ('rounding', 'digits', 'round'):
                if key in formatting_matches[0]:
                    custom_rule[key] = formatting_matches[0][key]
        if field['fieldPath'].endswith('/pitchLimits/maxPitch'):
            custom_rule['sequence'] = 'pitch_curve_values'
        if field['fieldPath'] == 'speedLimits/forward':
            custom_rule['maximum'] = 90
        elif field['fieldPath'] == 'speedLimits/backward':
            custom_rule['maximum'] = 60
        replacement = apply_operation(field['originalValue'], custom_rule)
        current = field.get('currentValue', field['originalValue'])
        for name in field.get('affectedVehicles', ()):
            affected.add('%s:%s' % (field.get('nation', vehicle['nation']), name))
        if _same_numeric(current, replacement):
            continue
        changes.append({
            'member': field['member'], 'fieldPath': field['fieldPath'],
            'label': field.get('fieldLabel', field['fieldPath']),
            'originalValue': field['originalValue'],
            'currentValue': current, 'replacementValue': replacement,
            'category': field.get('category'),
            'factor': factor,
            'affectedVehicles': tuple(field.get('affectedVehicles', ())),
        })
    return {
        'profile': profile_name, 'vehicle': dict(vehicle),
        'presetCategory': category, 'presetLevel': 'S',
        'changes': changes, 'affectedVehicles': sorted(affected),
    }


def plan_category_removal(service, game_root, profile_name, vehicle, presets,
                          category, fields=None):
    """Plan removal of this category's existing edits for one vehicle."""
    fields = custom_category_fields(
        service, game_root, profile_name, vehicle, presets, category, fields)
    changes = []
    affected = set()
    for field in fields:
        current = field.get('currentValue', field['originalValue'])
        if _same_numeric(current, field['originalValue']):
            continue
        for name in field.get('affectedVehicles', ()):
            affected.add('%s:%s' % (
                field.get('nation', vehicle['nation']), name))
        changes.append({
            'member': field['member'],
            'fieldPath': field['fieldPath'],
            'label': field.get('fieldLabel', field['fieldPath']),
            'originalValue': field['originalValue'],
            'currentValue': current,
            'replacementValue': field['originalValue'],
            'category': field.get('category'),
            'nation': field.get('nation', vehicle['nation']),
            'shared': bool(field.get('shared')),
            'component': field.get('component'),
            'scope': field.get('scope', ''),
            'affectedVehicles': tuple(field.get('affectedVehicles', ())),
        })
    return {
        'profile': profile_name,
        'vehicle': dict(vehicle),
        'presetCategory': category,
        'presetLevel': 'off',
        'action': 'remove',
        'selections': [{'category': category, 'level': 'off'}],
        'changes': changes,
        'affectedVehicles': sorted(affected),
    }


def select_category_removals(plan, changes):
    """Return a removal plan narrowed to the user's shared-module choices."""
    result = copy.deepcopy(plan)
    result['changes'] = [dict(change) for change in changes]
    affected = set()
    vehicle = result['vehicle']
    for change in result['changes']:
        nation = change.get('nation', vehicle['nation'])
        for name in change.get('affectedVehicles', ()):
            affected.add('%s:%s' % (nation, name))
    result['affectedVehicles'] = sorted(affected)
    return result


def combine_plans(plans):
    """Combine category plans for one vehicle into one transactional write."""
    plans = list(plans)
    if not plans:
        raise VehicleModificationError('没有启用任何车辆强化方案。')
    first = plans[0]
    identity = (first['profile'], first['vehicle']['member'])
    changes = {}
    affected = set()
    selections = []
    for plan in plans:
        if (plan['profile'], plan['vehicle']['member']) != identity:
            raise VehicleModificationError('不能合并不同车辆或方案的修改。')
        selections.append({
            'category': plan['presetCategory'],
            'level': str(plan['presetLevel']),
        })
        affected.update(plan.get('affectedVehicles', ()))
        for change in plan.get('changes', ()):
            key = (change['member'], change['fieldPath'])
            existing = changes.get(key)
            if (existing is not None and existing['replacementValue'] != change[
                    'replacementValue']):
                raise VehicleModificationError(
                    '多个启用方案对字段 %s 给出了冲突值。' % change['fieldPath'])
            changes[key] = dict(change)
    return {
        'profile': first['profile'], 'vehicle': dict(first['vehicle']),
        'presetCategory': ','.join(item['category'] for item in selections),
        'presetLevel': 'combined', 'selections': selections,
        'changes': [changes[key] for key in sorted(changes)],
        'affectedVehicles': sorted(affected),
    }


def plan_text(plan, presets):
    selections = plan.get('selections') or [{
        'category': plan['presetCategory'], 'level': plan['presetLevel']}]
    labels = []
    for selection in selections:
        category = presets['categories'][selection['category']]['tag']
        level = str(selection['level'])
        if level == 'off':
            style = {'name': '取消', 'color_cn': '恢复原值'}
        else:
            style = (presets['level_styles'][level] if level != 'S' else
                     {'name': 'Lv.S', 'color_cn': ''})
        label = '%s %s' % (category, style['name'])
        if style['color_cn']:
            label += '·' + style['color_cn']
        labels.append(label)
    vehicle = plan['vehicle']
    lines = [
        '方案: %s' % plan['profile'],
        '车辆: %s (%s:%s)' % (
            vehicle.get('label', vehicle['vehicle']), vehicle['nation'],
            vehicle['vehicle']),
        '%s: %s' % (
            '取消分类' if plan.get('action') == 'remove' else '启用方案',
            '  '.join(labels)),
        '',
    ]
    for change in plan['changes']:
        lines.append('%s' % change['label'])
        lines.append('  %s → %s' % (
            change['currentValue'], change['replacementValue']))
    lines.extend(['', '共%s %d 个字段。' % (
        '取消' if plan.get('action') == 'remove' else '修改',
        len(plan['changes'])), ''])
    lines.append('受共享模块影响的车辆 (%d):' % len(plan['affectedVehicles']))
    lines.extend('  ' + name for name in plan['affectedVehicles'])
    return '\n'.join(lines)


def _profile_document(service, game_root):
    path = service.profile_store_path(game_root)
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            return path, json.load(stream)
    except (IOError, OSError, ValueError) as error:
        raise VehicleModificationError('无法读取 vehicle_profiles.json: %s' % error)


def _find_profile(document, profile_name):
    matches = [profile for profile in document.get('profiles', ())
               if str(profile.get('name', '')).casefold() ==
               str(profile_name).casefold()]
    if len(matches) != 1:
        raise VehicleModificationError('车辆属性方案不存在: %s' % profile_name)
    return matches[0]


def _safe_name(value):
    cleaned = _SAFE_NAME.sub('_', str(value)).strip('._')
    return cleaned[:80] or 'vehicle'


def _timestamp(now=None):
    now = now or datetime.datetime.now()
    return now.strftime('%Y-%m-%d_%H-%M-%S')


_INDEX_TABLES = (
    'profile_scans', 'vehicle_tags', 'backups', 'manual_tags', 'meta',
    'top_modules', 'vehicle_tag_sources')


def _migrate_legacy_index(connection, legacy_path):
    """Merge the former trainer-only database, then retire it."""
    if (not legacy_path or not os.path.isfile(legacy_path) or
            os.path.normcase(os.path.abspath(legacy_path)) ==
            os.path.normcase(os.path.abspath(
                connection.execute('PRAGMA database_list').fetchone()[2]))):
        return False
    attached = False
    try:
        connection.execute(
            'ATTACH DATABASE ? AS legacy_trainer', (legacy_path,))
        attached = True
        tables = set(row[0] for row in connection.execute(
            "SELECT name FROM legacy_trainer.sqlite_master "
            "WHERE type = 'table'"))
        for table in _INDEX_TABLES:
            if table in tables:
                connection.execute(
                    'INSERT OR REPLACE INTO %s SELECT * FROM legacy_trainer.%s'
                    % (table, table))
        connection.commit()
        connection.execute('DETACH DATABASE legacy_trainer')
        attached = False
    except (IOError, OSError, sqlite3.Error):
        connection.rollback()
        if attached:
            try:
                connection.execute('DETACH DATABASE legacy_trainer')
            except sqlite3.Error:
                pass
        return False
    try:
        os.unlink(legacy_path)
        for suffix in ('-wal', '-shm'):
            sidecar = legacy_path + suffix
            if os.path.isfile(sidecar):
                os.unlink(sidecar)
    except (IOError, OSError):
        # The committed rows are already usable. A locked legacy file can be
        # retired by the next trainer start without repeating expensive work.
        return False
    return True


def ensure_index(path=INDEX_PATH, legacy_path=None):
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS profile_scans (
            profile_name TEXT PRIMARY KEY,
            profile_sha256 TEXT NOT NULL,
            preset_sha256 TEXT NOT NULL,
            scanned_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vehicle_tags (
            profile_name TEXT NOT NULL,
            vehicle_member TEXT NOT NULL,
            category TEXT NOT NULL,
            level TEXT NOT NULL,
            exact INTEGER NOT NULL,
            PRIMARY KEY (profile_name, vehicle_member, category)
        );
        CREATE TABLE IF NOT EXISTS vehicle_tag_sources (
            profile_name TEXT NOT NULL,
            vehicle_member TEXT NOT NULL,
            source_member TEXT NOT NULL,
            source_sha1 TEXT NOT NULL,
            PRIMARY KEY (profile_name, vehicle_member, source_member)
        );
        CREATE TABLE IF NOT EXISTS backups (
            backup_dir TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            profile_name TEXT NOT NULL,
            vehicle_name TEXT NOT NULL,
            category TEXT,
            level TEXT,
            profile_sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manual_tags (
            profile_name TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            vehicle_member TEXT NOT NULL,
            category TEXT NOT NULL,
            level TEXT NOT NULL,
            PRIMARY KEY (profile_name, vehicle_member, category)
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS top_modules (
            vehicle_member TEXT PRIMARY KEY,
            data TEXT NOT NULL
        );
    ''')
    connection.commit()
    if legacy_path is None and os.path.normcase(os.path.abspath(path)) == \
            os.path.normcase(os.path.abspath(INDEX_PATH)):
        legacy_path = LEGACY_INDEX_PATH
    _migrate_legacy_index(connection, legacy_path)
    return connection


# ---- elite (top-of-tree) module resolution --------------------------------

_PACKAGE_SIGNATURE_KEY = 'scripts_pkg'


def _top_modules_cached(service, game_root, member, connection):
    """Elite-module map for one vehicle, persisted in the trainer index."""
    signature = json.dumps(service.source_package_signature(game_root))
    row = connection.execute(
        'SELECT value FROM meta WHERE key = ?',
        (_PACKAGE_SIGNATURE_KEY,)).fetchone()
    if row is None or row[0] != signature:
        # The stock package changed: every cached resolution is suspect.
        connection.execute('DELETE FROM top_modules')
        connection.execute(
            'INSERT OR REPLACE INTO meta VALUES (?, ?)',
            (_PACKAGE_SIGNATURE_KEY, signature))
        connection.commit()
    row = connection.execute(
        'SELECT data FROM top_modules WHERE vehicle_member = ?',
        (member,)).fetchone()
    if row is not None:
        return json.loads(row[0])
    tops = service.vehicle_top_components(game_root, member)
    connection.execute(
        'INSERT OR REPLACE INTO top_modules VALUES (?, ?)', (
            member, json.dumps(tops, ensure_ascii=False, sort_keys=True)))
    connection.commit()
    return tops


def top_modules(service, game_root, member, index_path=INDEX_PATH):
    connection = ensure_index(index_path)
    try:
        return _top_modules_cached(service, game_root, member, connection)
    finally:
        connection.close()


def _field_module_key(field):
    """Identify the module one editable field belongs to, if any."""
    category = field.get('category')
    parts = str(field.get('fieldPath', '')).split('/')
    if not field.get('shared'):
        if category == 'chassis':
            return ('component', 'chassis',
                    parts[1] if len(parts) > 1 else None)
        if category == 'turret':
            return ('component', 'turrets',
                    parts[1] if len(parts) > 1 else None)
        if category == 'guns':
            return ('gun_local', parts[1] if len(parts) > 1 else None,
                    parts[3] if len(parts) > 3 else None)
        return None
    if category in ('chassis', 'engines', 'fuelTanks', 'radios', 'turrets'):
        return ('component', category, field.get('component'))
    if category == 'guns':
        return ('gun_shared', field.get('component'))
    if category == 'shells':
        return ('shell', field.get('component'))
    return None


def filter_top_module_fields(fields, tops):
    """Keep vehicle-level fields and fields of the elite modules only."""
    if not tops:
        return list(fields)
    shells = set(tops.get('shells') or ())
    result = []
    for field in fields:
        key = _field_module_key(field)
        if key is None:
            result.append(field)
            continue
        kind = key[0]
        if kind == 'component':
            if key[2] is None or key[2] == tops.get(key[1]):
                result.append(field)
        elif kind == 'gun_shared':
            if key[1] is None or key[1] == tops.get('guns'):
                result.append(field)
        elif kind == 'gun_local':
            if ((key[1] is None or key[1] == tops.get('turrets')) and
                    (key[2] is None or key[2] == tops.get('guns'))):
                result.append(field)
        elif kind == 'shell':
            if key[1] is None or key[1] in shells:
                result.append(field)
    return result


def _elite_fields(service, game_root, vehicle, fields):
    """Restrict editable fields to the vehicle's elite modules.

    A vehicle whose elite modules cannot be resolved keeps every field, so
    an unexpected topology never blocks editing.
    """
    resolver = getattr(service, 'vehicle_top_components', None)
    if resolver is None:
        return fields
    try:
        tops = top_modules(service, game_root, vehicle['member'])
    except Exception:
        return fields
    return filter_top_module_fields(fields, tops)


def backup_profile(service, game_root, profile_name, vehicle_name,
                   category=None, level=None, backup_root=BACKUP_ROOT,
                   index_path=INDEX_PATH, now=None):
    unused_path, document = _profile_document(service, game_root)
    profile = copy.deepcopy(_find_profile(document, profile_name))
    stamp = _timestamp(now)
    leaf = '%s_before_%s' % (stamp, _safe_name(vehicle_name))
    if not os.path.isdir(backup_root):
        os.makedirs(backup_root)
    directory = os.path.join(backup_root, leaf)
    suffix = 2
    while os.path.exists(directory):
        directory = os.path.join(backup_root, '%s_%d' % (leaf, suffix))
        suffix += 1
    os.makedirs(directory)
    payload = {'schema': 1, 'profile': profile}
    profile_path = os.path.join(directory, 'profile.json')
    metadata_path = os.path.join(directory, 'metadata.json')
    with open(profile_path, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write('\n')
    metadata = {
        'schema': 1,
        'createdAt': (now or datetime.datetime.now()).isoformat(),
        'profileName': profile['name'],
        'vehicleName': str(vehicle_name),
        'category': category,
        'level': None if level is None else str(level),
        'profileSha256': _digest(profile),
    }
    with open(metadata_path, 'w', encoding='utf-8') as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write('\n')
    connection = ensure_index(index_path)
    try:
        connection.execute(
            'INSERT INTO backups VALUES (?, ?, ?, ?, ?, ?, ?)', (
                directory, metadata['createdAt'], profile['name'],
                metadata['vehicleName'], category, metadata['level'],
                metadata['profileSha256']))
        connection.commit()
    finally:
        connection.close()
    return directory


def list_backups(backup_root=BACKUP_ROOT):
    try:
        names = sorted(os.listdir(backup_root), reverse=True)
    except (IOError, OSError):
        return []
    result = []
    for name in names:
        directory = os.path.join(backup_root, name)
        try:
            with open(os.path.join(directory, 'metadata.json'), 'r',
                      encoding='utf-8') as stream:
                metadata = json.load(stream)
            with open(os.path.join(directory, 'profile.json'), 'r',
                      encoding='utf-8') as stream:
                payload = json.load(stream)
            if payload.get('profile', {}).get('name') != metadata.get('profileName'):
                continue
        except (IOError, OSError, ValueError, AttributeError):
            continue
        item = dict(metadata)
        item['directory'] = directory
        result.append(item)
    return result


def _atomic_json(path, value):
    temporary = path + '.trainer.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2,
                      sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_backup(service, game_root, backup, backup_root=BACKUP_ROOT,
                   index_path=INDEX_PATH):
    directory = backup['directory'] if isinstance(backup, dict) else backup
    absolute_root = os.path.abspath(backup_root)
    absolute = os.path.abspath(directory)
    if os.path.commonpath((absolute_root, absolute)) != absolute_root:
        raise VehicleModificationError('备份路径不在 trainer 备份目录中。')
    try:
        with open(os.path.join(absolute, 'profile.json'), 'r',
                  encoding='utf-8') as stream:
            saved = json.load(stream)['profile']
    except (IOError, OSError, ValueError, KeyError, TypeError) as error:
        raise VehicleModificationError('车辆方案备份损坏: %s' % error)
    if service.core.game_is_running():
        raise VehicleModificationError('客户端正在运行，请先关闭游戏。')
    path, document = _profile_document(service, game_root)
    current = _find_profile(document, saved['name'])
    backup_profile(service, game_root, current['name'],
                   'restore-%s' % current['name'], 'restore', None,
                   backup_root, index_path)
    index = document['profiles'].index(current)
    document['profiles'][index] = saved
    validator = getattr(service, '_validate_profile_store', None)
    if callable(validator):
        try:
            validator(copy.deepcopy(document))
        except service.VehicleOverlayError as error:
            raise VehicleModificationError(
                '车辆方案备份未通过格式校验: %s' % error)
    _atomic_json(path, document)
    return saved['name']


def apply_plan(service, game_root, plan, backup_root=BACKUP_ROOT,
               index_path=INDEX_PATH):
    if not plan['changes']:
        return None
    vehicle = plan['vehicle']
    backup = backup_profile(
        service, game_root, plan['profile'],
        vehicle.get('label', vehicle['vehicle']), plan['presetCategory'],
        plan['presetLevel'], backup_root, index_path)
    try:
        if plan.get('action') == 'remove':
            remove = getattr(service, 'remove_profile_edits', None)
            if remove is None:
                raise VehicleModificationError(
                    '当前车辆数据服务不支持按分类取消修改。')
            remove(game_root, plan['profile'], plan['changes'])
        else:
            batch = getattr(service, 'apply_profile_edits', None)
            if batch is not None:
                batch(game_root, plan['profile'], plan['changes'])
            else:
                for change in plan['changes']:
                    service.apply_profile_edit(
                        game_root, plan['profile'], change['member'],
                        change['fieldPath'], change['replacementValue'])
    except Exception:
        # Each public edit is atomic, but a preset spans many edits. Restore the
        # pre-operation profile so a failed field cannot leave a half-preset.
        path, document = _profile_document(service, game_root)
        with open(os.path.join(backup, 'profile.json'), 'r',
                  encoding='utf-8') as stream:
            saved = json.load(stream)['profile']
        current = _find_profile(document, plan['profile'])
        document['profiles'][document['profiles'].index(current)] = saved
        _atomic_json(path, document)
        raise
    selections = plan.get('selections') or [{
            'category': plan.get('presetCategory'),
            'level': plan.get('presetLevel')}]
    manual_selections = [selection for selection in selections
                         if str(selection.get('level')) == 'S']
    vehicle_member = vehicle.get('member')
    if selections and vehicle_member:
        unused_path, document = _profile_document(service, game_root)
        profile_hash = _digest(_find_profile(document, plan['profile']))
        connection = ensure_index(index_path)
        try:
            connection.executemany(
                'DELETE FROM manual_tags WHERE profile_name = ? '
                'AND vehicle_member = ? AND category = ?', [
                    (plan['profile'], vehicle_member,
                     selection.get('category'))
                    for selection in selections
                    if selection.get('category')])
            connection.executemany(
                'INSERT OR REPLACE INTO manual_tags VALUES (?, ?, ?, ?, ?)', [
                    (plan['profile'], profile_hash, vehicle_member,
                     selection['category'], 'S')
                    for selection in manual_selections])
            connection.commit()
        finally:
            connection.close()
    return backup


def evaluate_tags(fields, presets):
    """Conservatively classify current field values against preset levels."""
    tags = []
    for category, definition in presets['categories'].items():
        modified = [field for field in fields if not _same_numeric(
            field.get('currentValue', field['originalValue']),
            field['originalValue']) and any(rule_matches(field, rule)
                                            for level in ('1', '2', '3')
                                            for rule in level_rules(
                                                presets, category, level))]
        if not modified:
            continue
        exact_levels = []
        partial_levels = []
        for level in ('1', '2', '3'):
            rules = level_rules(presets, category, level)
            applicable = [(field, rule) for field in fields for rule in rules
                          if rule_matches(field, rule)]
            if not applicable:
                continue
            matched = sum(_same_numeric(
                field.get('currentValue', field['originalValue']),
                apply_operation(field['originalValue'], rule))
                          for field, rule in applicable)
            if matched == len(applicable):
                exact_levels.append(str(level))
            elif matched:
                partial_levels.append((matched / float(len(applicable)), str(level)))
        if exact_levels:
            level = exact_levels[-1]
            exact = True
        elif partial_levels:
            level = max(partial_levels)[1]
            exact = False
        else:
            level = 'custom'
            exact = False
        tags.append({'category': category, 'tag': definition['tag'],
                     'level': level, 'exact': exact})
    return tags


def format_tags(tags, presets):
    rendered = []
    for tag in tags:
        if tag['level'] in ('custom', 'S'):
            rendered.append('[%s Lv.S]' % tag['tag'])
            continue
        style = presets['level_styles'][str(tag['level'])]
        suffix = '' if tag.get('exact') else '·混合'
        rendered.append('[%s %s·%s%s]' % (
            tag['tag'], style['name'], style['color_cn'], suffix))
    return ' '.join(rendered)


class VehicleTagIndex(object):
    """Lazy SQLite-backed tag evaluation with per-member invalidation.

    Each vehicle records the SHA-1 of every profile ``sourceMember`` section
    used to evaluate it.  A profile edit therefore expires only vehicles that
    depend on the changed sections; timestamps and unrelated vehicle edits do
    not invalidate the row.
    """

    def __init__(self, service, game_root, profile_name, presets,
                 index_path=INDEX_PATH):
        self.service = service
        self.game_root = game_root
        self.profile_name = profile_name
        self.presets = presets
        unused_path, document = _profile_document(service, game_root)
        profile = _find_profile(document, profile_name)
        self.profile_hash = _digest(profile)
        self.preset_hash = _digest(presets)
        self.member_hashes = dict(
            (entry['sourceMember'], _section_sha1(entry))
            for entry in profile.get('members', ()))
        self._tops_cache = {}
        self.connection = ensure_index(index_path)
        row = self.connection.execute(
            'SELECT profile_sha256, preset_sha256 FROM profile_scans '
            'WHERE profile_name = ?', (profile_name,)).fetchone()
        # Preset rules change the meaning of every tag.  Profile changes are
        # handled below at sourceMember-section granularity instead.
        if row is not None and row[1] != self.preset_hash:
            self.connection.execute(
                'DELETE FROM vehicle_tags WHERE profile_name = ?',
                (profile_name,))
            self.connection.execute(
                'DELETE FROM vehicle_tag_sources WHERE profile_name = ?',
                (profile_name,))
        if row != (self.profile_hash, self.preset_hash):
            self.connection.execute(
                'INSERT OR REPLACE INTO profile_scans VALUES (?, ?, ?, ?)', (
                    profile_name, self.profile_hash, self.preset_hash,
                    datetime.datetime.now().isoformat()))
        self.connection.commit()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _tops(self, member):
        """Elite-module map for one vehicle, memoized and sqlite-backed."""
        if member not in self._tops_cache:
            resolver = getattr(self.service, 'vehicle_top_components', None)
            tops = None
            if resolver is not None:
                try:
                    tops = _top_modules_cached(
                        self.service, self.game_root, member, self.connection)
                except Exception:
                    tops = None
            self._tops_cache[member] = tops
        return self._tops_cache[member]

    def _evaluate_record(self, vehicle):
        try:
            fields = self.service.list_vehicle_profile_field_choices(
                self.game_root, self.profile_name, vehicle['member'])
        except self.service.VehicleOverlayError as error:
            raise VehicleModificationError(str(error))
        tops = self._tops(vehicle['member'])
        if tops:
            fields = filter_top_module_fields(fields, tops)
        members = set(field.get('member') for field in fields
                      if field.get('member'))
        members.add(vehicle['member'])
        sources = dict(
            (member, self.member_hashes.get(member, 'absent'))
            for member in members)
        return evaluate_tags(fields, self.presets), sources

    def _evaluate(self, vehicle):
        return self._evaluate_record(vehicle)[0]

    def _store(self, vehicle, tags, sources):
        # An empty result needs a durable sentinel or it would be recalculated
        # on every redraw. The private category cannot collide with presets.
        stored = tags or [{'category': '__none__', 'level': '', 'exact': True}]
        self.connection.execute(
            'DELETE FROM vehicle_tags '
            'WHERE profile_name = ? AND vehicle_member = ?',
            (self.profile_name, vehicle['member']))
        self.connection.execute(
            'DELETE FROM vehicle_tag_sources '
            'WHERE profile_name = ? AND vehicle_member = ?',
            (self.profile_name, vehicle['member']))
        self.connection.executemany(
            'INSERT OR REPLACE INTO vehicle_tags VALUES (?, ?, ?, ?, ?)', [
                (self.profile_name, vehicle['member'], tag['category'],
                 tag['level'], int(bool(tag['exact']))) for tag in stored])
        self.connection.executemany(
            'INSERT OR REPLACE INTO vehicle_tag_sources VALUES (?, ?, ?, ?)', [
                (self.profile_name, vehicle['member'], member, sha1)
                for member, sha1 in sorted(sources.items())])
        self.connection.commit()

    def _is_stale(self, vehicle):
        cached = self.connection.execute(
            'SELECT 1 FROM vehicle_tags '
            'WHERE profile_name = ? AND vehicle_member = ? LIMIT 1',
            (self.profile_name, vehicle['member'])).fetchone()
        if cached is None:
            return True
        rows = self.connection.execute(
            'SELECT source_member, source_sha1 FROM vehicle_tag_sources '
            'WHERE profile_name = ? AND vehicle_member = ?',
            (self.profile_name, vehicle['member'])).fetchall()
        if not rows:
            return True
        return any(self.member_hashes.get(member, 'absent') != sha1
                   for member, sha1 in rows)

    def stale_vehicles(self, vehicles):
        """Vehicles whose tags are not cached for this profile snapshot."""
        return [vehicle for vehicle in vehicles if self._is_stale(vehicle)]

    def prune_vehicles(self, vehicles):
        """Drop cached rows for vehicles no longer in the roster."""
        keep = set(vehicle['member'] for vehicle in vehicles)
        rows = self.connection.execute(
            'SELECT vehicle_member FROM vehicle_tags WHERE profile_name = ?',
            (self.profile_name,)).fetchall()
        stale = [member for (member,) in rows if member not in keep]
        if stale:
            self.connection.executemany(
                'DELETE FROM vehicle_tags '
                'WHERE profile_name = ? AND vehicle_member = ?',
                [(self.profile_name, member) for member in stale])
            self.connection.executemany(
                'DELETE FROM vehicle_tag_sources '
                'WHERE profile_name = ? AND vehicle_member = ?',
                [(self.profile_name, member) for member in stale])
            self.connection.commit()
        return len(stale)

    def preload(self, vehicles, progress=None):
        """Evaluate and cache tags for every vehicle missing from the index.

        ``progress(done, total, vehicle)`` runs after each evaluation;
        returning False aborts the preload.  Vehicles that fail evaluation
        stay uncached so the picker can report the error lazily.
        """
        pending = self.stale_vehicles(vehicles)
        total = len(pending)
        done = 0
        for vehicle in pending:
            try:
                tags, sources = self._evaluate_record(vehicle)
            except VehicleModificationError:
                tags = None
            if tags is not None:
                self._store(vehicle, tags, sources)
            done += 1
            if progress is not None and progress(done, total, vehicle) is False:
                break
        return done, total

    def resync(self):
        """Adopt the current profile/preset hashes without dropping rows.

        Used right after a successful apply: the caller refreshes the
        affected vehicles itself, and every other cached row is untouched
        by that edit, so a full invalidation would waste the cache.
        """
        unused_path, document = _profile_document(self.service, self.game_root)
        profile = _find_profile(document, self.profile_name)
        profile_hash = _digest(profile)
        preset_hash = _digest(self.presets)
        if (profile_hash, preset_hash) == (self.profile_hash,
                                           self.preset_hash):
            return False
        self.profile_hash = profile_hash
        self.preset_hash = preset_hash
        self.member_hashes = dict(
            (entry['sourceMember'], _section_sha1(entry))
            for entry in profile.get('members', ()))
        self.connection.execute(
            'INSERT OR REPLACE INTO profile_scans VALUES (?, ?, ?, ?)', (
                self.profile_name, profile_hash, preset_hash,
                datetime.datetime.now().isoformat()))
        self.connection.commit()
        return True

    def refresh(self, vehicles):
        """Re-evaluate and store tags for the given vehicles."""
        for vehicle in vehicles:
            try:
                tags, sources = self._evaluate_record(vehicle)
            except VehicleModificationError:
                continue
            self._store(vehicle, tags, sources)

    def tags(self, vehicle):
        if self._is_stale(vehicle):
            tags, sources = self._evaluate_record(vehicle)
            self._store(vehicle, tags, sources)
        return self.cached_tags(vehicle)

    def cached_tags(self, vehicle):
        """Return already indexed tags without opening scripts.pkg."""
        if self._is_stale(vehicle):
            return []
        rows = self.connection.execute(
            'SELECT category, level, exact FROM vehicle_tags '
            'WHERE profile_name = ? AND vehicle_member = ? '
            'ORDER BY category',
            (self.profile_name, vehicle['member'])).fetchall()
        by_category = self.presets['categories']
        tags = [
            {'category': category, 'tag': by_category[category]['tag'],
             'level': level, 'exact': bool(exact)}
            for category, level, exact in rows if category in by_category]
        manual = self.connection.execute(
            'SELECT category, level FROM manual_tags WHERE profile_name = ? '
            'AND vehicle_member = ?',
            (self.profile_name, vehicle['member'])).fetchall()
        overrides = dict(manual)
        if overrides:
            tags = [tag for tag in tags if tag['category'] not in overrides]
            tags.extend({
                'category': category, 'tag': by_category[category]['tag'],
                'level': level, 'exact': True,
            } for category, level in manual if category in by_category)
        return tags


def default_profile(service, game_root):
    names = service.list_vehicle_profiles(game_root)
    if 'SPG' in names:
        return 'SPG'
    return names[0] if names else None
