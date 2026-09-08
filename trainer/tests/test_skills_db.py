"""Tests for trainer.skills_db."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.skills_db import (  # noqa: E402
    ACTIVE_SKILLS, SKILL_NAMES_CN, describe_skill, skills_for_roles,
    validate_skill_assignment)


class SkillsDbTest(unittest.TestCase):

    def test_every_active_skill_has_a_cn_name(self):
        self.assertEqual(set(ACTIVE_SKILLS), set(SKILL_NAMES_CN))

    def test_common_skills_available_to_every_role(self):
        for role in ('commander', 'driver', 'gunner', 'loader', 'radioman'):
            allowed = skills_for_roles((role,))
            for common in ('repair', 'fireFighting', 'camouflage',
                           'brotherhood'):
                self.assertIn(common, allowed)

    def test_role_combination_unions_skills(self):
        allowed = skills_for_roles(('commander', 'radioman'))
        self.assertIn('commander_sixthSense', allowed)
        self.assertIn('radioman_finder', allowed)
        self.assertNotIn('driver_badRoadsKing', allowed)

    def test_validate_reports_foreign_skills(self):
        errors = validate_skill_assignment(
            ('commander',), ['commander_sixthSense', 'driver_badRoadsKing'])
        self.assertEqual(['driver_badRoadsKing'], errors)
        self.assertEqual(
            [], validate_skill_assignment(
                ('commander', 'driver'), ['driver_badRoadsKing']))

    def test_describe_skill(self):
        self.assertEqual(
            'driver_badRoadsKing (如履平地)',
            describe_skill('driver_badRoadsKing'))


if __name__ == '__main__':
    unittest.main()
