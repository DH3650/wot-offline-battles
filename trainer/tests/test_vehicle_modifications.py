"""Tests for preset math, planning, tagging, and profile backups."""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from launcher import vehicle_overlays
from trainer import vehicle_modifications as modifications


class FakeOverlayError(Exception):
    pass


class FakeCore(object):
    @staticmethod
    def game_is_running():
        return False


class FakeService(object):
    VehicleOverlayError = FakeOverlayError
    core = FakeCore()

    def __init__(self, store_path=None, fields=None):
        self.store_path = store_path
        self.fields = list(fields or ())
        self.applied = []

    def profile_store_path(self, unused_root):
        return self.store_path

    def list_vehicle_profile_field_choices(
            self, unused_root, unused_profile, unused_member):
        return [dict(field) for field in self.fields]

    def apply_profile_edit(self, root, profile, member, field, value):
        self.applied.append((root, profile, member, field, value))


def field(path, original, current=None, category='guns', affected=('Tank',)):
    return {
        'member': 'scripts/item_defs/vehicles/ussr/R01_Tank.xml',
        'fieldPath': path,
        'fieldLabel': path,
        'originalValue': str(original),
        'currentValue': str(original if current is None else current),
        'category': category,
        'nation': 'ussr',
        'affectedVehicles': affected,
    }


