"""Tests for trainer.apply_crew_templates."""

import argparse
import base64
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.apply_crew_templates import (  # noqa: E402
    DEFAULT_TEMPLATES, TemplateError, build_filters, load_templates,
    main, normalize_combo, plan_garage, plan_vehicle)
from trainer.tankman_codec import parse_tankman  # noqa: E402
from trainer.vehicle_db import VehicleInfo  # noqa: E402

TEMPLATES = load_templates(DEFAULT_TEMPLATES)

NO_SKILL_BLOB = base64.b64encode(bytes.fromhex(
    '0800016400000000530048000900410674119701'.replace(' ', ''))).decode()
# commander, nation 0, vehicleTypeID 0, roleLevel 100, no skills.
COMMANDER_BLOB = base64.b64encode(bytes.fromhex(
    '0800016400000000530048000900410674119701')).decode()
# Same tail but role byte = driver (3).
DRIVER_BLOB = base64.b64encode(bytes.fromhex(
    '0800036400000000530048000900410674119701')).decode()
# A commander who already has skills (real shape, 7 trained + 1 at 0%).
SKILLED_BLOB = base64.b64encode(bytes.fromhex(
    '1804016408131210110906080700' '00' 'e0000e0004000a003e120000')).decode()


def make_info(crew):
    return VehicleInfo(
        type_cd=1, nation='ussr', nation_id=0, vehicle_type_id=0,
        key='R04_T-34', user_string='#ussr_vehicles:T-34', name='T-34',
        tier=5, clazz='mediumTank', crew=[tuple(slot) for slot in crew])


class NormalizeComboTest(unittest.TestCase):

    def test_secondary_roles_are_sorted(self):
        self.assertEqual(
            'commander+gunner+radioman',
            normalize_combo(('commander', 'radioman', 'gunner')))
        self.assertEqual('driver', normalize_combo(('driver',)))


class TemplateFileTest(unittest.TestCase):

    def test_shipped_templates_are_valid(self):
        self.assertGreaterEqual(len(TEMPLATES), 16)

    def test_every_garage_combo_has_a_template(self):
        garage = os.path.join(
            os.environ.get('APPDATA', ''), 'Wargaming.net', 'WorldOfTanks',
            'offline_lan_0922', 'garage_state.json')
        vehicle_db = os.path.join(
            os.path.dirname(DEFAULT_TEMPLATES), 'vehicle_db.json')
        if not (os.path.isfile(garage) and os.path.isfile(vehicle_db)):
            self.skipTest('no live garage state or vehicle db')
        from trainer.vehicle_db import load_vehicle_db
        vehicles = load_vehicle_db(vehicle_db)
        with open(garage, 'rb') as stream:
            state = json.load(stream)
        missing = set()
        for key in state['vehicles']:
            info = vehicles.get(int(key))
            if info is None:
                continue
            for slot in info.crew:
                combo = normalize_combo(slot)
                if combo not in TEMPLATES:
                    missing.add(combo)
        self.assertEqual(set(), missing)

    def test_rejects_wrong_trained_count(self):
        payload = {'templates': {'driver': {
            'trained': ['repair'], 'training': 'camouflage'}}}
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(payload, stream)
            path = stream.name
        try:
            with self.assertRaises(TemplateError):
                load_templates(path)
        finally:
            os.unlink(path)


