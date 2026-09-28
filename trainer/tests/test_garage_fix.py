"""Tests for trainer.garage_fix: ammunition clamping for profile switches."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer import garage_fix  # noqa: E402
from trainer.ammo_db import (  # noqa: E402
    GUN_KIND, TURRET_KIND, capacity_for, component_compact_descr,
    load_ammo_db, package_version, save_ammo_db)
from trainer.vehicle_db import VehicleInfo  # noqa: E402

# germany G_Panther: turret 66 (Turret_1_G_Panther), gun 50 (_150mm_sFH36_L43)
TYPE_CD = 8977
TURRET_CD = component_compact_descr(TURRET_KIND, 1, 66)
GUN_CD = component_compact_descr(GUN_KIND, 1, 50)

CAPACITIES = {str(TYPE_CD): {str(TURRET_CD): {str(GUN_CD): 30}}}


def make_info():
    return VehicleInfo(
        type_cd=TYPE_CD, nation='germany', nation_id=1, vehicle_type_id=35,
        key='G49_G_Panther', user_string='', name='G_Panther', tier=7,
        clazz='SPG', crew=[('commander',)])


def make_state(shells, layout_idx=None):
    return {'vehicles': {str(TYPE_CD): {
        'shells': list(shells),
        'shellsLayoutIdx': list(
            layout_idx if layout_idx is not None else (TURRET_CD, GUN_CD)),
    }}}


class CompactDescrTest(unittest.TestCase):

    def test_packing_matches_saved_descriptors(self):
        # Values observed in a real save: germany turret id 66 -> 16915,
        # gun id 50 -> 12820.
        self.assertEqual(16915, component_compact_descr(TURRET_KIND, 1, 66))
        self.assertEqual(12820, component_compact_descr(GUN_KIND, 1, 50))


class PackageVersionTest(unittest.TestCase):

    def test_reads_client_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'version.xml')
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(
                    '<version.xml><version>v.0.9.22.0.1 #1513</version>'
                    '<meta><client>1265476</client></meta></version.xml>')
            self.assertEqual('1265476', package_version(tmp))

    def test_falls_back_to_version_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'version.xml')
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write(
                    '<version.xml><version> v.0.9.22.0.1 #1513</version>'
                    '</version.xml>')
            self.assertEqual('v.0.9.22.0.1 #1513', package_version(tmp))

    def test_missing_version_xml_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(package_version(tmp))


class CapacityTest(unittest.TestCase):

    def test_lookup(self):
        self.assertEqual(
            30, capacity_for(CAPACITIES, TYPE_CD, TURRET_CD, GUN_CD))
        self.assertIsNone(capacity_for(CAPACITIES, TYPE_CD, TURRET_CD, 1))
        self.assertIsNone(capacity_for(CAPACITIES, 1, TURRET_CD, GUN_CD))
        self.assertIsNone(capacity_for(None, TYPE_CD, TURRET_CD, GUN_CD))

    def test_save_load_roundtrip(self):
        db = {'schema': 1, 'capacities': CAPACITIES,
              'gunIds': {'germany': {'_150mm_sFH36_L43': 50}},
              'turretIds': {'germany': {'Turret_1_G_Panther': 66}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'ammo_db.json')
            save_ammo_db(db, path)
            loaded = load_ammo_db(path)
        self.assertEqual(30, capacity_for(
            loaded['capacities'], TYPE_CD, TURRET_CD, GUN_CD))


class PlanTest(unittest.TestCase):

    def test_over_capacity_is_clamped_from_the_tail(self):
        state = make_state([100, 68, 200, 2])  # total 70, capacity 30
        changes, unknown = garage_fix.plan_ammo_fix(state, CAPACITIES)
        self.assertEqual(0, unknown)
        self.assertEqual(1, len(changes))
        self.assertEqual([100, 30, 200, 0], changes[0]['shells'])
        self.assertEqual(70, changes[0]['before'])
        self.assertEqual(30, changes[0]['capacity'])

    def test_partial_last_pair_keeps_remainder(self):
        state = make_state([100, 25, 200, 20])  # total 45, capacity 30
        changes, unused = garage_fix.plan_ammo_fix(state, CAPACITIES)
        self.assertEqual([100, 25, 200, 5], changes[0]['shells'])

    def test_within_capacity_is_untouched(self):
        state = make_state([100, 30])
        changes, unused = garage_fix.plan_ammo_fix(state, CAPACITIES)
        self.assertEqual([], changes)

    def test_missing_or_malformed_rows_are_skipped(self):
        state = {'vehicles': {
            str(TYPE_CD): {'shells': [100, 999]},
            '2': {'shellsLayoutIdx': [TURRET_CD, GUN_CD]},
            '3': {'shells': [100], 'shellsLayoutIdx': [TURRET_CD, GUN_CD]},
        }}
        changes, unknown = garage_fix.plan_ammo_fix(state, CAPACITIES)
        self.assertEqual([], changes)
        self.assertEqual(0, unknown)

    def test_unknown_capacity_is_not_guessed(self):
        state = make_state([100, 700], layout_idx=(1, 2))
        changes, unknown = garage_fix.plan_ammo_fix(state, CAPACITIES)
        self.assertEqual([], changes)
        self.assertEqual(1, unknown)

    def test_profile_cap_lowers_capacity(self):
        state = make_state([100, 40])  # total 40
        caps = {(TYPE_CD, TURRET_CD, GUN_CD): 35}
        changes, unused = garage_fix.plan_ammo_fix(state, CAPACITIES, caps)
        self.assertEqual(35, changes[0]['capacity'])
        self.assertEqual([100, 35], changes[0]['shells'])

    def test_profile_boost_keeps_saved_ammo(self):
        state = make_state([100, 700])  # exceeds stock 30, fits profile 700
        caps = {(TYPE_CD, TURRET_CD, GUN_CD): 700}
        changes, unused = garage_fix.plan_ammo_fix(state, CAPACITIES, caps)
        self.assertEqual([], changes)


class ApplyTest(unittest.TestCase):

    def test_apply_writes_clamped_shells(self):
        state = make_state([100, 68, 200, 2])
        changes, unused = garage_fix.plan_ammo_fix(state, CAPACITIES)
        self.assertEqual(1, garage_fix.apply_ammo_fix(state, changes))
        self.assertEqual([100, 30, 200, 0],
                         state['vehicles'][str(TYPE_CD)]['shells'])


class ReportTest(unittest.TestCase):

    def test_report_lists_vehicles_and_summary(self):
        state = make_state([100, 700])
        changes, unused = garage_fix.plan_ammo_fix(state, CAPACITIES)
        text = garage_fix.fix_report(
            changes, {TYPE_CD: make_info()}, skipped_unknown=2)
        self.assertIn('G_Panther', text)
        self.assertIn('700 → 30', text)
        self.assertIn('共 1 辆车', text)
        self.assertIn('2 辆车的载弹上限未知', text)


class ProfileCapsTest(unittest.TestCase):

    def _ammo_db(self):
        return {'schema': 1, 'capacities': CAPACITIES,
                'gunIds': {'germany': {'_150mm_sFH36_L43': 50}},
                'turretIds': {'germany': {'Turret_1_G_Panther': 66}}}

    def _write_profiles(self, profiles):
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump({'profiles': profiles}, stream)
            return stream.name

    def _g_panther_member(self, value):
        return {'name': 'SPG', 'members': [{
            'sourceMember':
                'scripts/item_defs/vehicles/germany/G49_G_Panther.xml',
            'edits': [{
                'fieldPath': 'turrets0/Turret_1_G_Panther/guns/'
                             '_150mm_sFH36_L43/maxAmmo',
                'originalValue': 30,
                'replacementValue': value,
            }],
        }]}

    def test_load_profile_caps_maps_edit_to_compact_descrs(self):
        path = self._write_profiles([self._g_panther_member(20)])
        try:
            caps = garage_fix.load_profile_caps(
                path, {TYPE_CD: make_info()}, self._ammo_db())
        finally:
            os.unlink(path)
        self.assertEqual({(TYPE_CD, TURRET_CD, GUN_CD): 20}, caps)

    def test_missing_profile_store_yields_no_caps(self):
        caps = garage_fix.load_profile_caps(
            os.path.join(tempfile.gettempdir(), 'no-such-file.json'),
            {TYPE_CD: make_info()}, self._ammo_db())
        self.assertEqual({}, caps)

    def test_list_profiles_preserves_order(self):
        path = self._write_profiles([
            {'name': 'SPG', 'members': []},
            {'name': 'France', 'members': []},
            {'name': '', 'members': []},
            'not-a-dict',
        ])
        try:
            self.assertEqual(['SPG', 'France'],
                             garage_fix.list_profiles(path))
        finally:
            os.unlink(path)

    def test_load_profile_caps_filters_by_name(self):
        path = self._write_profiles([
            self._g_panther_member(700),
            {'name': 'France', 'members': []},
        ])
        try:
            # None = safe-for-any: fold the 700 boost down to the 30 stock cap.
            all_caps = garage_fix.load_profile_caps(
                path, {TYPE_CD: make_info()}, self._ammo_db(), None)
            self.assertEqual({(TYPE_CD, TURRET_CD, GUN_CD): 30}, all_caps)
            spg_caps = garage_fix.load_profile_caps(
                path, {TYPE_CD: make_info()}, self._ammo_db(), 'SPG')
            self.assertEqual({(TYPE_CD, TURRET_CD, GUN_CD): 700}, spg_caps)
            france_caps = garage_fix.load_profile_caps(
                path, {TYPE_CD: make_info()}, self._ammo_db(), 'France')
            self.assertEqual({}, france_caps)
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