class PresetTest(unittest.TestCase):

    def setUp(self):
        self.presets = modifications.load_presets()

    def test_category_removal_plans_only_existing_matching_edits(self):
        presets = {
            'level_styles': {},
            'categories': {'mobility': {'tag': '机动', 'rules': [{
                'field': 'speedLimits/*', 'operation': 'multiply',
                'value': [1.1, 1.2, 1.3],
            }]}},
        }
        vehicle = {
            'member': 'vehicle.xml', 'vehicle': 'Tank', 'nation': 'ussr'}
        fields = [
            dict(field('speedLimits/forward', 40, current=60,
                       category='vehicle'), shared=False, nation='ussr'),
            dict(field('speedLimits/backward', 20, current=20,
                       category='vehicle'), shared=False, nation='ussr'),
            dict(field('hull/maxHealth', 100, current=200,
                       category='vehicle'), shared=False, nation='ussr'),
        ]

        plan = modifications.plan_category_removal(
            FakeService(fields=fields), 'game', 'SPG', vehicle, presets,
            'mobility', fields=fields)

        self.assertEqual('remove', plan['action'])
        self.assertEqual(
            ['speedLimits/forward'],
            [change['fieldPath'] for change in plan['changes']])
        self.assertEqual('40', plan['changes'][0]['replacementValue'])

    def test_observation_sets_fixed_vision_radius(self):
        values = []
        for level in ('1', '2', '3'):
            rule = modifications.level_rules(
                self.presets, 'observation', level)[0]
            self.assertEqual('equal', rule['operation'])
            values.append(rule['value'])
        self.assertEqual([480, 680, 900], values)

    def test_firepower_lower_is_better_levels_are_reciprocals(self):
        values = []
        for level in ('1', '2', '3'):
            rules = modifications.level_rules(self.presets, 'firepower', level)
            reload_rule = next(rule for rule in rules
                               if rule['field'].endswith('reloadTime'))
            values.append(reload_rule['value'])
        self.assertAlmostEqual(2.0 / 3.0, values[0])
        self.assertEqual(0.5, values[1])
        self.assertAlmostEqual(1.0 / 3.0, values[2])

    def test_fire_control_is_gun_only_and_includes_ammunition_capacity(self):
        self.assertEqual('火控', self.presets['categories']['firepower']['tag'])
        ammo_values = []
        for level in ('1', '2', '3'):
            rules = modifications.level_rules(
                self.presets, 'firepower', level)
            ammo = next(rule for rule in rules
                        if rule['field'].endswith('maxAmmo'))
            ammo_values.append(ammo['value'])
            self.assertTrue(all(rule.get('component_categories') == ['guns']
                                for rule in rules))
        self.assertEqual([2, 3, 4], ammo_values)

    def test_preset_values_are_arrays_and_levels_select_by_position(self):
        for definition in self.presets['categories'].values():
            self.assertNotIn('levels', definition)
            for rule in definition['rules']:
                self.assertIsInstance(rule['value'], list)
                self.assertEqual(3, len(rule['value']))
                self.assertIn(rule['rounding'], ('round', 'truncate'))
                self.assertIsInstance(rule['digits'], int)
        values = [modifications.level_rules(
            self.presets, 'observation', level)[0]['value']
                  for level in ('1', '2', '3')]
        self.assertEqual([480, 680, 900], values)

    def test_equal_uses_the_value_at_the_selected_level(self):
        presets = {'categories': {'fixed': {'rules': [{
            'name': '固定值', 'field': 'value', 'operation': 'equal',
            'value': [100, 200, 300],
        }]}}}
        replacements = [modifications.apply_operation(
            '1', modifications.level_rules(presets, 'fixed', level)[0])
            for level in ('1', '2', '3')]
        self.assertEqual(['100', '200', '300'], replacements)

    def test_every_preset_rule_has_a_chinese_name(self):
        for category in self.presets['categories']:
            for level in ('1', '2', '3'):
                rules = modifications.level_rules(
                    self.presets, category, level)
                self.assertTrue(rules)
                self.assertTrue(all(rule.get('name') for rule in rules))

    def test_operations_support_vectors_and_bounds(self):
        self.assertEqual('15 30', modifications.apply_operation('10 20', {
            'operation': 'multiply', 'value': 1.5}))
        self.assertEqual('7 17', modifications.apply_operation('10 20', {
            'operation': 'subtract', 'value': 3}))
        self.assertEqual('13 23', modifications.apply_operation('10 20', {
            'operation': 'add', 'value': 3}))
        self.assertEqual('500', modifications.apply_operation('120', {
            'operation': 'equal', 'value': 500}))
        self.assertEqual('1', modifications.apply_operation('0.1', {
            'operation': 'subtract', 'value': 5, 'minimum': 1}))

    def test_rounding_and_truncation_strategies(self):
        self.assertEqual('53', modifications.apply_operation('35', {
            'operation': 'multiply', 'value': 1.5,
            'rounding': 'round', 'digits': 0}))
        self.assertEqual('1.23', modifications.apply_operation('1.239', {
            'operation': 'multiply', 'value': 1,
            'rounding': 'truncate', 'digits': 2}))
        self.assertEqual('-1.23', modifications.apply_operation('-1.239', {
            'operation': 'multiply', 'value': 1,
            'rounding': 'truncate', 'digits': 2}))

    def test_pitch_curve_changes_angles_not_curve_positions(self):
        rule = modifications.level_rules(
            self.presets, 'gun_depression', '2')[0]
        self.assertEqual('0 15 0.5 18 1 20', modifications.apply_operation(
            '0 5 0.5 8 1 10', rule))

    def test_t110e5_pitch_curve_keeps_all_six_positions(self):
        rule = modifications.level_rules(
            self.presets, 'gun_depression', '2')[0]
        original = ('0 8 0.40856 8 0.472222 5 0.527778 5 '
                    '0.59144 8 1 8')
        replacement = modifications.apply_operation(original, rule)
        self.assertEqual(
            '0 18 0.40856 18 0.472222 15 0.527778 15 '
            '0.59144 18 1 18', replacement)
        positions = [float(value) for value in replacement.split()[0::2]]
        self.assertEqual(0.0, positions[0])
        self.assertEqual(1.0, positions[-1])
        self.assertTrue(all(left < right for left, right in zip(
            positions, positions[1:])))

    def test_armor_adds_fixed_thickness_per_level(self):
        self.assertEqual('add', modifications.level_rules(
            self.presets, 'armor', '1')[0]['operation'])
        self.assertEqual('243', modifications.apply_operation(
            '123', modifications.level_rules(self.presets, 'armor', '1')[0]))
        self.assertEqual('323', modifications.apply_operation(
            '123', modifications.level_rules(self.presets, 'armor', '2')[0]))
        self.assertEqual('523', modifications.apply_operation(
            '123', modifications.level_rules(self.presets, 'armor', '3')[0]))

    def test_mobility_speed_adds_ten_per_level_with_caps(self):
        for level, expected in (('1', '80'), ('2', '90'), ('3', '90')):
            rule = next(rule for rule in modifications.level_rules(
                self.presets, 'mobility', level)
                        if rule['field'] == 'speedLimits/forward')
            self.assertEqual(expected, modifications.apply_operation('70', rule))
        reverse = next(rule for rule in modifications.level_rules(
            self.presets, 'mobility', '3')
                       if rule['field'] == 'speedLimits/backward')
        self.assertEqual('60', modifications.apply_operation('45', reverse))

    def test_track_resistance_is_truncated_to_two_decimals(self):
        rule = next(rule for rule in modifications.level_rules(
            self.presets, 'mobility', '1')
                    if rule['field'].endswith('terrainResistance'))
        self.assertEqual('0.74 1.4 2', modifications.apply_operation(
            '1.23456 2.34567 3.34567', rule))


