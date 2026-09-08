"""Tests for template save/load round trip."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.apply_crew_templates import (  # noqa: E402
    DEFAULT_TEMPLATES, load_templates, save_templates)


class SaveTemplatesTest(unittest.TestCase):

    def test_round_trip(self):
        templates = load_templates(DEFAULT_TEMPLATES)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            path = stream.name
        try:
            save_templates(path, templates)
            reloaded = load_templates(path)
        finally:
            os.unlink(path)
        self.assertEqual(templates, reloaded)

    def test_edited_entry_round_trips(self):
        templates = load_templates(DEFAULT_TEMPLATES)
        available = ['repair', 'fireFighting', 'camouflage', 'brotherhood',
                     'driver_badRoadsKing', 'driver_smoothDriving',
                     'driver_virtuoso']
        templates['driver'] = {'trained': available,
                               'training': 'driver_rammingMaster'}
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            path = stream.name
        try:
            save_templates(path, templates)
            reloaded = load_templates(path)
        finally:
            os.unlink(path)
        self.assertEqual(available, reloaded['driver']['trained'])
        self.assertEqual('driver_rammingMaster',
                         reloaded['driver']['training'])

    def test_saved_file_is_valid_json_with_comments(self):
        templates = load_templates(DEFAULT_TEMPLATES)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            path = stream.name
        try:
            save_templates(path, templates)
            with open(path, encoding='utf-8') as stream:
                payload = json.load(stream)
        finally:
            os.unlink(path)
        self.assertIn('_comment', payload)
        self.assertIn('templates', payload)


if __name__ == '__main__':
    unittest.main()
