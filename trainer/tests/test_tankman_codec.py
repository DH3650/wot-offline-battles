"""Tests for trainer.tankman_codec against synthetic and real save data."""

import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.tankman_codec import (  # noqa: E402
    ACTIVE_SKILLS, FIXED_SIZE, MAX_SKILL_LEVEL, ROLES, SKILL_INDICES,
    SKILL_NAMES, Tankman, TankmanFormatError, parse_tankman,
    serialize_tankman, with_skills)

GARAGE_STATE = os.environ.get(
    'TRAINER_GARAGE_STATE',
    os.path.join(
        os.environ.get('APPDATA', ''), 'Wargaming.net', 'WorldOfTanks',
        'offline_lan_0922', 'garage_state.json'))

# A real 19-byte crew descriptor with no skills from the live save.
NO_SKILL_BLOB = base64.b64decode('CAABZAAAAFMASAAJAEEGdBGXAQ==')
# A real 27-byte descriptor: 7 trained skills + one at 0%.
SKILLED_BLOB = bytes.fromhex(
    '1804016408131210110906080700' '00' 'e0000e0004000a003e120000')


class SkillTableTest(unittest.TestCase):

    def test_skill_names_match_client_constants(self):
        self.assertEqual(61, len(SKILL_NAMES))
        self.assertEqual('repair', SKILL_NAMES[6])
        self.assertEqual('fireFighting', SKILL_NAMES[7])
        self.assertEqual('camouflage', SKILL_NAMES[8])
        self.assertEqual('brotherhood', SKILL_NAMES[9])
        self.assertEqual('commander_tutor', SKILL_NAMES[16])
        self.assertEqual('commander_universalist', SKILL_NAMES[20])
        self.assertEqual('driver_virtuoso', SKILL_NAMES[28])
        self.assertEqual('driver_badRoadsKing', SKILL_NAMES[30])
        self.assertEqual('driver_tidyPerson', SKILL_NAMES[32])
        self.assertEqual('gunner_gunsmith', SKILL_NAMES[37])
        self.assertEqual('gunner_rancorous', SKILL_NAMES[40])
        self.assertEqual('loader_pedant', SKILL_NAMES[46])
        self.assertEqual('loader_intuition', SKILL_NAMES[48])
        self.assertEqual('radioman_inventor', SKILL_NAMES[53])
        self.assertEqual('radioman_lastEffort', SKILL_NAMES[56])

    def test_roles_have_skill_indices_1_to_5(self):
        self.assertEqual(
            {'commander': 1, 'radioman': 2, 'driver': 3, 'gunner': 4,
             'loader': 5},
            {role: SKILL_INDICES[role] for role in ROLES})

    def test_active_skills_count(self):
        self.assertEqual(25, len(ACTIVE_SKILLS))


class ParseTest(unittest.TestCase):

    def test_parse_no_skill_blob(self):
        tankman = parse_tankman(NO_SKILL_BLOB)
        self.assertEqual(0, tankman.nation_id)
        self.assertEqual(0, tankman.vehicle_type_id)
        self.assertEqual('commander', tankman.role)
        self.assertEqual(100, tankman.role_level)
        self.assertEqual([], tankman.skills)
        self.assertEqual(0, tankman.last_skill_level)
        self.assertEqual(0, tankman.flags)
        self.assertEqual(b'', tankman.suffix)

    def test_parse_skilled_blob(self):
        tankman = parse_tankman(SKILLED_BLOB)
        self.assertEqual('commander', tankman.role)
        self.assertEqual(8, len(tankman.skills))
        self.assertEqual(0, tankman.last_skill_level)
        self.assertEqual(
            ['commander_expert', 'commander_sixthSense', 'commander_tutor',
             'commander_eagleEye', 'brotherhood', 'repair', 'camouflage',
             'fireFighting'],
            tankman.skills)
        self.assertEqual(7, len(tankman.trained_skills()))

    def test_rejects_short_blob(self):
        with self.assertRaises(TankmanFormatError):
            parse_tankman(b'\x08\x00')

    def test_rejects_non_tankman_header(self):
        blob = bytearray(NO_SKILL_BLOB)
        blob[0] = 0x07
        with self.assertRaises(TankmanFormatError):
            parse_tankman(bytes(blob))

    def test_rejects_role_level_above_max(self):
        blob = bytearray(NO_SKILL_BLOB)
        blob[3] = 101
        with self.assertRaises(TankmanFormatError):
            parse_tankman(bytes(blob))

    def test_rejects_non_active_skill(self):
        # numSkills=1 with skill id 0 ('reserved' is not an active skill).
        blob = NO_SKILL_BLOB[:4] + b'\x01\x00' + NO_SKILL_BLOB[5:]
        with self.assertRaises(TankmanFormatError):
            parse_tankman(bytes(blob))


class RoundTripTest(unittest.TestCase):

    def test_serialize_parse_identity(self):
        for blob in (NO_SKILL_BLOB, SKILLED_BLOB):
            self.assertEqual(blob, serialize_tankman(parse_tankman(blob)))

    def test_with_skills_sets_seven_trained_plus_one_training(self):
        tankman = parse_tankman(NO_SKILL_BLOB)
        trained = ['driver_badRoadsKing', 'brotherhood', 'camouflage',
                   'repair', 'fireFighting', 'driver_smoothDriving',
                   'driver_virtuoso']
        updated = with_skills(tankman, trained, 'driver_rammingMaster')
        self.assertEqual(trained + ['driver_rammingMaster'], updated.skills)
        self.assertEqual(0, updated.last_skill_level)
        blob = serialize_tankman(updated)
        self.assertEqual(FIXED_SIZE + 8, len(blob))
        reparsed = parse_tankman(blob)
        self.assertEqual(trained, reparsed.trained_skills())
        self.assertEqual('driver_rammingMaster', reparsed.skills[-1])
        self.assertEqual(0, reparsed.last_skill_level)
        # Untouched fields survive.
        self.assertEqual(tankman.free_xp, reparsed.free_xp)
        self.assertEqual(tankman.flags, reparsed.flags)
        self.assertEqual(tankman.rank, reparsed.rank)

    def test_with_skills_without_training_marks_last_at_100(self):
        tankman = parse_tankman(NO_SKILL_BLOB)
        updated = with_skills(tankman, ['repair'], None)
        self.assertEqual(MAX_SKILL_LEVEL, updated.last_skill_level)


@unittest.skipUnless(os.path.isfile(GARAGE_STATE), 'no live garage state')
class LiveGarageCorpusTest(unittest.TestCase):
    """Every crew descriptor in the real save must round-trip byte-exact."""

    def test_all_crew_blobs_round_trip(self):
        with open(GARAGE_STATE, 'rb') as stream:
            state = json.load(stream)
        total = 0
        skilled = 0
        for vehicle in state['vehicles'].values():
            for encoded in vehicle.get('crew', {}).values():
                blob = base64.b64decode(encoded)
                tankman = parse_tankman(blob)
                self.assertEqual(blob, serialize_tankman(tankman))
                total += 1
                skilled += bool(tankman.skills)
        self.assertGreater(total, 2000)
        self.assertGreater(skilled, 100)


if __name__ == '__main__':
    unittest.main()
