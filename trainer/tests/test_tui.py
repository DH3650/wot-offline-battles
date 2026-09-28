"""Tests for trainer.tui pure helpers and scripted-key screen flow."""

import base64
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import trainer.tui as tui  # noqa: E402
from trainer.vehicle_db import VehicleInfo  # noqa: E402

NO_SKILL_BLOB = base64.b64encode(bytes.fromhex(
    '0800016400000000530048000900410674119701')).decode()
SKILLED_BLOB = base64.b64encode(bytes.fromhex(
    '1804016408131210110906080700' '00' 'e0000e0004000a003e120000')).decode()


def role_blob(role_id):
    """A skill-less crew blob with the given role byte."""
    raw = bytearray.fromhex('0800016400000000530048000900410674119701')
    raw[2] = role_id
    return base64.b64encode(bytes(raw)).decode()


T34_BLOBS = (role_blob(1), role_blob(2), role_blob(3), role_blob(5))


def make_info(key='R04_T-34', name='T-34', nation='ussr', tier=5,
              clazz='mediumTank'):
    return VehicleInfo(
        type_cd=1, nation=nation, nation_id=0, vehicle_type_id=0, key=key,
        user_string='#ussr_vehicles:%s' % name, name=name, tier=tier,
        clazz=clazz, crew=[('commander',), ('driver',)])


def make_state(*blobs):
    return {'vehicles': {'1': {'crew': {
        str(slot): blob for slot, blob in enumerate(blobs)}}}}


def make_state_with_stock():
    state = make_state(NO_SKILL_BLOB)
    state['owned'] = {'9': {'5625': 5}}
    return state


class CrewStatusTest(unittest.TestCase):

    def test_counts_skill_less_members(self):
        ready, total, broken = tui.crew_status(
            make_state(NO_SKILL_BLOB, NO_SKILL_BLOB)['vehicles']['1'])
        self.assertEqual((2, 2, 0), (ready, total, broken))

    def test_skilled_members_are_not_ready(self):
        ready, total, broken = tui.crew_status(
            make_state(SKILLED_BLOB, NO_SKILL_BLOB)['vehicles']['1'])
        self.assertEqual((1, 2, 0), (ready, total, broken))

    def test_broken_blobs_are_flagged(self):
        ready, total, broken = tui.crew_status(
            {'crew': {'0': base64.b64encode(b'\x01\x02').decode()}})
        self.assertEqual((0, 1, 1), (ready, total, broken))


class RowTest(unittest.TestCase):

    def test_rows_and_filters(self):
        state = make_state(NO_SKILL_BLOB)
        info = make_info()
        rows = tui.vehicle_rows(state, {1: info})
        self.assertEqual(1, len(rows))
        self.assertTrue(tui.row_matches(rows[0], set(), set(), set(), ''))
        self.assertTrue(tui.row_matches(
            rows[0], {'ussr'}, {5}, {'mediumTank'}, 't-34'))
        self.assertFalse(tui.row_matches(rows[0], {'china'}, set(), set(), ''))
        self.assertFalse(tui.row_matches(rows[0], set(), {10}, set(), ''))
        self.assertFalse(tui.row_matches(rows[0], set(), set(), set(), 'is-7'))

    def test_effective_selection_intersects_filters(self):
        state = {'vehicles': {
            '1': {'crew': {'0': NO_SKILL_BLOB}},
            '2': {'crew': {'0': NO_SKILL_BLOB}},
        }}
        t34 = make_info()
        is7 = make_info(key='R90_IS_7', name='IS-7', tier=10,
                        clazz='heavyTank')
        is7.type_cd = 2
        rows = tui.vehicle_rows(state, {1: t34, 2: is7})
        selected = {'1', '2'}
        no_filters = {'nations': set(), 'tiers': set(), 'classes': set(),
                      'text': ''}
        self.assertEqual({'1', '2'}, tui.effective_selection(
            rows, selected, no_filters))
        tier10 = dict(no_filters, tiers={10})
        self.assertEqual({'2'}, tui.effective_selection(
            rows, selected, tier10))
        self.assertEqual(set(), tui.effective_selection(
            rows, {'1'}, tier10))
        search = dict(no_filters, text='is-7')
        self.assertEqual({'2'}, tui.effective_selection(
            rows, selected, search))

    def test_format_row_marks_selection_and_status(self):
        state = make_state(NO_SKILL_BLOB)
        rows = tui.vehicle_rows(state, {1: make_info()})
        line = tui.format_row(rows[0], True, 200)
        self.assertIn('[x]', line)
        self.assertIn('白板 1/1', line)
        line = tui.format_row(rows[0], False, 200)
        self.assertIn('[ ]', line)


