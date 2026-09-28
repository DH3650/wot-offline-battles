"""Tests for Trainer's read-only gun-mark calculator."""

import json
import os
import tempfile
import types
import unittest

from trainer import gun_marks
from trainer.vehicle_db import VehicleInfo


def vehicle(type_cd=49, tier=8, clazz='mediumTank'):
    return VehicleInfo(
        type_cd=type_cd, nation='china', nation_id=3, vehicle_type_id=0,
        key='Ch01_Type59', user_string='', name='Type 59', tier=tier,
        clazz=clazz, crew=[])


CATALOGUE = types.SimpleNamespace(
    MARKS_PERCENTILES=(5, 65, 85, 95, 100),
    MARKS_DAMAGE={49: (362, 1449, 2118, 2659, 3110)},
    MARKS_DAMAGE_FALLBACK={(8, 'mediumTank'):
                           (400, 1500, 2200, 2700, 3200)},
)


class GunMarksTest(unittest.TestCase):

    def test_state_joins_progress_to_thresholds(self):
        states = gun_marks.vehicle_states(
            {49: vehicle()},
            {'china:Ch01_Type59': {
                'movingAvgDamage': 2000, 'marksOnGun': 1,
                'damageRating': 8123, 'battles': 77}},
            owned_type_cds={'49'}, catalogue=CATALOGUE)
        self.assertEqual((1449, 2118, 2659), states[0]['thresholds'])
        self.assertEqual(2000, states[0]['movingAvgDamage'])
        self.assertEqual(1, states[0]['marksOnGun'])
        self.assertEqual(8123, states[0]['damageRating'])

    def test_unplayed_vehicle_starts_at_zero_and_uses_fallback(self):
        state = gun_marks.vehicle_states(
            {999: vehicle(type_cd=999)}, {}, owned_type_cds={'999'},
            catalogue=CATALOGUE)[0]
        self.assertEqual(0, state['movingAvgDamage'])
        self.assertEqual((1500, 2200, 2700), state['thresholds'])

    def test_unowned_and_low_tier_vehicles_are_absent(self):
        self.assertEqual([], gun_marks.vehicle_states(
            {49: vehicle()}, {}, owned_type_cds=set(), catalogue=CATALOGUE))
        self.assertEqual([], gun_marks.vehicle_states(
            {1: vehicle(type_cd=1, tier=4)}, {}, owned_type_cds={'1'},
            catalogue=CATALOGUE))

    def test_projection_uses_exact_integer_ema(self):
        state = gun_marks.vehicle_states(
            {49: vehicle()},
            {'china:Ch01_Type59': {
                'movingAvgDamage': 2000, 'marksOnGun': 1}},
            catalogue=CATALOGUE)[0]
        result = gun_marks.projection(state, 3000)
        self.assertEqual(gun_marks.next_average(2000, 3000),
                         result['nextAverage'])
        self.assertEqual(2, result['nextMark'])
        self.assertEqual(2118, result['target'])
        required = result['minimumNextCombinedDamage']
        self.assertGreaterEqual(gun_marks.next_average(2000, required), 2118)
        self.assertLess(gun_marks.next_average(2000, required - 1), 2118)
        self.assertEqual(7, result['battlesAtPlannedDamage'])

    def test_insufficient_pace_is_impossible(self):
        self.assertIsNone(gun_marks.battles_to_target(0, 1000, 1449))

    def test_three_marks_has_no_next_target(self):
        state = gun_marks.vehicle_states(
            {49: vehicle()},
            {'china:Ch01_Type59': {
                'movingAvgDamage': 100, 'marksOnGun': 3}},
            catalogue=CATALOGUE)[0]
        result = gun_marks.projection(state, 0)
        self.assertIsNone(result['nextMark'])
        self.assertIn('已经获得三环', gun_marks.report_text(state, result))

    def test_progress_is_read_beside_the_garage(self):
        with tempfile.TemporaryDirectory() as folder:
            garage = os.path.join(folder, 'garage_state.json')
            path = gun_marks.postbattle_path(garage)
            with open(path, 'w', encoding='utf-8') as stream:
                json.dump({
                    'schema': 1,
                    'progress': {'vehicles': {
                        'china:Ch01_Type59': {'movingAvgDamage': 1234}}},
                }, stream)
            rows = gun_marks.load_progress(path)
        self.assertEqual(1234, rows['china:Ch01_Type59']['movingAvgDamage'])


if __name__ == '__main__':
    unittest.main()
