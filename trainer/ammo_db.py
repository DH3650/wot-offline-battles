"""Stock ammunition capacity per mounted (turret, gun), from scripts.pkg.

A garage save stores each vehicle's loaded shells plus the mounted
``(turretCompactDescr, gunCompactDescr)`` pair in ``shellsLayoutIdx``.  The
client refuses a save whose loaded rounds exceed the mounted gun's
``maxAmmo`` (``bootstrap.py _validate_restored_vehicle``), which is exactly
what a vehicle profile raising ``maxAmmo`` produces once the game runs
without that profile.

Compact descriptors follow the pinned client's packing: the low 4 bits are
the item kind (``vehicleTurret == 3``, ``vehicleGun == 4``), then nation and
the per-nation id from ``components/turrets.xml`` / ``components/guns.xml``.

The effective stock capacity of one mounted gun is the vehicle-local
``turretsN/<turret>/guns/<gun>/maxAmmo`` override when present, else the
gun's shared ``components/guns.xml`` value.
"""

from __future__ import annotations

import json
import os
import re
import zipfile

try:
    from tools.packed_xml import read_packed_xml
except ImportError:  # trainer run as a loose directory, not a repo package
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
    from packed_xml import read_packed_xml

from trainer.vehicle_db import (
    NATIONS, _children, _element_fields, _text, type_compact_descr)

TURRET_KIND = 3
GUN_KIND = 4
AMMO_DB_SCHEMA = 1
DEFAULT_AMMO_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'ammo_db.json')

_TURRETS_SECTION = re.compile(r'^turrets\d+$')
_CLIENT_REVISION = re.compile(r'<client>\s*(\d+)\s*</client>')
_VERSION_TEXT = re.compile(r'<version>\s*([^<]+?)\s*</version>')


def package_version(client_dir):
    """The client data revision (``version.xml`` ``<meta><client>``).

    This tracks updates to the packaged game data (``scripts.pkg``), so the
    extracted ammunition database can be invalidated without re-reading the
    archive on every launch.  Falls back to the human version text, and to
    ``None`` when the file is missing or unreadable.
    """
    path = os.path.join(client_dir, 'version.xml')
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as stream:
            text = stream.read()
    except (IOError, OSError):
        return None
    match = _CLIENT_REVISION.search(text)
    if match:
        return match.group(1)
    match = _VERSION_TEXT.search(text)
    return match.group(1).strip() if match else None


def component_compact_descr(kind, nation_id, item_id):
    """Compact descriptor for one component (turret/gun) id."""
    return int(kind) + (int(nation_id) << 4) + (int(item_id) << 8)


def _ids_section(root):
    """``name -> id`` map from one component file's ``ids`` element."""
    for name, value in _children(root):
        if _text(name) != 'ids':
            continue
        ids = {}
        for entry_name, entry in _children(value.value):
            try:
                ids[_text(entry_name)] = int(entry.value)
            except (TypeError, ValueError):
                continue
        return ids
    return {}


def _shared_gun_ammo(root):
    """``gun name -> shared maxAmmo`` from a ``components/guns.xml`` root."""
    for name, value in _children(root):
        if _text(name) != 'shared':
            continue
        table = {}
        for gun_name, gun in _children(value.value):
            fields = _element_fields(gun)
            try:
                table[_text(gun_name)] = int(fields.get('maxAmmo'))
            except (TypeError, ValueError):
                continue
        return table
    return {}


def _vehicle_guns(vehicle_root, gun_ids, shared_ammo):
    """``turret name -> {gun name: effective stock maxAmmo}``."""
    per_turret = {}
    for section_name, section in _children(vehicle_root):
        if _TURRETS_SECTION.match(_text(section_name)) is None:
            continue
        if not hasattr(section.value, 'children'):
            continue
        for turret_name, turret in _children(section.value):
            if not hasattr(turret.value, 'children'):
                continue
            for group_name, group in _children(turret.value):
                if _text(group_name) != 'guns':
                    continue
                if not hasattr(group.value, 'children'):
                    continue
                guns = {}
                for gun_name, gun in _children(group.value):
                    gun_name = _text(gun_name)
                    if gun_name not in gun_ids:
                        continue
                    ammo = None
                    if hasattr(gun.value, 'children'):
                        fields = _element_fields(gun)
                        try:
                            ammo = int(fields.get('maxAmmo'))
                        except (TypeError, ValueError):
                            ammo = None
                    if ammo is None:
                        ammo = shared_ammo.get(gun_name)
                    if ammo:
                        guns[gun_name] = int(ammo)
                if guns:
                    per_turret[_text(turret_name)] = guns
    return per_turret