class EnhancementRowTest(unittest.TestCase):

    def setUp(self):
        self.choice = {
            'nation': 'ussr', 'vehicle': 'R04_T-34',
            'member': 'scripts/item_defs/vehicles/ussr/R04_T-34.xml',
            'tags': ('mediumTank',), 'vehicleClass': 'mediumTank',
            'level': 5, 'label': 'T-34',
        }

    def test_complete_roster_rows_use_single_choice_filters(self):
        row = tui.enhancement_rows([self.choice])[0]
        filters = {'nations': {'ussr'}, 'tiers': {5},
                   'classes': {'mediumTank'}, 'text': ''}
        self.assertTrue(tui.enhancement_row_matches(row, filters, 't-34'))
        filters['tiers'] = {10}
        self.assertFalse(tui.enhancement_row_matches(row, filters, ''))

    def test_custom_tag_is_rendered_as_lv_s(self):
        text = tui._colored_tags([{
            'tag': '火力', 'level': 'custom', 'exact': False,
        }], {'level_styles': {}})
        self.assertEqual(tui.GREEN + '[火力 Lv.S]' + tui.RESET, text)

    def test_picker_only_evaluates_the_highlighted_vehicle(self):
        choices = []
        for index in range(10):
            choice = dict(self.choice)
            choice['vehicle'] = 'Tank%d' % index
            choice['member'] = 'vehicles/Tank%d.xml' % index
            choice['label'] = 'Tank %d' % index
            choices.append(choice)
        tag_index = mock.Mock()
        tag_index.tags.return_value = []
        tag_index.cached_tags.return_value = []
        filters = {'nations': set(), 'tiers': set(),
                   'classes': set(), 'text': ''}
        with mock.patch.object(tui, 'read_key', return_value='esc'), \
                mock.patch.object(tui, 'draw'), \
                mock.patch.object(tui, 'terminal_size', return_value=(120, 30)):
            tui.enhancement_vehicle_picker(
                tui.enhancement_rows(choices), filters, tag_index,
                {'level_styles': {}})
        self.assertEqual(1, tag_index.tags.call_count)
        self.assertEqual(9, tag_index.cached_tags.call_count)

    def test_entering_enhancement_only_prunes_and_never_preloads(self):
        tag_index = mock.Mock()
        rows = tui.enhancement_rows([self.choice])
        tui._prepare_tag_cache(tag_index, rows)
        tag_index.prune_vehicles.assert_called_once_with([self.choice])
        tag_index.preload.assert_not_called()
        tag_index.stale_vehicles.assert_not_called()


