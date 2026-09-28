"""Clamp saved ammunition so a garage save survives vehicle profile switches.

A vehicle profile may raise a gun's ``maxAmmo``; battles then load and save
more rounds than the stock gun holds.  When the game next runs without that
profile the client validator refuses every over-capacity vehicle, and once
more than eight vehicles refuse, the whole garage — fittings, crew and
outfits alike — is restored as stock (see account_rpc/garage_store.py
``MAX_CONTAINED_VEHICLES`` and bootstrap.py ``_validate_restored_vehicle``).

Clamping the saved ``shells`` counts to the target profile's effective
capacity keeps the save valid under that profile.  ``shellsLayout`` (the
auto-load buy-back preference) is deliberately left untouched: it is never
validated, and keeping the larger layout lets a boosted profile refill the
rack when it returns.
"""

from __future__ import annotations

import json
import re

from trainer.ammo_db import (
    GUN_KIND, TURRET_KIND, capacity_for, component_compact_descr)
from trainer.vehicle_db import CLASS_NAMES_CN, NATION_NAMES_CN, NATIONS

_MAX_AMMO_EDIT = re.compile(r'^turrets\d+/([^/]+)/guns/([^/]+)/maxAmmo$')
_MEMBER = re.compile(
    r'^scripts/item_defs/vehicles/([a-z][a-z0-9_]*)/([^/]+)\.xml$')


def _clamp_pairs(pairs, capacity):
    """Reduce tail counts until the total fits; primary shells come first."""
    budget = max(0, int(capacity))
    clamped = []
    for compact_descr, count in pairs:
        keep = min(count, budget)
        clamped.append((compact_descr, keep))
        budget -= keep
    return clamped


def plan_ammo_fix(state, capacities, profile_caps=None):
    """(changes, skipped_unknown): per-vehicle clamped ``shells`` lists.

    Only vehicles whose loaded rounds exceed the effective capacity appear
    in ``changes``; vehicles with no known capacity are counted in
    ``skipped_unknown`` and left untouched rather than guessed at.
    """
    changes = []
    skipped_unknown = 0
    vehicles = state.get('vehicles') if isinstance(state, dict) else None
    if not isinstance(vehicles, dict):
        return changes, skipped_unknown
    for key, record in vehicles.items():
        if not isinstance(record, dict):
            continue
        shells = record.get('shells')
        layout_idx = record.get('shellsLayoutIdx')
        if (not isinstance(shells, list) or len(shells) % 2 or
                not isinstance(layout_idx, (list, tuple)) or
                len(layout_idx) != 2):
            continue
        try:
            type_cd = int(key)
            turret_cd = int(layout_idx[0])
            gun_cd = int(layout_idx[1])
            pairs = [(int(shells[index]), int(shells[index + 1]))
                     for index in range(0, len(shells), 2)]
        except (TypeError, ValueError):
            continue
        total = sum(count for unused_cd, count in pairs)
        capacity = capacity_for(capacities, type_cd, turret_cd, gun_cd)
        if capacity is None:
            if total > 0:
                skipped_unknown += 1
            continue
        if profile_caps:
            effective = profile_caps.get((type_cd, turret_cd, gun_cd))
            if effective is not None:
                capacity = effective
        if capacity <= 0 or total <= capacity:
            continue
        clamped = _clamp_pairs(pairs, capacity)
        changes.append({
            'key': str(type_cd),
            'shells': [value for pair in clamped for value in pair],
            'before': total,
            'capacity': capacity,
        })
    return changes, skipped_unknown


def apply_ammo_fix(state, changes):
    """Write the clamped ``shells`` lists into the loaded state."""
    vehicles = state.get('vehicles') if isinstance(state, dict) else None
    if not isinstance(vehicles, dict):
        return 0
    fixed = 0
    for change in changes:
        record = vehicles.get(change['key'])
        if not isinstance(record, dict):
            continue
        record['shells'] = list(change['shells'])
        fixed += 1
    return fixed


def _describe(key, vehicles_db):
    info = vehicles_db.get(int(key)) if vehicles_db else None
    if info is None:
        return 'typeCD %s' % key
    nation = NATION_NAMES_CN.get(info.nation, info.nation)
    clazz = CLASS_NAMES_CN.get(info.clazz, info.clazz)
    tier = '%d级' % info.tier if info.tier else '?'
    return '%s %s %s %s [%s]' % (nation, tier, clazz, info.name, info.key)