def build_ammo_db(scripts_pkg_path):
    """Extract stock capacities and the name->id tables from scripts.pkg."""
    capacities = {}
    gun_ids_by_nation = {}
    turret_ids_by_nation = {}
    with zipfile.ZipFile(scripts_pkg_path) as pkg:
        names = set(pkg.namelist())
        for nation_id, nation in enumerate(NATIONS):
            guns_member = (
                'scripts/item_defs/vehicles/%s/components/guns.xml' % nation)
            if guns_member not in names:
                continue
            guns_root = read_packed_xml(pkg.read(guns_member))
            gun_ids = _ids_section(guns_root)
            shared_ammo = _shared_gun_ammo(guns_root)
            turrets_member = (
                'scripts/item_defs/vehicles/%s/components/turrets.xml'
                % nation)
            turret_ids = {}
            if turrets_member in names:
                turret_ids = _ids_section(
                    read_packed_xml(pkg.read(turrets_member)))
            gun_ids_by_nation[nation] = gun_ids
            turret_ids_by_nation[nation] = turret_ids
            list_member = 'scripts/item_defs/vehicles/%s/list.xml' % nation
            if list_member not in names:
                continue
            root = read_packed_xml(pkg.read(list_member))
            for key, entry in _children(root):
                if not hasattr(entry.value, 'children'):
                    continue
                fields = _element_fields(entry)
                try:
                    vehicle_type_id = int(fields.get('id', -1))
                except (TypeError, ValueError):
                    continue
                if vehicle_type_id < 0:
                    continue
                key = _text(key)
                member = 'scripts/item_defs/vehicles/%s/%s.xml' % (
                    nation, key)
                if member not in names:
                    continue
                vehicle_root = read_packed_xml(pkg.read(member))
                per_vehicle = {}
                for turret_name, guns in _vehicle_guns(
                        vehicle_root, gun_ids, shared_ammo).items():
                    turret_id = turret_ids.get(turret_name)
                    if turret_id is None:
                        continue
                    turret_cd = component_compact_descr(
                        TURRET_KIND, nation_id, turret_id)
                    row = {}
                    for gun_name, ammo in guns.items():
                        gun_cd = component_compact_descr(
                            GUN_KIND, nation_id, gun_ids[gun_name])
                        row[str(gun_cd)] = ammo
                    if row:
                        per_vehicle[str(turret_cd)] = row
                if per_vehicle:
                    type_cd = type_compact_descr(nation_id, vehicle_type_id)
                    capacities[str(type_cd)] = per_vehicle
    return {
        'schema': AMMO_DB_SCHEMA,
        'capacities': capacities,
        'gunIds': gun_ids_by_nation,
        'turretIds': turret_ids_by_nation,
    }


def save_ammo_db(db, path):
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(db, stream, ensure_ascii=False, indent=1, sort_keys=True)
        stream.write('\n')


def load_ammo_db(path):
    with open(path, 'r', encoding='utf-8') as stream:
        db = json.load(stream)
    if not isinstance(db, dict) or db.get('schema') != AMMO_DB_SCHEMA \
            or not isinstance(db.get('capacities'), dict):
        raise ValueError('%s 不是有效的载弹数据库' % path)
    return db


def ensure_ammo_db(path, client_dir):
    """Load the cached database, extracting from scripts.pkg when missing.

    The cache records the client data revision (``sourceVersion``); a matching
    revision is returned without touching scripts.pkg, while a changed one
    triggers a fresh extraction.  When the revision cannot be read the
    existing cache is trusted as-is.
    """
    version = package_version(client_dir)
    if os.path.isfile(path):
        db = load_ammo_db(path)
        if version is None or db.get('sourceVersion') == version:
            return db
    scripts_pkg = os.path.join(client_dir, 'res', 'packages', 'scripts.pkg')
    db = build_ammo_db(scripts_pkg)
    if version is not None:
        db['sourceVersion'] = version
    save_ammo_db(db, path)
    return db


def capacity_for(capacities, type_cd, turret_cd, gun_cd):
    """Stock maxAmmo for one mounted (turret, gun), or None when unknown."""
    per_vehicle = (capacities or {}).get(str(int(type_cd)))
    if not isinstance(per_vehicle, dict):
        return None
    row = per_vehicle.get(str(int(turret_cd)))
    if not isinstance(row, dict):
        return None
    value = row.get(str(int(gun_cd)))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    'AMMO_DB_SCHEMA', 'DEFAULT_AMMO_DB', 'GUN_KIND', 'TURRET_KIND',
    'build_ammo_db', 'capacity_for', 'component_compact_descr',
    'ensure_ammo_db', 'load_ammo_db', 'package_version', 'save_ammo_db',
]