class PlanVehicleTest(unittest.TestCase):

    def test_applies_template_to_skillless_commander(self):
        info = make_info([('commander',), ('driver',)])
        record = {'crew': {'0': COMMANDER_BLOB, '1': DRIVER_BLOB}}
        changes, skips = plan_vehicle(info, record, TEMPLATES)
        self.assertEqual([], skips)
        self.assertEqual(2, len(changes))
        slot, combo, trained, training, encoded = changes[0]
        self.assertEqual(0, slot)
        self.assertEqual('commander', combo)
        self.assertEqual(7, len(trained))
        tankman = parse_tankman(base64.b64decode(encoded))
        self.assertEqual(8, len(tankman.skills))
        self.assertEqual(0, tankman.last_skill_level)
        self.assertEqual(trained, tankman.trained_skills())

    def test_loader_template_has_no_training_skill(self):
        info = make_info([('loader',)])
        record = {'crew': {'0': base64.b64encode(bytes.fromhex(
            '0800056400000000530048000900410674119701')).decode()}}
        changes, skips = plan_vehicle(info, record, TEMPLATES)
        self.assertEqual([], skips)
        _, combo, _, training, encoded = changes[0]
        self.assertIsNone(training)
        tankman = parse_tankman(base64.b64decode(encoded))
        self.assertEqual(7, len(tankman.skills))
        self.assertEqual(100, tankman.last_skill_level)

    def test_skips_crew_with_existing_skills(self):
        info = make_info([('commander',), ('driver',)])
        record = {'crew': {'0': SKILLED_BLOB, '1': DRIVER_BLOB}}
        changes, skips = plan_vehicle(info, record, TEMPLATES)
        self.assertEqual(1, len(changes))
        self.assertEqual(1, len(skips))
        self.assertEqual(0, skips[0][0])
        self.assertIn('already has', skips[0][1])

    def test_overwrite_replaces_existing_skills(self):
        info = make_info([('commander',), ('driver',)])
        record = {'crew': {'0': SKILLED_BLOB, '1': DRIVER_BLOB}}
        changes, skips = plan_vehicle(info, record, TEMPLATES, overwrite=True)
        self.assertEqual([], skips)
        self.assertEqual(2, len(changes))
        slot, combo, trained, training, encoded = changes[0]
        tankman = parse_tankman(base64.b64decode(encoded))
        # 原技能 (commander_expert 等 8 个) 被模板完全替换
        self.assertEqual(trained, tankman.trained_skills())
        self.assertEqual(training, tankman.skills[-1])
        self.assertEqual(0, tankman.last_skill_level)
        self.assertNotIn('commander_expert', tankman.skills[:7] if
                         'commander_expert' not in trained else [])

    def test_skips_on_role_mismatch(self):
        info = make_info([('gunner',)])
        record = {'crew': {'0': COMMANDER_BLOB}}
        changes, skips = plan_vehicle(info, record, TEMPLATES)
        self.assertEqual([], changes)
        self.assertIn('role mismatch', skips[0][1])


class FilterTest(unittest.TestCase):

    def _args(self, **kwargs):
        defaults = dict(nation=None, tier=None, class_=None, vehicles=None)
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_nation_filter_accepts_cn_alias(self):
        matches = build_filters(self._args(nation=['苏联']))
        self.assertTrue(matches(make_info([('commander',)])))
        german = make_info([('commander',)])
        german.nation = 'germany'
        self.assertFalse(matches(german))

    def test_tier_range(self):
        matches = build_filters(self._args(tier=['5-7']))
        self.assertTrue(matches(make_info([('commander',)])))
        top = make_info([('commander',)])
        top.tier = 10
        self.assertFalse(matches(top))

    def test_vehicle_name_substring(self):
        matches = build_filters(self._args(vehicles=['t-34']))
        self.assertTrue(matches(make_info([('commander',)])))
        other = make_info([('commander',)])
        other.key = other.name = 'IS-7'
        other.user_string = '#ussr_vehicles:IS-7'
        self.assertFalse(matches(other))


class EndToEndTest(unittest.TestCase):

    def test_dry_run_then_apply_round_trips(self):
        state = {'schema': 4, 'vehicles': {'1': {
            'crew': {'0': COMMANDER_BLOB, '1': DRIVER_BLOB},
            'settings': 15}}, 'owned': {}, 'battleCrewReceipts': []}
        vehicles = {1: make_info([('commander',), ('driver',)])}
        matches = build_filters(argparse.Namespace(
            nation=None, tier=None, class_=None, vehicles=None))
        plans = plan_garage(state, vehicles, TEMPLATES, matches)
        self.assertEqual(1, len(plans))
        for key, info, changes, _ in plans:
            record = state['vehicles'][key]
            for slot, _c, _t, _tr, encoded in changes:
                record['crew'][str(slot)] = encoded
        # The result must remain parseable and carry the template skills.
        tankman = parse_tankman(base64.b64decode(
            state['vehicles']['1']['crew']['0']))
        self.assertEqual(8, len(tankman.skills))


if __name__ == '__main__':
    unittest.main()