class VehicleEditSessionTest(unittest.TestCase):

    def setUp(self):
        self.presets = {
            'level_styles': {
                '1': {'name': 'Lv1', 'color_cn': '蓝色'},
                '2': {'name': 'Lv2', 'color_cn': '紫色'},
                '3': {'name': 'Lv3', 'color_cn': '金色'},
            },
            'categories': {
                'mobility': {'tag': '机动'},
                'armor': {'tag': '装甲'},
            },
        }
        self.vehicle = {
            'member': 'vehicles/Tank.xml', 'vehicle': 'Tank', 'label': 'Tank',
            'nation': 'ussr'}
        self.plan = {
            'profile': 'SPG', 'vehicle': self.vehicle,
            'presetCategory': 'mobility,armor', 'presetLevel': 'combined',
            'selections': [
                {'category': 'mobility', 'level': '1'},
                {'category': 'armor', 'level': '2'},
            ],
            'changes': [{
                'fieldPath': 'x', 'label': 'Field',
                'currentValue': '1', 'replacementValue': '2',
            }],
            'affectedVehicles': [],
        }

    def test_current_levels_are_shown_on_every_category_with_colors(self):
        captured = {}

        def choose(title, items, subtitle=''):
            captured['items'] = dict(items)
            captured['subtitle'] = subtitle
            return 'q'

        tags = [{
            'category': 'mobility', 'tag': '机动',
            'level': '2', 'exact': True,
        }]
        with mock.patch.object(tui, 'menu', side_effect=choose):
            result = tui.vehicle_edit_session(
                'game', 'SPG', self.vehicle, self.presets, tags)

        self.assertFalse(result)
        self.assertIn(tui.PURPLE + 'Lv2' + tui.RESET,
                      captured['items']['mobility'])
        self.assertIn('[未套用]', captured['items']['armor'])
        self.assertIn('当前已套用', captured['subtitle'])
        self.assertIn(tui.PURPLE + 'Lv2' + tui.RESET,
                      captured['subtitle'])

    def test_level_labels_use_configured_terminal_colors(self):
        self.assertEqual(tui.BLUE + 'Lv1' + tui.RESET,
                         tui._level_label('1', self.presets))
        self.assertEqual(tui.PURPLE + 'Lv2' + tui.RESET,
                         tui._level_label('2', self.presets))
        self.assertEqual(tui.GOLD + 'Lv3' + tui.RESET,
                         tui._level_label('3', self.presets))
        self.assertEqual(tui.GREEN + 'Lv.S' + tui.RESET,
                         tui._level_label('S', self.presets))

    def test_details_escape_returns_to_editor_without_discarding(self):
        with mock.patch.object(
                tui, 'menu', side_effect=['mobility', '1', 'details', 'q']), \
                mock.patch.object(
                    tui, '_build_combined_vehicle_plan', return_value=self.plan), \
                mock.patch.object(tui, 'text_view') as viewed:
            result = tui.vehicle_edit_session(
                'game', 'SPG', self.vehicle, self.presets)
        self.assertFalse(result)
        self.assertTrue(any('方案细则' in call.args[0]
                            for call in viewed.call_args_list))

    def test_multiple_categories_are_applied_once(self):
        with mock.patch.object(
                tui, 'menu', side_effect=[
                    'mobility', '1', 'armor', '2', 'apply', 'a']), \
                mock.patch.object(
                    tui, '_build_combined_vehicle_plan', return_value=self.plan), \
                mock.patch.object(tui, 'text_view'), \
                mock.patch.object(
                    tui.vehicle_modifications, 'apply_plan',
                    return_value='backup') as applied:
            result = tui.vehicle_edit_session(
                'game', 'SPG', self.vehicle, self.presets)
        self.assertTrue(result)
        applied.assert_called_once_with(
            tui.vehicle_overlays, 'game', self.plan)

    def test_shared_modules_are_individually_kept_or_removed(self):
        plan = dict(self.plan)
        plan['action'] = 'remove'
        plan['vehicle'] = dict(self.vehicle)
        plan['changes'] = [
            {'member': 'vehicle.xml', 'fieldPath': 'speedLimits/forward',
             'label': 'Forward', 'shared': False,
             'affectedVehicles': ('Tank',), 'nation': 'ussr'},
            {'member': 'engines.xml', 'fieldPath': 'shared/E1/power',
             'label': 'Power', 'shared': True, 'component': 'E1',
             'affectedVehicles': ('Tank', 'Tank2'), 'nation': 'ussr'},
            {'member': 'guns.xml', 'fieldPath': 'shared/G1/reloadTime',
             'label': 'Reload', 'shared': True, 'component': 'G1',
             'affectedVehicles': ('Tank', 'Tank3'), 'nation': 'ussr'},
        ]

        with mock.patch.object(
                tui, 'menu', side_effect=['keep', 'remove']):
            selected = tui._choose_category_removals(plan)

        self.assertEqual(
            ['speedLimits/forward', 'shared/G1/reloadTime'],
            [change['fieldPath'] for change in selected['changes']])
        self.assertEqual(
            ['ussr:Tank', 'ussr:Tank3'], selected['affectedVehicles'])

    def test_existing_category_can_be_removed_from_the_edit_session(self):
        removal = dict(self.plan)
        removal.update({
            'action': 'remove', 'presetCategory': 'mobility',
            'presetLevel': 'off',
            'selections': [{'category': 'mobility', 'level': 'off'}],
        })
        removal['changes'] = [{
            'member': 'vehicle.xml', 'fieldPath': 'speedLimits/forward',
            'label': 'Forward', 'currentValue': '60',
            'replacementValue': '40', 'shared': False,
            'affectedVehicles': ('Tank',), 'nation': 'ussr',
        }]
        with mock.patch.object(
                tui, 'menu', side_effect=['mobility', 'remove', 'a']), \
                mock.patch.object(
                    tui.vehicle_overlays,
                    'list_vehicle_profile_field_choices', return_value=[]), \
                mock.patch.object(
                    tui.vehicle_modifications, 'plan_category_removal',
                    return_value=removal), \
                mock.patch.object(tui, 'text_view'), \
                mock.patch.object(
                    tui.vehicle_modifications, 'apply_plan',
                    return_value='backup') as applied:
            result = tui.vehicle_edit_session(
                'game', 'SPG', self.vehicle, self.presets)

        self.assertEqual('remove', result['action'])
        self.assertEqual(['ussr:Tank'], result['affectedVehicles'])
        applied.assert_called_once_with(
            tui.vehicle_overlays, 'game', result)


