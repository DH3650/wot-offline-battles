"""Tests for trainer.vehicle_db against the real client packages."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.vehicle_db import (  # noqa: E402
    NATIONS, build_vehicle_db, decode_compressed_string,
    parse_type_compact_descr, type_compact_descr, vehicle_db_from_json,
    vehicle_db_to_json)

CLIENT_DIR = os.environ.get('WOT_CLIENT_DIR', r'C:\games\wot_0.9.22_cn')
SCRIPTS_PKG = os.path.join(CLIENT_DIR, 'res', 'packages', 'scripts.pkg')
TEXT_DIR = os.path.join(CLIENT_DIR, 'res', 'text')


class CompactDescrTest(unittest.TestCase):

    def test_known_pairs(self):
        # Anchors from the live save: (key, nation, vehicleTypeID from blob).
        self.assertEqual(1, type_compact_descr(0, 0))
        self.assertEqual(1041, type_compact_descr(1, 4))
        self.assertEqual(10497, type_compact_descr(0, 41))

    def test_round_trip(self):
        for nation_id, vehicle_type_id in ((0, 0), (1, 4), (3, 81), (9, 255)):
            self.assertEqual(
                (nation_id, vehicle_type_id),
                parse_type_compact_descr(
                    type_compact_descr(nation_id, vehicle_type_id)))


class JsonRoundTripTest(unittest.TestCase):

    def test_round_trip(self):
        info_payload = {
            '1041': {
                'type_cd': 1041, 'nation': 'germany', 'nation_id': 1,
                'vehicle_type_id': 4, 'key': 'G01_Test',
                'user_string': '#germany_vehicles:G01_Test',
                'name': 'Test', 'tier': 5, 'clazz': 'mediumTank',
                'crew': [['commander', 'radioman'], ['driver']],
            },
        }
        restored = vehicle_db_from_json(vehicle_db_from_json_roundtrip(info_payload))
        info = restored[1041]
        self.assertEqual('germany', info.nation)
        self.assertEqual(('commander', 'radioman'), info.crew[0])
        self.assertEqual('commander+radioman', info.crew_combo(0))


def vehicle_db_from_json_roundtrip(payload):
    return vehicle_db_to_json(vehicle_db_from_json(payload))


class CompressedStringTest(unittest.TestCase):

    def test_decodes_radioman(self):
        # /crew/loader of Ch19_121 in the pinned client.
        self.assertEqual(
            'radioman',
            decode_compressed_string(b'\xad\xa7\x62\xa2\x66\xa7'))

    def test_decodes_mixed_case(self):
        # effects/mud of Chassis_Ch19_121: verifies the uppercase segment.
        self.assertEqual(
            'largeTankMud',
            decode_compressed_string(
                bytes.fromhex('95aae07936a790cb9d')))


@unittest.skipUnless(os.path.isfile(SCRIPTS_PKG), 'no client package')
class LiveBuildTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.vehicles = build_vehicle_db(SCRIPTS_PKG, TEXT_DIR)

    def test_covers_every_garage_vehicle(self):
        self.assertGreater(len(self.vehicles), 500)

    def test_t34(self):
        info = self.vehicles[1]
        self.assertEqual('ussr', info.nation)
        self.assertEqual('R04_T-34', info.key)
        self.assertEqual(5, info.tier)
        self.assertEqual('mediumTank', info.clazz)
        self.assertEqual(
            [('commander', 'gunner'), ('radioman',), ('driver',),
             ('loader',)],
            [tuple(slot) for slot in info.crew])

    def test_renault_ft_commander_doubles_everything(self):
        for info in self.vehicles.values():
            if info.key == 'F01_RenaultFT':
                break
        else:
            self.fail('F01_RenaultFT not found')
        self.assertEqual(2, len(info.crew))
        self.assertEqual('commander', info.crew[0][0])
        self.assertIn('radioman', info.crew[0])
        self.assertIn('gunner', info.crew[0])
        self.assertIn('loader', info.crew[0])
        self.assertEqual(('driver',), tuple(info.crew[1]))

    def test_121_loader_doubles_as_radioman(self):
        # The doubling is stored as a BigWorld 6-bit compressed string;
        # a reader that ignores TYPE_COMPRESSED_STRING misses it.
        info = self.vehicles[4145]
        self.assertEqual('Ch19_121', info.key)
        self.assertEqual(('loader', 'radioman'), tuple(info.crew[3]))

    def test_every_garage_key_resolves(self):
        import json
        garage = os.path.join(
            os.environ.get('APPDATA', ''), 'Wargaming.net', 'WorldOfTanks',
            'offline_lan_0922', 'garage_state.json')
        if not os.path.isfile(garage):
            self.skipTest('no live garage state')
        with open(garage, 'rb') as stream:
            state = json.load(stream)
        missing = [
            key for key in state['vehicles'] if int(key) not in self.vehicles]
        self.assertEqual([], missing)

    def test_nation_index_bounds(self):
        for info in self.vehicles.values():
            self.assertIn(info.nation, NATIONS)
            self.assertEqual(
                (info.nation_id, info.vehicle_type_id),
                parse_type_compact_descr(info.type_cd))


if __name__ == '__main__':
    unittest.main()
