"""Static vehicle database extracted from the pinned client's scripts.pkg.

The garage save keys vehicles on ``vehicleTypeCompactDescr`` which the #1513
client computes as ``1 + (nationID << 4) + (vehicleTypeID << 8)``
(``items/vehicles.py: getVehicleTypeCompactDescr``, with
``ITEM_TYPES.vehicle == 1``).  ``vehicleTypeID`` is the ``id`` attribute in
that nation's ``item_defs/vehicles/<nation>/list.xml``.

Per-slot crew roles come from each vehicle XML's ``<crew>`` section: the
element name is the primary role and its text value lists the secondary
roles (e.g. the Renault FT commander reads ``gunner radioman loader``).

Display names are resolved from the CN client's gettext catalogs
(``res/text/LC_MESSAGES/<nation>_vehicles.mo``).
"""

from __future__ import annotations

import gettext
import io
import json
import os
import zipfile
from dataclasses import dataclass, field

try:
    from tools.packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING
except ImportError:  # trainer run as a loose directory, not a repo package
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
    from packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING

# BigWorld packed XML stores identifier-like strings as 6-bit-per-char
# big-endian values over this alphabet (reverse-engineered from the pinned
# client: 'radioman' <-> ad a7 62 a2 66 a7, 'largeTankMud' verified too).
_COMPRESSED_ALPHABET = (
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_/')


def decode_compressed_string(raw):
    """Decode one BigWorld 6-bit packed string."""
    bits = ''.join(format(byte, '08b') for byte in raw)
    chars = []
    for index in range(0, len(bits) - 5, 6):
        chars.append(_COMPRESSED_ALPHABET[int(bits[index:index + 6], 2)])
    return ''.join(chars)

# common/nations.py NAMES in the pinned client.
NATIONS = (
    'ussr', 'germany', 'usa', 'china', 'france',
    'uk', 'japan', 'czech', 'sweden', 'poland')

NATION_NAMES_CN = {
    'ussr': '苏联', 'germany': '德国', 'usa': '美国', 'china': '中国',
    'france': '法国', 'uk': '英国', 'japan': '日本', 'czech': '捷克',
    'sweden': '瑞典', 'poland': '波兰',
}

VEHICLE_CLASS_TAGS = ('lightTank', 'mediumTank', 'heavyTank', 'AT-SPG', 'SPG')

CLASS_NAMES_CN = {
    'lightTank': '轻型坦克', 'mediumTank': '中型坦克',
    'heavyTank': '重型坦克', 'AT-SPG': '自行反坦克炮', 'SPG': '自行火炮',
}

_VALID_ROLE_TOKENS = frozenset(
    ('commander', 'radioman', 'driver', 'gunner', 'loader'))


def type_compact_descr(nation_id, vehicle_type_id):
    """``vehicleTypeCompactDescr`` for a (nation, id) pair."""
    return 1 + (int(nation_id) << 4) + (int(vehicle_type_id) << 8)


def parse_type_compact_descr(type_cd):
    """Inverse of :func:`type_compact_descr`; returns (nation_id, veh_id)."""
    type_cd = int(type_cd) - 1
    return (type_cd >> 4) & 0xF, type_cd >> 8


@dataclass
class VehicleInfo:
    type_cd: int
    nation: str
    nation_id: int
    vehicle_type_id: int
    key: str
    user_string: str
    name: str
    tier: int
    clazz: str
    crew: list = field(default_factory=list)

    def crew_combo(self, slot):
        """``'commander+radioman'`` style template key for one slot."""
        return '+'.join(self.crew[slot])


def _text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return value


def _children(element):
    """Yield ``(name, PackedValue)`` pairs of one packed element."""
    return getattr(element, 'children', ()) or ()


def _element_fields(packed_value):
    """Map child name → scalar value for one XML element."""
    fields = {}
    for name, value in _children(packed_value.value):
        if hasattr(value.value, 'children'):
            continue
        fields[_text(name)] = value.value
    return fields


def _parse_crew(vehicle_root):
    """One ``(primary, *secondary)`` tuple per crew slot, in slot order."""
    for name, value in _children(vehicle_root):
        if _text(name) != 'crew':
            continue
        slots = []
        for role_name, role_value in _children(value.value):
            primary = _text(role_name)
            roles = [primary]
            if role_value.value_type == TYPE_COMPRESSED_STRING:
                extra = decode_compressed_string(role_value.value or b'')
            else:
                extra = _text(role_value.value or b'')
            for token in extra.split():
                if token in _VALID_ROLE_TOKENS and token not in roles:
                    roles.append(token)
            slots.append(tuple(roles))
        return slots
    return []


def _load_mo_catalog(text_dir, domain):
    path = os.path.join(text_dir, 'LC_MESSAGES', domain + '.mo')
    if not os.path.isfile(path):
        return {}
    with open(path, 'rb') as stream:
        catalog = gettext.GNUTranslations(io.BytesIO(stream.read()))
    return catalog


def _resolve_name(catalog, user_string, fallback):
    if not user_string:
        return fallback
    key = user_string
    if key.startswith('#'):
        key = key[1:]
    msgid = key.split(':', 1)[-1]
    if catalog:
        translated = catalog.gettext(msgid)
        if translated and translated != msgid:
            return translated
    return msgid


def build_vehicle_db(scripts_pkg_path, text_dir):
    """Extract every vehicle definition into ``{type_cd: VehicleInfo}``."""
    vehicles = {}
    with zipfile.ZipFile(scripts_pkg_path) as pkg:
        names = set(pkg.namelist())
        for nation_id, nation in enumerate(NATIONS):
            list_path = 'scripts/item_defs/vehicles/%s/list.xml' % nation
            if list_path not in names:
                continue
            catalog = _load_mo_catalog(text_dir, '%s_vehicles' % nation)
            root = read_packed_xml(pkg.read(list_path))
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
                xml_path = 'scripts/item_defs/vehicles/%s/%s.xml' % (
                    nation, key)
                crew = []
                if xml_path in names:
                    vehicle_root = read_packed_xml(pkg.read(xml_path))
                    crew = _parse_crew(vehicle_root)
                user_string = _text(fields.get('userString') or b'')
                tags = _text(fields.get('tags') or b'').split()
                clazz = next(
                    (tag for tag in tags if tag in VEHICLE_CLASS_TAGS), '')
                try:
                    tier = int(fields.get('level', 0))
                except (TypeError, ValueError):
                    tier = 0
                type_cd = type_compact_descr(nation_id, vehicle_type_id)
                vehicles[type_cd] = VehicleInfo(
                    type_cd=type_cd,
                    nation=nation,
                    nation_id=nation_id,
                    vehicle_type_id=vehicle_type_id,
                    key=key,
                    user_string=user_string,
                    name=_resolve_name(catalog, user_string, key),
                    tier=tier,
                    clazz=clazz,
                    crew=crew,
                )
    return vehicles


def vehicle_db_to_json(vehicles):
    return {
        str(type_cd): {
            'type_cd': info.type_cd,
            'nation': info.nation,
            'nation_id': info.nation_id,
            'vehicle_type_id': info.vehicle_type_id,
            'key': info.key,
            'user_string': info.user_string,
            'name': info.name,
            'tier': info.tier,
            'clazz': info.clazz,
            'crew': [list(slot) for slot in info.crew],
        }
        for type_cd, info in vehicles.items()
    }


def vehicle_db_from_json(payload):
    vehicles = {}
    for type_cd, row in payload.items():
        vehicles[int(type_cd)] = VehicleInfo(
            type_cd=int(row['type_cd']),
            nation=row['nation'],
            nation_id=int(row['nation_id']),
            vehicle_type_id=int(row['vehicle_type_id']),
            key=row['key'],
            user_string=row.get('user_string', ''),
            name=row.get('name', row['key']),
            tier=int(row.get('tier', 0)),
            clazz=row.get('clazz', ''),
            crew=[tuple(slot) for slot in row.get('crew', ())],
        )
    return vehicles


def save_vehicle_db(vehicles, path):
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(vehicle_db_to_json(vehicles), stream,
                  ensure_ascii=False, indent=1, sort_keys=True)
        stream.write('\n')


def load_vehicle_db(path):
    with open(path, 'r', encoding='utf-8') as stream:
        return vehicle_db_from_json(json.load(stream))
