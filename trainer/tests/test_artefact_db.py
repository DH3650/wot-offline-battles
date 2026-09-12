"""Tests for trainer.artefact_db against the real client packages."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.artefact_db import (  # noqa: E402
    artefact_compact_descr, artefact_db_from_json, artefact_db_to_json,
    build_artefact_db)

CLIENT_DIR = os.environ.get('WOT_CLIENT_DIR', r'C:\games\wot_0.9.22_cn')
SCRIPTS_PKG = os.path.join(CLIENT_DIR, 'res', 'packages', 'scripts.pkg')
TEXT_DIR = os.path.join(CLIENT_DIR, 'res', 'text')


class CompactDescrTest(unittest.TestCase):

    def test_formula_anchors_from_live_save(self):
        # 真实存档 owned['9'] 的库存键: (id << 8) | 0xF9
        self.assertEqual(5625, artefact_compact_descr(21))
        self.assertEqual(11769, artefact_compact_descr(45))
        # 给养(item type 11)同一公式: largeRepairkit id=5 -> 1531
        self.assertEqual(1531, artefact_compact_descr(5, 11))


class JsonRoundTripTest(unittest.TestCase):

    def test_round_trip(self):
        payload = {'5625': {
            'compact_descr': 5625, 'item_type': 9, 'artefact_id': 21,
            'key': 'mediumCaliberTankRammer',
            'user_string': '#artefacts:mediumCaliberTankRammer/name',
            'name': '中型坦克炮输弹机', 'price': 200000,
            'tags': ['rammer']}}
        restored = artefact_db_from_json(payload)
        info = restored[5625]
        self.assertEqual('rammer', info.tags[0])
        self.assertEqual(200000, info.price)
        self.assertEqual(payload, artefact_db_to_json(restored))


@unittest.skipUnless(os.path.isfile(SCRIPTS_PKG), 'no client package')
class LiveBuildTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.artefacts = build_artefact_db(SCRIPTS_PKG, TEXT_DIR)

    def test_covers_every_optional_device(self):
        self.assertEqual(49, len(self.artefacts))

    def test_names_resolve_to_chinese(self):
        self.assertEqual('中型坦克炮输弹机', self.artefacts[5625].name)
        self.assertEqual('改进型装填系统', self.artefacts[11769].name)

    def test_delux_devices_have_no_xml_price(self):
        self.assertIsNone(self.artefacts[11769].price)

    def test_live_save_owned_keys_are_known(self):
        garage = os.path.join(
            os.environ.get('APPDATA', ''), 'Wargaming.net', 'WorldOfTanks',
            'offline_lan_0922', 'saves', 'default', 'garage_state.json')
        if not os.path.isfile(garage):
            self.skipTest('no live garage state')
        with open(garage, 'rb') as stream:
            owned = json.load(stream).get('owned', {}).get('9', {})
        # 目录必须能解释存档里出现的每个配件键 (数量可以不同)
        self.assertTrue(set(int(k) for k in owned) <= set(self.artefacts))


if __name__ == '__main__':
    unittest.main()