class PlanAndTagTest(unittest.TestCase):

    def setUp(self):
        self.presets = modifications.load_presets()
        self.vehicle = {
            'nation': 'ussr', 'vehicle': 'R01_Tank', 'label': 'Tank',
            'member': 'scripts/item_defs/vehicles/ussr/R01_Tank.xml',
        }

    def test_plan_uses_original_value_and_reports_shared_vehicles(self):
        service = FakeService(fields=[field(
            'shared/Gun/reloadTime', 12, current=9, affected=('Tank', 'Other'))])
        plan = modifications.plan_vehicle(
            service, 'game', 'SPG', self.vehicle, self.presets,
            'firepower', '2')
        self.assertEqual('6', plan['changes'][0]['replacementValue'])
        self.assertEqual(['ussr:Other', 'ussr:Tank'], plan['affectedVehicles'])

    def test_custom_plan_sets_each_field_factor(self):
        first = field('shared/Gun/reloadTime', 12)
        second = field('shared/Gun/aimingTime', 3)
        service = FakeService(fields=[first, second])
        factors = {
            (first['member'], first['fieldPath']): 0.4,
            (second['member'], second['fieldPath']): 0.75,
        }
        plan = modifications.plan_custom_vehicle(
            service, 'game', 'SPG', self.vehicle, self.presets,
            'firepower', factors)
        self.assertEqual(['4.8', '2.25'], [
            change['replacementValue'] for change in plan['changes']])
        self.assertEqual('S', plan['presetLevel'])

    def test_custom_mobility_obeys_speed_caps_and_track_precision(self):
        forward = field('speedLimits/forward', 70, category='vehicle')
        backward = field('speedLimits/backward', 45, category='vehicle')
        tracks = field('chassis/C/terrainResistance',
                       '1.23456 2.34567 3.45678', category='chassis')
        service = FakeService(fields=[forward, backward, tracks])
        factors = {
            (forward['member'], forward['fieldPath']): 2,
            (backward['member'], backward['fieldPath']): 2,
            (tracks['member'], tracks['fieldPath']): 0.333333333333,
        }
        plan = modifications.plan_custom_vehicle(
            service, 'game', 'SPG', self.vehicle, self.presets,
            'mobility', factors)
        values = dict((change['fieldPath'], change['replacementValue'])
                      for change in plan['changes'])
        self.assertEqual('90', values['speedLimits/forward'])
        self.assertEqual('60', values['speedLimits/backward'])
        self.assertEqual('0.41 0.78 1.15', values[
            'chassis/C/terrainResistance'])

    def test_exact_and_custom_tags(self):
        exact = field('turrets0/T/guns/G/reloadTime', 12, current=6)
        tags = modifications.evaluate_tags([exact], self.presets)
        firepower = next(tag for tag in tags if tag['category'] == 'firepower')
        self.assertEqual('2', firepower['level'])
        custom = field('turrets0/T/guns/G/reloadTime', 12, current=5)
        tags = modifications.evaluate_tags([custom], self.presets)
        firepower = next(tag for tag in tags if tag['category'] == 'firepower')
        self.assertEqual('custom', firepower['level'])
        rendered = modifications.format_tags(tags, self.presets)
        self.assertIn('Lv.S', rendered)
        self.assertNotIn('自定义', rendered)

    def test_combines_categories_into_one_plan(self):
        mobility = {
            'profile': 'SPG', 'vehicle': self.vehicle,
            'presetCategory': 'mobility', 'presetLevel': '1',
            'changes': [{
                'member': self.vehicle['member'],
                'fieldPath': 'speedLimits/forward',
                'replacementValue': '70',
            }],
            'affectedVehicles': ['ussr:R01_Tank'],
        }
        armor = {
            'profile': 'SPG', 'vehicle': self.vehicle,
            'presetCategory': 'armor', 'presetLevel': '2',
            'changes': [{
                'member': self.vehicle['member'],
                'fieldPath': 'hull/armor/armor_1',
                'replacementValue': '500',
            }],
            'affectedVehicles': ['ussr:R01_Tank', 'ussr:Other'],
        }
        combined = modifications.combine_plans([mobility, armor])
        self.assertEqual(2, len(combined['changes']))
        self.assertEqual([
            {'category': 'mobility', 'level': '1'},
            {'category': 'armor', 'level': '2'},
        ], combined['selections'])
        self.assertEqual(
            ['ussr:Other', 'ussr:R01_Tank'], combined['affectedVehicles'])


