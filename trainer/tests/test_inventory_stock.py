"""Tests for trainer.inventory_stock depot editing logic."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trainer.artefact_db import ArtefactInfo  # noqa: E402
from trainer.inventory_stock import (  # noqa: E402
    apply_stock, category_of, current_stock, plan_stock, stock_rows,
    stock_table_text)


def make_artefacts():
    return {
        5625: ArtefactInfo(
            compact_descr=5625, item_type=9, artefact_id=21,
            key='mediumCaliberTankRammer',
            user_string='#artefacts:mediumCaliberTankRammer/name',
            name='中型坦克炮输弹机', price=200000, tags=['rammer']),
        5881: ArtefactInfo(
            compact_descr=5881, item_type=9, artefact_id=23,
            key='mediumCaliberHowitzerRammer',
            user_string='#artefacts:mediumCaliberHowitzerRammer/name',
            name='中型火炮输弹机', price=300000, tags=['rammer']),
        11769: ArtefactInfo(
            compact_descr=11769, item_type=9, artefact_id=45,
            key='deluxRammer', user_string='#artefacts:deluxRammer/name',
            name='改进型装填系统', price=None, tags=['rammer', 'deluxe']),
    }


class CategoryTest(unittest.TestCase):

    def test_known_categories(self):
        self.assertEqual('输弹机', category_of('mediumCaliberTankRammer'))
        self.assertEqual('输弹机', category_of('deluxRammer'))
        self.assertEqual('通风系统', category_of('improvedVentilation_class2'))
        self.assertEqual('垂直稳定器', category_of('aimingStabilizer_Mk1'))
        self.assertEqual('炮控系统', category_of('deluxEnhancedAimDrives'))

    def test_unknown_falls_back(self):
        self.assertEqual('其他', category_of('somethingExotic'))


class StockTest(unittest.TestCase):

    def test_current_stock_handles_missing_owned(self):
        self.assertEqual(0, current_stock({}, 5625))
        self.assertEqual(0, current_stock({'owned': {}}, 5625))
        self.assertEqual(5, current_stock({'owned': {'9': {'5625': 5}}}, 5625))

    def test_stock_rows_sorted_with_counts(self):
        state = {'owned': {'9': {'11769': 7}}}
        rows = stock_rows(state, make_artefacts())
        self.assertEqual([5625, 5881, 11769], [row['cd'] for row in rows])
        self.assertEqual(0, rows[0]['current'])
        self.assertEqual(7, rows[2]['current'])
        self.assertEqual('输弹机', rows[0]['category'])

    def test_plan_stock_only_reports_changes(self):
        state = {'owned': {'9': {'5625': 200, '5881': 3}}}
        changes = plan_stock(state, make_artefacts(), {5625, 5881, 11769}, 200)
        self.assertEqual([(5881, 3, 200), (11769, 0, 200)],
                         [(i.compact_descr, old, new)
                          for i, old, new in changes])

    def test_plan_stock_skips_unknown_compact_descr(self):
        self.assertEqual([], plan_stock({}, make_artefacts(), {999}, 200))

    def test_apply_stock_writes_and_drops_zero(self):
        state = {'owned': {'9': {'5625': 5, '5881': 8}}}
        artefacts = make_artefacts()
        changes = plan_stock(state, artefacts, {5625, 5881, 11769}, 0)
        # 5625 与 5881 变为 0 -> 删除键; 11769 已是 0 -> 不在变更里
        self.assertEqual(2, apply_stock(state, changes))
        self.assertEqual({}, state['owned']['9'])

    def test_apply_stock_creates_owned_when_missing(self):
        state = {}
        changes = plan_stock(state, make_artefacts(), {5625}, 200)
        self.assertEqual(1, apply_stock(state, changes))
        self.assertEqual(200, state['owned']['9']['5625'])


class TableTest(unittest.TestCase):

    def test_table_groups_by_category_with_counts(self):
        state = {'owned': {'9': {'5625': 5}}}
        text = stock_table_text(state, make_artefacts())
        self.assertIn('输弹机:', text)
        self.assertIn('中型坦克炮输弹机', text)
        self.assertIn('库存 5', text)
        self.assertIn('高级', text)  # deluxRammer 无 XML 单价
        # 组内按价格升序: 中型坦克炮(200000) 在 中型火炮(300000) 之前
        self.assertLess(text.index('中型坦克炮输弹机'),
                        text.index('中型火炮输弹机'))


if __name__ == '__main__':
    unittest.main()
