import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922.account_rpc import save_adapter


class SaveDocumentAdapterTests(unittest.TestCase):

    def test_merge_preserves_unknown_fields_and_newer_schema(self):
        raw = {'schema': 8, 'ledger': {
            'wallet': {'credits': 100, 'crystal': 9},
            'personalMissions': {'selected': [1]}}}
        before = {'schema': 7, 'ledger': {'wallet': {'credits': 100}}}
        after = {'schema': 7, 'ledger': {'wallet': {'credits': 75}}}

        self.assertEqual({
            'schema': 8,
            'ledger': {'wallet': {'credits': 75, 'crystal': 9},
                       'personalMissions': {'selected': [1]}}},
            save_adapter.merge_document(raw, before, after))

    def test_merge_applies_legacy_deletion_without_deleting_extensions(self):
        raw = {'vehicles': {'1': {'settings': 3, 'future': True},
                            '2': {'settings': 0, 'future': True}},
               'futureRoot': {'enabled': True}}
        before = {'vehicles': {'1': {'settings': 3}, '2': {'settings': 0}}}
        after = {'vehicles': {'2': {'settings': 0}}}

        self.assertEqual({
            'vehicles': {'2': {'settings': 0, 'future': True}},
            'futureRoot': {'enabled': True}},
            save_adapter.merge_document(raw, before, after))

    def test_merge_matches_receipt_rows_by_identity(self):
        raw = {'pending': [
            {'receipt_id': 'old', 'awarded': 1, 'future': 7},
            {'receipt_id': 'future-only', 'future': 8},
        ]}
        before = {'pending': [{'receipt_id': 'old', 'awarded': 1}]}
        after = {'pending': [{'receipt_id': 'old', 'awarded': 2},
                             {'receipt_id': 'new', 'awarded': 3}]}

        self.assertEqual({
            'pending': [
                {'receipt_id': 'old', 'awarded': 2, 'future': 7},
                {'receipt_id': 'new', 'awarded': 3},
                {'receipt_id': 'future-only', 'future': 8},
            ]}, save_adapter.merge_document(raw, before, after))

    def test_adapter_advances_only_after_commit_and_copies_inputs(self):
        raw = {'schema': 8, 'legacy': 1, 'future': {'value': 2}}
        baseline = {'schema': 7, 'legacy': 1}
        adapter = save_adapter.SaveDocumentAdapter()
        adapter.capture(raw, baseline)
        raw['future']['value'] = 99
        baseline['legacy'] = 99

        first = adapter.merge({'schema': 7, 'legacy': 2})
        self.assertEqual(
            {'schema': 8, 'legacy': 2, 'future': {'value': 2}}, first)
        adapter.commit(first, {'schema': 7, 'legacy': 2})
        first['future']['value'] = 100

        self.assertEqual(
            {'schema': 8, 'legacy': 3, 'future': {'value': 2}},
            adapter.merge({'schema': 7, 'legacy': 3}))

    def test_merge_does_not_mutate_inputs(self):
        raw = {'schema': 8, 'nested': {'legacy': 1, 'future': [1, 2]}}
        baseline = {'schema': 7, 'nested': {'legacy': 1}}
        updated = {'schema': 7, 'nested': {'legacy': 2}}
        originals = copy.deepcopy((raw, baseline, updated))

        save_adapter.merge_document(raw, baseline, updated)

        self.assertEqual(originals, (raw, baseline, updated))
