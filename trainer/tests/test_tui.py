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
            result, screen = self._run_main(['3', 'esc', 'q'], path)
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
            # 菜单1 勾选该车(空格→Enter) → 菜单4 → 冲突菜单选 o(覆盖)
            # → esc 看完变更清单 → y 确认 → 完成后 esc → q 退出
            result, screen = self._run_main(
                ['1', ' ', 'enter', '4', 'o', 'esc', 'y', 'esc', 'q'], path)
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
                ['1', 'enter', '4', 'esc', 'n', 'esc', 'q'], path)
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
                ['1', ' ', 'enter', '4', 's', 'esc', 'q'], path)
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
            result, screen = self._run_main(['5', 'esc', 'q'], path)
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
        self.assertIn('乘员技能模板工具', screen)


if __name__ == '__main__':
    unittest.main()