class EliteModuleFilterTest(unittest.TestCase):

    def setUp(self):
        self.tops = {
            'chassis': 'C2', 'turrets': 'T2', 'engines': 'E2',
            'radios': 'R2', 'fuelTanks': 'F2', 'guns': 'G2',
            'shells': ['S1'],
        }

    def check(self, fields):
        kept = modifications.filter_top_module_fields(fields, self.tops)
        return set(field['fieldPath'] for field in kept)

    def test_vehicle_fields_are_always_kept(self):
        fields = [field('speedLimits/forward', 56, category='vehicle'),
                  field('hull/maxHealth', 320, category='vehicle')]
        self.assertEqual(2, len(self.check(fields)))

    def test_direct_module_fields_follow_the_elite_module_names(self):
        fields = [
            field('chassis/C1/maxLoad', 1, category='chassis'),
            field('chassis/C2/maxLoad', 2, category='chassis'),
            field('turrets0/T1/maxHealth', 1, category='turret'),
            field('turrets0/T2/maxHealth', 2, category='turret'),
        ]
        self.assertEqual({'chassis/C2/maxLoad', 'turrets0/T2/maxHealth'},
                         self.check(fields))

    def test_gun_fields_require_the_elite_turret_and_gun(self):
        local_stock = field('turrets0/T1/guns/G2/reloadTime', 1,
                            category='guns')
        local_elite = field('turrets0/T2/guns/G2/reloadTime', 2,
                            category='guns')
        local_other_gun = field('turrets0/T2/guns/G1/reloadTime', 3,
                                category='guns')
        kept = self.check([local_stock, local_elite, local_other_gun])
        self.assertEqual({'turrets0/T2/guns/G2/reloadTime'}, kept)

    def test_shared_components_and_shells_follow_the_elite_modules(self):
        fields = [
            field('shared/E1/power', 1, category='engines', affected=()),
            field('shared/E2/power', 2, category='engines', affected=()),
            field('shared/G2/reloadTime', 3, category='guns', affected=()),
            field('S1/damage/armor', 4, category='shells', affected=()),
            field('S2/damage/armor', 5, category='shells', affected=()),
        ]
        for entry in fields:
            entry['shared'] = True
            entry['component'] = entry['fieldPath'].split('/')[1] \
                if entry['category'] != 'shells' \
                else entry['fieldPath'].split('/')[0]
        self.assertEqual({'shared/E2/power', 'shared/G2/reloadTime',
                          'S1/damage/armor'}, self.check(fields))

    def test_missing_resolution_keeps_everything(self):
        fields = [field('chassis/C1/maxLoad', 1, category='chassis')]
        self.assertEqual(1, len(modifications.filter_top_module_fields(
            fields, None)))


