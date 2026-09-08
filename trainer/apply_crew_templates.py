"""Apply per-role crew skill templates to the offline garage save.

Reads ``garage_state.json``, selects vehicles by nation / tier / class /
name filters, and for every crew member who has NOT learned any skill yet
writes the template for that slot's role combination: seven trained skills
at 100% plus one skill in training at 0% (byte-identical in shape to what
the stock client itself stores).

Safety:

- dry-run by default; ``--apply`` is required to write;
- refuses to write while ``WorldOfTanks.exe`` is running (the client
  overwrites the same file on every garage action);
- before writing, the save is copied to
  ``garage_state.json.trainer-backup-<timestamp>``;
- the write itself is atomic (temp file + replace, .bak fallback), same
  protocol as the client's own ``config.write_json``.

Usage::

    python -m trainer.apply_crew_templates                 # dry-run report
    python -m trainer.apply_crew_templates --nation china --tier 8-10
    python -m trainer.apply_crew_templates --vehicles "T-34" --apply
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import datetime
import json
import os
import shutil
import subprocess
import sys

from trainer.skills_db import (
    ROLE_NAMES_CN, SKILL_NAMES_CN, SKILLS_BY_ROLES, describe_skill,
    describe_role, skills_for_roles)
from trainer.tankman_codec import (
    TankmanFormatError, parse_tankman, serialize_tankman, with_skills)
from trainer.vehicle_db import (
    CLASS_NAMES_CN, NATION_NAMES_CN, NATIONS, build_vehicle_db,
    load_vehicle_db, save_vehicle_db)

DEFAULT_GARAGE = os.path.join(
    os.environ.get('APPDATA', ''), 'Wargaming.net', 'WorldOfTanks',
    'offline_lan_0922', 'garage_state.json')
DEFAULT_CLIENT_DIR = r'C:\games\wot_0.9.22_cn'
DEFAULT_TEMPLATES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'templates.json')
DEFAULT_VEHICLE_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'vehicle_db.json')

TRAINED_SKILL_COUNT = 7
CLIENT_PROCESSES = ('WorldOfTanks.exe',)


class TemplateError(ValueError):
    pass


# ---- templates ------------------------------------------------------------

def normalize_combo(roles):
    """Canonical template key: primary role + sorted secondary roles."""
    roles = [str(role) for role in roles]
    if not roles:
        raise TemplateError('empty role combination')
    return '+'.join([roles[0]] + sorted(roles[1:]))


def load_templates(path):
    """Load and validate the template file.

    Format::

        {"templates": {
            "<combo>": {"trained": [7 skill names], "training": "<name>"}}}

    ``trained`` must hold exactly seven distinct skills learnable by the
    role combination.  ``training`` names the eighth, in-training skill; it
    is required when the combination offers more than seven skills and must
    be null otherwise (a pure loader only has seven skills available).
    """
    with open(path, 'r', encoding='utf-8') as stream:
        payload = json.load(stream)
    raw = payload.get('templates')
    if not isinstance(raw, dict) or not raw:
        raise TemplateError('%s: no "templates" object' % path)
    templates = {}
    errors = []
    for combo, entry in raw.items():
        try:
            key = normalize_combo(combo.split('+'))
        except TemplateError as error:
            errors.append('%s: %s' % (combo, error))
            continue
        roles = key.split('+')
        available = skills_for_roles(roles)
        if len(available) == 0:
            errors.append('%s: unknown roles' % combo)
            continue
        trained = list(entry.get('trained') or ())
        training = entry.get('training')
        problems = []
        if len(trained) != TRAINED_SKILL_COUNT:
            problems.append(
                'needs exactly %d trained skills, got %d' % (
                    TRAINED_SKILL_COUNT, len(trained)))
        unknown = [s for s in trained + ([training] if training else [])
                   if s not in available]
        if unknown:
            problems.append(
                'not learnable by %s: %s' % (
                    key, ', '.join(
                        '%s' % describe_skill(s) for s in unknown)))
        seen = set()
        duplicates = [s for s in trained + ([training] if training else [])
                      if s in seen or seen.add(s)]
        if duplicates:
            problems.append('duplicate skills: %s' % ', '.join(duplicates))
        if len(available) > TRAINED_SKILL_COUNT and not training:
            problems.append(
                'this role combination offers %d skills; name the in-'
                'training one in "training"' % len(available))
        if len(available) <= TRAINED_SKILL_COUNT and training:
            problems.append(
                'this role combination only offers %d skills; "training" '
                'must be null' % len(available))
        if problems:
            errors.append('%s: %s' % (combo, '; '.join(problems)))
            continue
        templates[key] = {'trained': trained, 'training': training}
    if errors:
        raise TemplateError(
            'invalid templates:\n  ' + '\n  '.join(sorted(errors)))
    return templates


def save_templates(path, templates):
    """Persist ``{combo: {'trained': [...], 'training': name|None}}``."""
    payload = {
        '_comment': [
            '乘员技能模板: 键 = 角色组合 (主角色在前, 兼职角色按字母序), 用 + 连接。',
            'trained = 7 个生效技能 (全部 100%); training = 第 8 个在练技能 (0%)。',
            '该角色组合总共只有 7 个可学技能时 (纯装填手), training 为 null。',
            '可用 TUI 编辑: python -m trainer.tui → 编辑技能模板。',
        ],
        'templates': {
            combo: {'trained': list(entry['trained']),
                    'training': entry.get('training')}
            for combo, entry in sorted(templates.items())
        },
    }
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


# ---- filters --------------------------------------------------------------

def _alias_map(mapping):
    aliases = {}
    for key, cn in mapping.items():
        aliases[key.lower()] = key
        aliases[cn] = key
    return aliases


def _parse_csv(values):
    result = []
    for value in values or ():
        result.extend(
            part.strip() for part in str(value).split(',') if part.strip())
    return result


def build_filters(args):
    nation_aliases = _alias_map(NATION_NAMES_CN)
    class_aliases = _alias_map(CLASS_NAMES_CN)

    nations = set()
    for token in _parse_csv(args.nation):
        key = nation_aliases.get(token.lower())
        if key is None:
            raise TemplateError('unknown nation %r' % token)
        nations.add(key)

    tiers = set()
    for token in _parse_csv(args.tier):
        if '-' in token:
            low, high = token.split('-', 1)
            tiers.update(range(int(low), int(high) + 1))
        else:
            tiers.add(int(token))

    classes = set()
    for token in _parse_csv(args.class_):
        key = class_aliases.get(token.lower())
        if key is None:
            raise TemplateError('unknown vehicle class %r' % token)
        classes.add(key)

    names = [token.lower() for token in _parse_csv(args.vehicles)]

    def matches(info):
        if nations and info.nation not in nations:
            return False
        if tiers and info.tier not in tiers:
            return False
        if classes and info.clazz not in classes:
            return False
        if names:
            haystacks = (
                info.key.lower(), info.name.lower(),
                info.user_string.lower())
            if not any(
                    any(wanted in hay for hay in haystacks)
                    for wanted in names):
                return False
        return True

    return matches


# ---- planning -------------------------------------------------------------

def plan_vehicle(info, record, templates, overwrite=False):
    """Return (changes, skips) for one saved vehicle.

    ``changes`` are ``(slot, role_combo, trained, training, new_base64)``;
    ``skips`` are ``(slot, reason)``.  By default a crew member who already
    learned any skill is skipped; with ``overwrite=True`` their skills are
    replaced by the template instead.
    """
    changes, skips = [], []
    crew = record.get('crew')
    if not isinstance(crew, dict) or not crew:
        return changes, [('-', 'no crew stored')]
    if len(crew) != len(info.crew):
        skips.append((
            '-', 'saved crew size %d != definition %d' % (
                len(crew), len(info.crew))))
    for raw_slot, encoded in sorted(crew.items(), key=lambda kv: int(kv[0])):
        slot = int(raw_slot)
        if slot >= len(info.crew):
            skips.append((slot, 'slot outside vehicle definition'))
            continue
        roles = info.crew[slot]
        combo = normalize_combo(roles)
        try:
            tankman = parse_tankman(base64.b64decode(encoded))
        except TankmanFormatError as error:
            skips.append((slot, 'undecodable crew: %s' % error))
            continue
        if tankman.skills and not overwrite:
            skips.append((
                slot, 'already has %d skill(s); skipped' % len(
                    tankman.skills)))
            continue
        if tankman.role != roles[0]:
            skips.append((
                slot, 'role mismatch: save=%s definition=%s' % (
                    tankman.role, roles[0])))
            continue
        template = templates.get(combo)
        if template is None:
            skips.append((slot, 'no template for %s' % combo))
            continue
        updated = with_skills(
            tankman, template['trained'], template['training'])
        changes.append((
            slot, combo, template['trained'], template['training'],
            base64.b64encode(serialize_tankman(updated)).decode('ascii')))
    return changes, skips


def plan_garage(state, vehicles, templates, matches, overwrite=False):
    plans = []
    for key in sorted(state.get('vehicles', {}), key=int):
        info = vehicles.get(int(key))
        if info is None:
            plans.append((key, None, [], [('-', 'unknown vehicle type')]))
            continue
        if not matches(info):
            continue
        changes, skips = plan_vehicle(
            info, state['vehicles'][key], templates, overwrite)
        plans.append((key, info, changes, skips))
    return plans


# ---- writing --------------------------------------------------------------

def client_running(processes=CLIENT_PROCESSES):
    try:
        output = subprocess.check_output(
            ['tasklist', '/FO', 'CSV', '/NH'], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    running = []
    for line in output.decode('mbcs', 'replace').splitlines():
        for name in processes:
            if line.lower().startswith('"%s"' % name.lower()):
                running.append(name)
    return running


def _replace_file(temporary_path, path):
    move = None
    try:
        move = ctypes.windll.kernel32.MoveFileExW
    except (AttributeError, OSError):
        move = None
    if move is not None:
        if move(str(temporary_path), str(path), 0x1):
            return
    backup_path = path + '.bak'
    if os.path.exists(backup_path):
        os.unlink(backup_path)
    os.rename(path, backup_path)
    try:
        os.rename(temporary_path, path)
    except OSError:
        os.rename(backup_path, path)
        raise
    os.unlink(backup_path)


def write_garage(path, state):
    timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = '%s.trainer-backup-%s' % (path, timestamp)
    shutil.copyfile(path, backup)
    temporary = path + '.tmp'
    with open(temporary, 'wb') as stream:
        payload = json.dumps(state, indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    _replace_file(temporary, path)
    return backup


# ---- reporting / CLI ------------------------------------------------------

def _describe_vehicle(info):
    tier = '%d级' % info.tier if info.tier else '?'
    clazz = CLASS_NAMES_CN.get(info.clazz, info.clazz)
    nation = NATION_NAMES_CN.get(info.nation, info.nation)
    return '%s %s %s %s [%s]' % (nation, tier, clazz, info.name, info.key)


def report(plans):
    applied = skipped_members = skipped_vehicles = 0
    lines = []
    for key, info, changes, skips in plans:
        if not changes:
            skipped_vehicles += 1
            continue
        applied += 1
        header = '%s (typeCD %s)' % (
            _describe_vehicle(info) if info else 'unknown', key)
        lines.append(header)
        for slot, combo, trained, training, _ in changes:
            lines.append('  槽位%s %s:' % (slot, describe_role(combo)))
            for skill in trained:
                lines.append('    + %s' % describe_skill(skill))
            if training:
                lines.append('    ~ %s (在练)' % describe_skill(training))
        for slot, reason in skips:
            skipped_members += 1
            lines.append('  槽位%s 跳过: %s' % (slot, reason))
    lines.append('')
    lines.append(
        '将修改 %d 辆车的乘员; %d 个乘员槽位被跳过; %d 辆车无改动' % (
            applied, skipped_members, skipped_vehicles))
    return '\n'.join(lines)


def _ensure_vehicle_db(args):
    if os.path.isfile(args.vehicle_db) and not args.rebuild_db:
        return load_vehicle_db(args.vehicle_db)
    scripts_pkg = os.path.join(
        args.client_dir, 'res', 'packages', 'scripts.pkg')
    text_dir = os.path.join(args.client_dir, 'res', 'text')
    vehicles = build_vehicle_db(scripts_pkg, text_dir)
    save_vehicle_db(vehicles, args.vehicle_db)
    return vehicles


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='apply_crew_templates',
        description='按角色组合模板设置离线车库乘员技能 (默认 dry-run)')
    parser.add_argument('--garage', default=DEFAULT_GARAGE)
    parser.add_argument('--templates', default=DEFAULT_TEMPLATES)
    parser.add_argument('--vehicle-db', default=DEFAULT_VEHICLE_DB)
    parser.add_argument('--client-dir', default=DEFAULT_CLIENT_DIR)
    parser.add_argument('--rebuild-db', action='store_true')
    parser.add_argument('--nation', action='append',
                        help='系别, 如 ussr,china 或 苏联,中国')
    parser.add_argument('--tier', action='append',
                        help='等级, 如 8 或 8-10')
    parser.add_argument('--class', dest='class_', action='append',
                        help='车型, 如 mediumTank 或 中型坦克')
    parser.add_argument('--vehicles', action='append',
                        help='车辆名/关键字列表, 逗号分隔')
    parser.add_argument('--apply', action='store_true',
                        help='实际写入存档 (默认只打印 dry-run 报告)')
    parser.add_argument('--overwrite', action='store_true',
                        help='覆盖已学技能的乘员 (默认跳过他们)')
    parser.add_argument('--list-skills', action='store_true',
                        help='打印全部技能内部名/中文名对照后退出')
    parser.add_argument('--force', action='store_true',
                        help='跳过客户端进程检测 (不推荐)')
    args = parser.parse_args(argv)

    if args.list_skills:
        for role in ('commander', 'radioman', 'driver', 'gunner', 'loader'):
            print('%s:' % describe_role(role))
            for skill in sorted(
                    SKILLS_BY_ROLES[role],
                    key=lambda s: (s in ('repair', 'fireFighting',
                                         'camouflage', 'brotherhood'), s)):
                print('  %s' % describe_skill(skill))
        return 0

    if not os.path.isfile(args.garage):
        parser.error('garage state not found: %s' % args.garage)
    templates = load_templates(args.templates)
    vehicles = _ensure_vehicle_db(args)
    matches = build_filters(args)
    with open(args.garage, 'rb') as stream:
        state = json.load(stream)

    plans = plan_garage(state, vehicles, templates, matches,
                        overwrite=args.overwrite)
    text = report(plans)
    print(text)

    changed = any(changes for _, _, changes, _ in plans)
    if not changed:
        print('没有可应用的改动。')
        return 0
    if not args.apply:
        print('\n(dry-run; 加 --apply 实际写入)')
        return 0

    if not args.force:
        running = client_running()
        if running:
            print('错误: 客户端正在运行 (%s); 请先关闭游戏再写入。' %
                  ', '.join(running), file=sys.stderr)
            return 2

    for key, info, changes, _ in plans:
        record = state['vehicles'].get(key)
        for slot, _combo, _trained, _training, encoded in changes:
            record['crew'][str(slot)] = encoded
    backup = write_garage(args.garage, state)
    print('已写入 %s (备份: %s)' % (args.garage, backup))
    return 0


if __name__ == '__main__':
    sys.exit(main())