class ScriptedFlowTest(unittest.TestCase):
    """Drive the screens with a scripted key sequence, no real console."""

    def _run_main(self, keys, garage_path):
        rendered = []
        key_iter = iter(keys)
        with mock.patch.object(tui, 'read_key', side_effect=lambda: next(
                key_iter)), \
                mock.patch.object(tui, 'draw', side_effect=lambda lines: (
                    rendered.append('\n'.join(lines)))), \
                mock.patch.object(tui, '_enable_virtual_terminal'), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            result = tui.main([garage_path])
        return result, '\n'.join(rendered)

    def test_preview_then_quit(self):
        import tempfile
        state = make_state(*T34_BLOBS)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            result, screen = self._run_main(['1', '3', 'esc', 'q', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('预览变更', screen)
        self.assertIn('4 个乘员槽位', screen)

    def test_apply_overwrites_after_confirmation(self):
        import tempfile
        from trainer.tankman_codec import parse_tankman
        # 已有技能的乘员: 需要确认覆盖才会被重写
        state = make_state(SKILLED_BLOB)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 进车组管理 → 菜单1 勾选该车(空格→Enter) → 菜单4 → 冲突菜单选 o(覆盖)
            # → esc 看完变更清单 → y 确认 → 完成后 esc → q 返回 → q 退出
            result, screen = self._run_main(
                ['1', '1', ' ', 'enter', '4', 'o', 'esc', 'y', 'esc', 'q', 'q'],
                path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('已学技能', screen)
        crew = written['vehicles']['1']['crew']['0']
        tankman = parse_tankman(base64.b64decode(crew))
        self.assertEqual(8, len(tankman.skills))
        # 模板已替换原技能 (原第 1 技能是 commander_expert, 模板第 1 是 sixthSense)
        self.assertEqual('commander_sixthSense', tankman.skills[0])

    def test_apply_cancelled_by_user_writes_nothing(self):
        import tempfile
        state = make_state(NO_SKILL_BLOB)
        original = state['vehicles']['1']['crew']['0']
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 白板车默认已勾选 → 菜单4 → esc 看完清单 → n 拒绝 → esc 离开"已取消" → q
            result, screen = self._run_main(
                ['1', '1', 'enter', '4', 'esc', 'n', 'esc', 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('已取消', screen)
        self.assertEqual(original, written['vehicles']['1']['crew']['0'])

    def test_apply_skips_when_user_chooses_skip(self):
        import tempfile
        from trainer.tankman_codec import parse_tankman
        state = make_state(SKILLED_BLOB)
        original = state['vehicles']['1']['crew']['0']
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 菜单1 勾选该车 → 菜单4 → 冲突菜单选 s(仅白板) → 无可写 → esc → q
            result, screen = self._run_main(
                ['1', '1', ' ', 'enter', '4', 's', 'esc', 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertEqual(original, written['vehicles']['1']['crew']['0'])

    def test_template_menu_opens_and_quits(self):
        import tempfile
        state = make_state(NO_SKILL_BLOB)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            result, screen = self._run_main(['1', '5', 'esc', 'q', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('编辑技能模板', screen)
        self.assertIn('commander', screen)

    def test_quit_immediately(self):
        import tempfile
        state = make_state(NO_SKILL_BLOB)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            result, screen = self._run_main(['q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('车库助手', screen)
        self.assertNotIn('白板', screen)  # 顶层菜单不渲染车辆行

    def test_switch_slot_reloads_garage(self):
        import tempfile
        paths = []
        try:
            for state in (make_state(NO_SKILL_BLOB), make_state(SKILLED_BLOB)):
                stream = tempfile.NamedTemporaryFile(
                    'w', suffix='.json', delete=False)
                with stream:
                    json.dump(state, stream)
                paths.append(stream.name)
            slots = [
                {'id': 'default', 'name': 'default', 'path': paths[0],
                 'has_garage': True},
                {'id': 'Career-Mode', 'name': 'Career Mode',
                 'path': paths[1], 'has_garage': True},
            ]
            paths_by_id = {entry['id']: entry['path'] for entry in slots}
            with mock.patch.object(tui, 'list_save_slots',
                                   return_value=slots), \
                    mock.patch.object(tui, 'slot_garage_path',
                                      side_effect=lambda s: paths_by_id[s]):
                # 进车组管理看计数 → q 返回 → 菜单3 选存档 → ↓ 选第二个 → Enter
                # → 再进车组管理看计数 → q 返回 → q 退出
                result, screen = self._run_main(
                    ['1', 'q', '4', 'down', 'enter', '1', 'q', 'q'], paths[0])
        finally:
            for path in paths:
                os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('选择要编辑的存档', screen)
        # 切换后载入新存档: 白板计数从 1 变为 0
        self.assertIn('已选 1, 可选 1', screen)
        self.assertIn('已选 0, 可选 0', screen)
        self.assertIn('存档: Career-Mode', screen)

    def test_vehicle_enhancement_entry_is_wired(self):
        import tempfile
        state = make_state(NO_SKILL_BLOB)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            with mock.patch.object(
                    tui, 'vehicle_enhancement_menu', return_value='SPG') as opened:
                result, screen = self._run_main(['3', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        opened.assert_called_once_with(tui.DEFAULT_CLIENT_DIR, None)
        self.assertIn('车辆强化', screen)

    def test_gun_marks_entry_is_wired(self):
        import tempfile
        state = make_state(NO_SKILL_BLOB)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            with mock.patch.object(tui, 'gun_marks_menu') as opened:
                result, screen = self._run_main(['6', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        opened.assert_called_once()
        self.assertEqual(state, opened.call_args.args[0])
        self.assertEqual(path, opened.call_args.args[2])
        self.assertIn('伤害标记计算器', screen)

    def test_ammo_fix_entry_is_wired(self):
        import tempfile
        state = make_state(NO_SKILL_BLOB)
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            with mock.patch.object(tui, 'ammo_fix_menu') as opened:
                result, screen = self._run_main(['7', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        opened.assert_called_once()
        self.assertEqual(state, opened.call_args.args[0])
        self.assertEqual(path, opened.call_args.args[2])
        self.assertIn('修整装弹', screen)

    def _unlink_with_backups(self, path):
        import glob as glob_module
        for candidate in [path] + glob_module.glob(
                path + '.trainer-backup-*'):
            os.unlink(candidate)

    def test_inventory_apply_writes_stock(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 库存管理 → 预览 → 应用 → 看完清单 → y 确认 → 完成 → 返回 → 退出
            result, screen = self._run_main(
                ['2', '3', 'esc', '4', 'esc', 'y', 'esc', 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            self._unlink_with_backups(path)
        self.assertEqual(0, result)
        self.assertIn('库存管理', screen)
        self.assertIn('中型坦克炮输弹机', screen)
        # 默认全选: 已有 5 的改为 200, 没有的(deluxRammer)补为 200
        self.assertEqual(200, written['owned']['9']['5625'])
        self.assertEqual(200, written['owned']['9']['11769'])

    def test_inventory_custom_count(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 目标数量改为 50 → 应用 → 确认
            result, screen = self._run_main(
                ['2', '2', '5', '0', 'enter', '4', 'esc', 'y', 'esc',
                 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            self._unlink_with_backups(path)
        self.assertEqual(0, result)
        self.assertIn('目标数量', screen)
        self.assertEqual(50, written['owned']['9']['5625'])

    def test_inventory_deselect_all_has_no_changes(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            # 选择配件 → N 取消全部 → 应用 → 无改动提示
            result, screen = self._run_main(
                ['2', '1', 'n', 'enter', '4', 'esc', 'q', 'q'], path)
            with open(path, 'rb') as stream:
                written = json.load(stream)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('选择配件', screen)
        self.assertIn('没有可应用的改动', screen)
        self.assertEqual(5, written['owned']['9']['5625'])

    def test_inventory_stock_table(self):
        import tempfile
        state = make_state_with_stock()
        with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False) as stream:
            json.dump(state, stream)
            path = stream.name
        try:
            result, screen = self._run_main(
                ['2', '5', 'esc', 'q', 'q'], path)
        finally:
            os.unlink(path)
        self.assertEqual(0, result)
        self.assertIn('库存一览', screen)
        self.assertIn('输弹机:', screen)
        self.assertIn('库存 5', screen)


if __name__ == '__main__':
    unittest.main()