class TopModuleCacheTest(unittest.TestCase):

    class Service(object):
        def __init__(self, signature):
            self.signature = signature
            self.calls = 0

        def source_package_signature(self, unused_root):
            return self.signature

        def vehicle_top_components(self, unused_root, member):
            self.calls += 1
            return {'guns': 'G', 'shells': []}

    def test_top_modules_are_cached_until_the_package_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            index = os.path.join(directory, 'index.sqlite3')
            service = self.Service((1, 100))
            first = modifications.top_modules(
                service, 'game', 'member.xml', index)
            second = modifications.top_modules(
                service, 'game', 'member.xml', index)
            self.assertEqual(first, second)
            self.assertEqual(1, service.calls)
            service.signature = (2, 100)
            modifications.top_modules(service, 'game', 'member.xml', index)
            self.assertEqual(2, service.calls)

    def test_legacy_index_is_merged_into_the_shared_vehicle_database(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = os.path.join(directory, 'vehicle-modifications.sqlite3')
            shared = os.path.join(directory, 'vehicle_data_cache.sqlite3')
            connection = modifications.ensure_index(legacy, legacy_path='')
            connection.execute(
                'INSERT INTO meta VALUES (?, ?)', ('legacy-key', 'kept'))
            connection.commit()
            connection.close()
            connection = sqlite3.connect(shared)
            connection.execute(
                'CREATE TABLE cache_values ('
                'kind TEXT NOT NULL, cache_key TEXT NOT NULL, data TEXT NOT NULL, '
                'PRIMARY KEY (kind, cache_key))')
            connection.execute(
                'INSERT INTO cache_values VALUES (?, ?, ?)',
                ('catalog', 'all', '[]'))
            connection.commit()
            connection.close()

            connection = modifications.ensure_index(
                shared, legacy_path=legacy)
            self.assertEqual(('kept',), connection.execute(
                'SELECT value FROM meta WHERE key = ?',
                ('legacy-key',)).fetchone())
            self.assertEqual(('[]',), connection.execute(
                'SELECT data FROM cache_values WHERE kind = ? AND cache_key = ?',
                ('catalog', 'all')).fetchone())
            connection.close()
            self.assertFalse(os.path.exists(legacy))


class TagIndexCacheTest(unittest.TestCase):

    class Service(FakeService):
        def __init__(self, store_path, fields):
            super(TagIndexCacheTest.Service, self).__init__(
                store_path, fields)
            self.evaluations = 0

        def list_vehicle_profile_field_choices(self, root, profile, member):
            self.evaluations += 1
            return super(TagIndexCacheTest.Service, self
                         ).list_vehicle_profile_field_choices(
                             root, profile, member)

    def setUp(self):
        self.presets = modifications.load_presets()
        self.vehicle = {
            'nation': 'ussr', 'vehicle': 'R01_Tank', 'label': 'Tank',
            'member': 'scripts/item_defs/vehicles/ussr/R01_Tank.xml',
        }

    def _index(self, directory, service, profile='SPG'):
        return modifications.VehicleTagIndex(
            service, 'game', profile, self.presets,
            index_path=os.path.join(directory, 'index.sqlite3'))

    def _store(self, directory, members):
        store = os.path.join(directory, 'vehicle_profiles.json')
        with open(store, 'w', encoding='utf-8') as stream:
            json.dump({'schema': 1, 'profiles': [
                {'name': 'SPG', 'members': members},
            ]}, stream)
        return store

    def test_preload_caches_tags_and_skips_cached_vehicles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, [])
            service = self.Service(store, [field(
                'turrets0/T/guns/G/reloadTime', 12, current=6)])
            index = self._index(directory, service)
            done, total = index.preload([self.vehicle])
            self.assertEqual((1, 1), (done, total))
            self.assertEqual(1, service.evaluations)
            done, total = index.preload([self.vehicle])
            self.assertEqual((0, 0), (done, total))
            self.assertEqual(1, service.evaluations)
            tags = index.tags(self.vehicle)
            self.assertEqual(1, service.evaluations)
            firepower = next(tag for tag in tags
                             if tag['category'] == 'firepower')
            self.assertEqual('2', firepower['level'])
            index.close()

    def test_resync_adopts_new_hashes_and_refresh_updates_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, [])
            service = self.Service(store, [field(
                'turrets0/T/guns/G/reloadTime', 12, current=6)])
            index = self._index(directory, service)
            index.preload([self.vehicle])
            self.assertFalse(index.resync())
            self._store(directory, [{'sourceMember': 'edited'}])
            self.assertTrue(index.resync())
            service.fields = [field(
                'turrets0/T/guns/G/reloadTime', 12, current=12)]
            index.refresh([self.vehicle])
            tags = index.cached_tags(self.vehicle)
            self.assertEqual(2, service.evaluations)
            self.assertFalse(any(
                tag['category'] == 'firepower' for tag in tags))
            index.close()

    def test_profile_sections_invalidate_only_dependent_vehicles(self):
        with tempfile.TemporaryDirectory() as directory:
            vehicle_section = {
                'sourceMember': self.vehicle['member'],
                'edits': [{'fieldPath': 'speed', 'replacementValue': 1}],
            }
            unrelated = {
                'sourceMember': 'scripts/item_defs/vehicles/usa/Other.xml',
                'edits': [{'fieldPath': 'speed', 'replacementValue': 1}],
            }
            store = self._store(directory, [vehicle_section, unrelated])
            service = self.Service(store, [field(
                'turrets0/T/guns/G/reloadTime', 12, current=6)])
            index = self._index(directory, service)
            index.preload([self.vehicle])
            index.close()

            unrelated['edits'][0]['replacementValue'] = 2
            self._store(directory, [vehicle_section, unrelated])
            index = self._index(directory, service)
            self.assertEqual([], index.stale_vehicles([self.vehicle]))
            self.assertTrue(index.cached_tags(self.vehicle))
            index.close()

            vehicle_section['edits'][0]['replacementValue'] = 2
            self._store(directory, [vehicle_section, unrelated])
            index = self._index(directory, service)
            self.assertEqual([self.vehicle], index.stale_vehicles(
                [self.vehicle]))
            self.assertEqual([], index.cached_tags(self.vehicle))
            index.tags(self.vehicle)
            self.assertEqual(2, service.evaluations)
            index.close()

    def test_prune_drops_vehicles_missing_from_the_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, [])
            service = self.Service(store, [])
            index = self._index(directory, service)
            index.preload([self.vehicle])
            self.assertEqual(1, index.prune_vehicles([]))
            self.assertEqual([], index.cached_tags(self.vehicle))
            index.close()


