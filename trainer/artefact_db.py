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