def fix_report(changes, vehicles_db, skipped_unknown=0):
    lines = []
    for change in sorted(changes, key=lambda row: int(row['key'])):
        lines.append('%s  载弹 %d → %d (上限)' % (
            _describe(change['key'], vehicles_db),
            change['before'], change['capacity']))
    lines.append('')
    lines.append('共 %d 辆车需要修剪' % len(changes))
    if skipped_unknown:
        lines.append('另有 %d 辆车的载弹上限未知, 已保留不动' % skipped_unknown)
    return '\n'.join(lines)


def list_profiles(profiles_path):
    """Profile names in the launcher's vehicle profile store, in file order."""
    try:
        with open(profiles_path, 'rb') as stream:
            payload = json.load(stream)
    except (IOError, OSError, TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    return [profile['name'] for profile in payload.get('profiles') or ()
            if isinstance(profile, dict) and isinstance(
                profile.get('name'), str) and profile['name']]


def load_profile_caps(profiles_path, vehicles_db, ammo_db, profile_name=None):
    """``(type_cd, turret_cd, gun_cd) -> effective maxAmmo`` for a profile.

    A profile overlay *replaces* the stock value, so the effective capacity
    under one named profile is that profile's ``maxAmmo`` (and the stock
    value where the profile does not touch the gun).  With ``profile_name``
    ``None``, the result is the minimum across every profile — folded with
    the stock capacity — so it is safe for whichever one launches.  Returns
    an empty map when the profile store is unreadable or the name is unknown.
    """
    capacities = (ammo_db or {}).get('capacities') or {}
    collected = {}
    try:
        with open(profiles_path, 'rb') as stream:
            payload = json.load(stream)
    except (IOError, OSError, TypeError, ValueError):
        return collected
    if not isinstance(payload, dict):
        return collected
    key_to_vehicle = {}
    nation_ids = {}
    for type_cd, info in (vehicles_db or {}).items():
        key_to_vehicle[(info.nation, info.key)] = (int(type_cd),
                                                   int(info.nation_id))
    for nation in NATIONS:
        if nation not in nation_ids:
            nation_ids[nation] = NATIONS.index(nation)
    gun_ids = (ammo_db or {}).get('gunIds') or {}
    turret_ids = (ammo_db or {}).get('turretIds') or {}
    for profile in payload.get('profiles') or ():
        if not isinstance(profile, dict):
            continue
        if profile_name is not None and profile.get('name') != profile_name:
            continue
        for member in profile.get('members') or ():
            if not isinstance(member, dict):
                continue
            match = _MEMBER.match(str(member.get('sourceMember') or ''))
            if match is None:
                continue
            nation, key = match.group(1), match.group(2)
            vehicle = key_to_vehicle.get((nation, key))
            nation_id = nation_ids.get(nation)
            if vehicle is None or nation_id is None:
                continue
            type_cd = vehicle[0]
            for edit in member.get('edits') or ():
                if not isinstance(edit, dict):
                    continue
                edit_match = _MAX_AMMO_EDIT.match(
                    str(edit.get('fieldPath') or ''))
                if edit_match is None:
                    continue
                turret_id = (turret_ids.get(nation) or {}).get(
                    edit_match.group(1))
                gun_id = (gun_ids.get(nation) or {}).get(
                    edit_match.group(2))
                if turret_id is None or gun_id is None:
                    continue
                try:
                    value = int(float(edit.get('replacementValue')))
                except (TypeError, ValueError):
                    continue
                if value <= 0:
                    continue
                turret_cd = component_compact_descr(
                    TURRET_KIND, nation_id, turret_id)
                gun_cd = component_compact_descr(
                    GUN_KIND, nation_id, gun_id)
                slot = (type_cd, turret_cd, gun_cd)
                if profile_name is None:
                    if slot not in collected or value < collected[slot]:
                        collected[slot] = value
                else:
                    collected[slot] = value
    if profile_name is None:
        result = {}
        for slot, value in collected.items():
            stock = capacity_for(capacities, slot[0], slot[1], slot[2])
            result[slot] = min(value, stock) if stock is not None else value
        return result
    return collected


__all__ = [
    'apply_ammo_fix', 'fix_report', 'list_profiles', 'load_profile_caps',
    'plan_ammo_fix',
]