class BackupTest(unittest.TestCase):

    def test_apply_plan_uses_one_batch_call_when_the_service_supports_it(self):
        class BatchService(FakeService):
            def __init__(self, store_path):
                super(BatchService, self).__init__(store_path=store_path)
                self.batches = []

            def apply_profile_edits(self, root, profile, changes):
                self.batches.append((root, profile, list(changes)))

        with tempfile.TemporaryDirectory() as directory:
            store = os.path.join(directory, 'vehicle_profiles.json')
            backup_root = os.path.join(directory, 'backups')
            index = os.path.join(directory, 'index.sqlite3')
            with open(store, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'profiles': [
                    {'name': 'SPG', 'members': []},
                ]}, stream)
            service = BatchService(store)
            changes = [
                {'member': 'vehicle.xml', 'fieldPath': 'speed/forward',
                 'replacementValue': '40'},
                {'member': 'vehicle.xml', 'fieldPath': 'speed/backward',
                 'replacementValue': '12'},
            ]
            plan = {
                'profile': 'SPG', 'changes': changes,
                'vehicle': {'vehicle': 'M4A1', 'label': 'M4A1 升级型'},
                'presetCategory': 'combined', 'presetLevel': '2',
                'selections': [],
            }

            modifications.apply_plan(
                service, 'game', plan, backup_root, index)

            self.assertEqual([('game', 'SPG', changes)], service.batches)
            self.assertEqual([], service.applied)

    def test_apply_plan_uses_the_selective_remove_transaction(self):
        class RemoveService(FakeService):
            def __init__(self, store_path):
                super(RemoveService, self).__init__(store_path=store_path)
                self.removals = []

            def remove_profile_edits(self, root, profile, changes):
                self.removals.append((root, profile, list(changes)))

        with tempfile.TemporaryDirectory() as directory:
            store = os.path.join(directory, 'vehicle_profiles.json')
            backup_root = os.path.join(directory, 'backups')
            index = os.path.join(directory, 'index.sqlite3')
            with open(store, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'profiles': [
                    {'name': 'SPG', 'members': []},
                ]}, stream)
            service = RemoveService(store)
            changes = [{'member': 'vehicle.xml', 'fieldPath': 'speed/forward'}]
            plan = {
                'profile': 'SPG', 'changes': changes, 'action': 'remove',
                'vehicle': {'vehicle': 'M4A1', 'label': 'M4A1 升级型'},
                'presetCategory': 'mobility', 'presetLevel': 'off',
                'selections': [{'category': 'mobility', 'level': 'off'}],
            }

            modifications.apply_plan(
                service, 'game', plan, backup_root, index)

            self.assertEqual([('game', 'SPG', changes)], service.removals)
            self.assertEqual([], service.applied)

    def test_backup_and_restore_replace_only_named_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = os.path.join(directory, 'vehicle_profiles.json')
            backup_root = os.path.join(directory, 'backups')
            index = os.path.join(directory, 'index.sqlite3')
            original = {
                'schema': 1,
                'profiles': [
                    {'name': 'SPG', 'members': [{'sourceMember': 'old'}]},
                    {'name': 'Other', 'members': [{'sourceMember': 'keep'}]},
                ],
            }
            with open(store, 'w', encoding='utf-8') as stream:
                json.dump(original, stream)
            service = FakeService(store_path=store)
            saved = modifications.backup_profile(
                service, 'game', 'SPG', 'Object 261', 'firepower', '2',
                backup_root, index, datetime.datetime(2026, 9, 21, 18, 42, 30))
            self.assertIn('2026-09-21_18-42-30_before_Object_261', saved)
            changed = dict(original)
            changed['profiles'] = [
                {'name': 'SPG', 'members': [{'sourceMember': 'new'}]},
                original['profiles'][1],
            ]
            with open(store, 'w', encoding='utf-8') as stream:
                json.dump(changed, stream)
            backup = modifications.list_backups(backup_root)[0]
            modifications.restore_backup(
                service, 'game', backup, backup_root, index)
            with open(store, 'r', encoding='utf-8') as stream:
                restored = json.load(stream)
            self.assertEqual('old', restored['profiles'][0]['members'][0][
                'sourceMember'])
            self.assertEqual('keep', restored['profiles'][1]['members'][0][
                'sourceMember'])


