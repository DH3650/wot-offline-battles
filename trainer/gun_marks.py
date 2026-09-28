"""Read saved gun-mark progress and project future battles (read-only)."""

from __future__ import annotations

import importlib.util
import json
import math
import os


POSTBATTLE_FILE_NAME = 'postbattle_state.json'
MARK_PERCENTILES = (65, 85, 95)
MAX_MOVING_AVERAGE = 60001
MAX_PROJECTION_BATTLES = 100000
SMOOTHING_NUMERATOR = 2
SMOOTHING_DENOMINATOR = 101

_CATALOGUE = None


class GunMarksError(ValueError):
    pass


def catalogue_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'src', 'res', 'scripts', 'client', 'gui', 'mods',
        'offline_lan_0922', 'mastery_catalog.py')


def load_catalogue(path=None):
    """Load the generated threshold table without importing client code."""
    global _CATALOGUE
    if path is None and _CATALOGUE is not None:
        return _CATALOGUE
    path = path or catalogue_path()
    if not os.path.isfile(path):
        raise GunMarksError('找不到伤害标记阈值表: %s' % path)
    try:
        spec = importlib.util.spec_from_file_location(
            'trainer_mastery_catalog', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (IOError, OSError, SyntaxError, ImportError, AttributeError) as error:
        raise GunMarksError('无法读取伤害标记阈值表: %s' % error)
    if path == catalogue_path():
        _CATALOGUE = module
    return module


def postbattle_path(garage_path):
    """Return the post-battle file beside a slot's garage file."""
    return os.path.join(os.path.dirname(os.path.abspath(garage_path)),
                        POSTBATTLE_FILE_NAME)


def load_progress(path):
    """Return the persisted per-vehicle result rows."""
    if not os.path.isfile(path) or os.path.islink(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError, UnicodeError) as error:
        raise GunMarksError('无法读取战斗记录: %s' % error)
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise GunMarksError('战斗记录格式不受支持: %s' % path)
    progress = value.get('progress')
    vehicles = progress.get('vehicles') if isinstance(progress, dict) else None
    if not isinstance(vehicles, dict):
        return {}
    return dict((name, row) for name, row in vehicles.items()
                if isinstance(name, str) and isinstance(row, dict))


def _whole(value, maximum=None):
    if isinstance(value, bool):
        value = 0
    try:
        value = max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        value = 0
    return min(value, maximum) if maximum is not None else value


def next_average(previous, combined_damage):
    """Apply the game's exact integer ``2 / 101`` EMA update once."""
    previous = _whole(previous, MAX_MOVING_AVERAGE)
    combined_damage = _whole(combined_damage)
    numerator = (SMOOTHING_NUMERATOR * combined_damage +
                 (SMOOTHING_DENOMINATOR - SMOOTHING_NUMERATOR) * previous)
    # Denominator 101 is odd, so this value can never land exactly on x.5.
    average = int(math.floor(
        float(numerator) / SMOOTHING_DENOMINATOR + 0.5))
    return min(average, MAX_MOVING_AVERAGE)


def minimum_combined_for_next_battle(previous, target):
    """Least combined damage that reaches ``target`` after one battle."""
    previous = _whole(previous, MAX_MOVING_AVERAGE)
    target = _whole(target, MAX_MOVING_AVERAGE)
    if previous >= target:
        return 0
    low, high = 0, max(1, target)
    while next_average(previous, high) < target:
        high *= 2
    while low < high:
        middle = (low + high) // 2
        if next_average(previous, middle) >= target:
            high = middle
        else:
            low = middle + 1
    return low


def battles_to_target(previous, combined_damage, target):
    """Return battle count, or ``None`` if this pace stabilizes below target."""
    average = _whole(previous, MAX_MOVING_AVERAGE)
    combined_damage = _whole(combined_damage)
    target = _whole(target, MAX_MOVING_AVERAGE)
    if average >= target:
        return 0
    for battle in range(1, MAX_PROJECTION_BATTLES + 1):
        updated = next_average(average, combined_damage)
        if updated >= target:
            return battle
        if updated == average:
            return None
        average = updated
    return None


def _curve(info, catalogue):
    if info is None or int(info.tier) < 5:
        return None
    curve = catalogue.MARKS_DAMAGE.get(int(info.type_cd))
    if curve is not None:
        return tuple(curve)
    return catalogue.MARKS_DAMAGE_FALLBACK.get(
        (int(info.tier), str(info.clazz)))


def vehicle_states(vehicles, progress=None, owned_type_cds=None,
                   catalogue=None):
    """Join Trainer vehicles, one save's progress and retail thresholds."""
    catalogue = catalogue or load_catalogue()
    progress = progress if isinstance(progress, dict) else {}
    owned = (None if owned_type_cds is None else
             set(str(value) for value in owned_type_cds))
    positions = dict((percentile, index) for index, percentile in
                     enumerate(catalogue.MARKS_PERCENTILES))
    states = []
    for type_cd, info in vehicles.items():
        if owned is not None and str(type_cd) not in owned:
            continue
        curve = _curve(info, catalogue)
        if not curve:
            continue
        try:
            thresholds = tuple(_whole(curve[positions[p]])
                               for p in MARK_PERCENTILES)
        except (IndexError, KeyError, TypeError):
            continue
        type_name = '%s:%s' % (info.nation, info.key)
        row = progress.get(type_name)
        row = row if isinstance(row, dict) else {}
        states.append({
            'typeCD': int(type_cd),
            'typeName': type_name,
            'info': info,
            'thresholds': thresholds,
            'movingAvgDamage': _whole(
                row.get('movingAvgDamage'), MAX_MOVING_AVERAGE),
            'marksOnGun': min(_whole(row.get('marksOnGun')), 3),
            'damageRating': min(_whole(row.get('damageRating')), 10000),
            'battles': _whole(row.get('battles')),
        })
    return sorted(states, key=lambda state: (
        0 if state['battles'] else 1,
        state['info'].nation_id, -state['info'].tier, state['info'].name))


def projection(state, planned_combined_damage):
    """Return next-mark progress at a sustained combined-damage pace."""
    average = _whole(state.get('movingAvgDamage'), MAX_MOVING_AVERAGE)
    marks = min(_whole(state.get('marksOnGun')), 3)
    thresholds = tuple(_whole(value) for value in state.get('thresholds', ()))
    if len(thresholds) != 3:
        raise GunMarksError('该车辆没有完整的三环阈值。')
    planned = _whole(planned_combined_damage)
    result = {
        'currentAverage': average,
        'currentMarks': marks,
        'thresholds': thresholds,
        'plannedCombinedDamage': planned,
        'nextAverage': next_average(average, planned),
        'nextMark': None,
        'target': None,
        'averageRemaining': 0,
        'minimumNextCombinedDamage': 0,
        'battlesAtPlannedDamage': 0,
    }
    if marks >= 3:
        return result
    target = thresholds[marks]
    result.update({
        'nextMark': marks + 1,
        'target': target,
        'averageRemaining': max(0, target - average),
        'minimumNextCombinedDamage': minimum_combined_for_next_battle(
            average, target),
        'battlesAtPlannedDamage': battles_to_target(
            average, planned, target),
    })
    return result


def report_text(state, result):
    """Format a complete Chinese report for Trainer's text viewer."""
    info = state['info']
    thresholds = result['thresholds']
    lines = [
        '车辆: %s  [%s]  %d级' % (info.name, state['typeName'], info.tier),
        '当前移动场均: %d' % result['currentAverage'],
        '当前伤害评级: %.2f%%' % (state.get('damageRating', 0) / 100.0),
        '已获得标记: %d 环' % result['currentMarks'],
        '该车已记录战斗: %d 场' % state.get('battles', 0),
        '',
        '一环线 (65%%): %d' % thresholds[0],
        '二环线 (85%%): %d' % thresholds[1],
        '三环线 (95%%): %d' % thresholds[2],
        '',
        '预计以后每场综合伤害: %d' % result['plannedCombinedDamage'],
        '下一场结束后的移动场均: %d' % result['nextAverage'],
    ]
    if result['nextMark'] is None:
        lines.extend(('', '该车辆已经获得三环；标记不会因场均下降而失去。'))
    else:
        lines.extend((
            '',
            '下一目标: 第 %d 环，阈值 %d' % (
                result['nextMark'], result['target']),
            '当前场均距离目标还差: %d' % result['averageRemaining'],
            '若想下一场立即过线，最低需要综合伤害: %d' %
            result['minimumNextCombinedDamage'],
        ))
        battles = result['battlesAtPlannedDamage']
        if battles is None:
            lines.append('按 %d 综合伤害持续打：无法达到下一环。' %
                         result['plannedCombinedDamage'])
        else:
            lines.append('按 %d 综合伤害持续打：预计还需要 %d 场。' % (
                result['plannedCombinedDamage'], battles))
    lines.extend((
        '',
        '综合伤害 = 直接伤害 + 履带/点亮/震晕协助中的最大一项。',
        '这里的“场均”是游戏保存的 100 场指数移动平均，不是普通算术平均。',
        '本页面只读取存档，不会修改任何成绩。',
    ))
    return '\n'.join(lines)