class DefaultProfileTest(unittest.TestCase):

    class Service(FakeService):
        def __init__(self, names):
            super(DefaultProfileTest.Service, self).__init__()
            self.names = list(names)

        def list_vehicle_profiles(self, unused_root):
            return list(self.names)

    def test_prefers_the_profile_selected_in_the_launcher(self):
        service = self.Service(['SPG', 'France', 'Other'])
        with mock.patch('launcher.core.load_settings',
                        return_value={'vehicle_profile': 'France'}):
            self.assertEqual(
                'France', modifications.default_profile(service, 'game'))

    def test_case_insensitive_match_returns_the_real_name(self):
        service = self.Service(['SPG', 'France'])
        with mock.patch('launcher.core.load_settings',
                        return_value={'vehicle_profile': 'france'}):
            self.assertEqual(
                'France', modifications.default_profile(service, 'game'))

    def test_stock_label_falls_back_to_spg(self):
        service = self.Service(['SPG', 'France'])
        with mock.patch(
                'launcher.core.load_settings',
                return_value={'vehicle_profile':
                              vehicle_overlays.ORIGINAL_PROFILE_LABEL}):
            self.assertEqual(
                'SPG', modifications.default_profile(service, 'game'))

    def test_missing_selection_falls_back_to_spg(self):
        service = self.Service(['SPG', 'France'])
        with mock.patch('launcher.core.load_settings', return_value={}):
            self.assertEqual(
                'SPG', modifications.default_profile(service, 'game'))

    def test_stale_selection_falls_back_to_spg(self):
        service = self.Service(['SPG'])
        with mock.patch('launcher.core.load_settings',
                        return_value={'vehicle_profile': 'France'}):
            self.assertEqual(
                'SPG', modifications.default_profile(service, 'game'))


if __name__ == '__main__':
    unittest.main()
