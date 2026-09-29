# Forward-Compatible Save Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let v0.7.7 read the fork's extended save documents while ensuring every v0.7.7 write changes only legacy-owned data and preserves newer structures byte-for-data.

**Architecture:** Add a self-contained Python 2.7-compatible `SaveDocumentAdapter` that owns raw-document retention, baseline snapshots, three-way merging, and post-write state. Existing stores receive only three thin integration calls (`capture`, `merge`, and `commit`); no merge policy is embedded in garage or post-battle business logic. The adapter applies only differences between the legacy baseline and new legacy payload onto the raw document, preserving dictionary extensions and stable-identity receipt fields while retaining intentional legacy deletions and newer schema numbers.

**Tech Stack:** CPython 2.7-compatible client code, JSON documents, Python 3 unittest/pytest compatibility tests.

## Global Constraints

- The implementation remains based on tag `v0.7.7` / commit `54603d6e68d345c45c9aaa5e6a2cd45200c5436b`.
- Client code under `src/res/scripts/client/` must parse and run on Python 2.7.
- Reading a newer save ignores structures unknown to v0.7.7 rather than exposing them to legacy gameplay code.
- Writing from v0.7.7 updates only legacy-owned values and does not overwrite newer structures.
- Compatibility is limited to the demonstrated fork formats: garage schema 8 and the extended schema-1 post-battle document.
- Existing backup, quarantine, durability, and atomic-write behavior remains in force.

---

## File Structure

- Create `src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/save_adapter.py`: generic, Python 2.7-compatible raw/baseline/updated JSON merge logic.
- Modify `src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/garage_store.py`: accept schema 8, retain its raw document, establish a schema-7 baseline after restore, and merge writes through the adapter.
- Modify `src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/postbattle_store.py`: retain the extended schema-1 raw document and normalized baseline, then merge saves through the adapter.
- Create `tests/test_port_0922_save_adapter.py`: focused unit tests for recursive updates, deletions, unknown-field preservation, schema preservation, and receipt-list identity matching.
- Modify `tests/test_port_0922_garage.py`: end-to-end schema-8 garage load/write regression coverage.
- Modify `tests/test_port_0922_postbattle.py`: end-to-end extended post-battle load/write regression coverage.

---

### Task 1: Three-Way JSON Save Adapter

**Files:**
- Create: `src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/save_adapter.py`
- Test: `tests/test_port_0922_save_adapter.py`

**Interfaces:**
- Consumes: raw JSON-compatible `dict`, normalized legacy baseline `dict`, and newly generated legacy payload `dict`.
- Produces: `merge_document(raw, baseline, updated) -> dict` and `SaveDocumentAdapter.capture/merge/commit`, without mutating caller-owned inputs.

- [ ] **Step 1: Write failing adapter tests**

Cover the concrete contract with tests equivalent to:

```python
def test_merge_preserves_unknown_fields_and_newer_schema():
    raw = {'schema': 8, 'ledger': {
        'wallet': {'credits': 100, 'crystal': 9},
        'personalMissions': {'selected': [1]}}}
    before = {'schema': 7, 'ledger': {'wallet': {'credits': 100}}}
    after = {'schema': 7, 'ledger': {'wallet': {'credits': 75}}}
    assert merge_document(raw, before, after) == {
        'schema': 8,
        'ledger': {'wallet': {'credits': 75, 'crystal': 9},
                   'personalMissions': {'selected': [1]}}}


def test_merge_applies_legacy_deletion_without_deleting_extensions():
    raw = {'vehicles': {'1': {'settings': 3, 'future': True},
                        '2': {'settings': 0, 'future': True}},
           'futureRoot': {'enabled': True}}
    before = {'vehicles': {'1': {'settings': 3}, '2': {'settings': 0}}}
    after = {'vehicles': {'2': {'settings': 0}}}
    assert merge_document(raw, before, after) == {
        'vehicles': {'2': {'settings': 0, 'future': True}},
        'futureRoot': {'enabled': True}}


def test_merge_matches_receipt_rows_by_identity():
    raw = {'pending': [{'receipt_id': 'old', 'awarded': 1, 'future': 7}]}
    before = {'pending': [{'receipt_id': 'old', 'awarded': 1}]}
    after = {'pending': [{'receipt_id': 'old', 'awarded': 2},
                         {'receipt_id': 'new', 'awarded': 3}]}
    assert merge_document(raw, before, after) == {
        'pending': [{'receipt_id': 'old', 'awarded': 2, 'future': 7},
                    {'receipt_id': 'new', 'awarded': 3}]}
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m unittest tests.test_port_0922_save_adapter`

Expected: collection/import failure because `save_adapter.py` does not exist.

- [ ] **Step 3: Implement the adapter**

Implement these rules in `merge_document`:

```python
def merge_document(raw, baseline, updated):
    """Apply legacy changes to raw while retaining data legacy never owned."""


class SaveDocumentAdapter(object):
    def capture(self, raw, baseline):
        """Remember independent raw and legacy-normalized snapshots."""

    def merge(self, updated):
        """Return the document to write without changing adapter state."""

    def commit(self, written, updated):
        """Advance snapshots only after the caller's atomic write succeeds."""
```

- Deep-copy every returned branch so callers cannot mutate the stored baseline.
- If `baseline == updated`, return a deep copy of `raw`; this is what preserves an untouched extension subtree.
- For dictionaries, recurse over legacy keys. A key removed from `updated` is removed from the raw result; a raw-only key is retained.
- Treat `schema` specially: when both values are non-boolean integers, retain `max(raw_schema, updated_schema)` so v0.7.7 cannot downgrade schema 8.
- For lists of dictionaries, match rows by the first stable identity available in `receipt_id`, `arena_unique_id`, `id`, or `key`; recursively merge matched rows, append new rows, remove legacy rows intentionally omitted from `updated`, and retain raw-only extension rows.
- Replace changed primitive lists and changed scalar values with deep copies of `updated`.

- [ ] **Step 4: Run adapter tests**

Run: `python -m unittest tests.test_port_0922_save_adapter`

Expected: all adapter tests pass.

- [ ] **Step 5: Commit the isolated adapter**

```bash
git add src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/save_adapter.py tests/test_port_0922_save_adapter.py
git commit -m "feat: add forward-compatible save merge adapter"
```

---

### Task 2: Garage Schema-8 Read/Write Compatibility

**Files:**
- Modify: `src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/garage_store.py:47-48,355-379,386-448,538-626,830-909,1130-1155`
- Modify: `tests/test_port_0922_garage.py` in `GarageSaveDurabilityTests`

**Interfaces:**
- Consumes: `save_adapter.merge_document(raw, baseline, updated)` from Task 1.
- Produces: `GarageStore` support for schema 8 with preservation of unknown root, ledger, wallet, vehicle, and battle-receipt fields.

- [ ] **Step 1: Add a failing schema-8 compatibility test**

Create a normal schema-7 fixture through the existing store, then extend it exactly as the fork does:

```python
saved['schema'] = 8
saved['futureRoot'] = {'owner': 'fork'}
saved['ledger']['wallet']['crystal'] = 27
saved['ledger']['premiumExpiryTime'] = 1900000000
saved['ledger']['personalMissions'] = {'regular': [1, 16]}
saved['vehicles'][vehicle_key]['personalMissionVehicleSource'] = 'reward:15'
saved['battleCrewReceipts'][0]['income'] = {'premium': True}
```

Load it into a fresh v0.7.7 snapshot, mutate one legacy value such as credits or vehicle settings, flush, and assert:

- the legacy mutation is persisted;
- `schema` remains `8`;
- all listed fork fields remain unchanged;
- no fork field appears in the in-memory legacy snapshot merely because it was preserved on disk.

- [ ] **Step 2: Run the garage regression and verify failure**

Run: `python -m unittest tests.test_port_0922_garage.GarageSaveDurabilityTests.test_schema_8_updates_legacy_fields_without_overwriting_extensions`

Expected: restore rejects schema 8 or the subsequent write drops fork-owned fields.

- [ ] **Step 3: Retain raw and normalized garage baselines**

Update the module and store state with one adapter instance rather than adding raw/baseline merge state to the store:

```python
from gui.mods.offline_lan_0922.account_rpc import save_adapter

READABLE_SCHEMAS = (3, 4, 5, 6, 7, 8)

# In GarageStore.__init__
self._save_adapter = save_adapter.SaveDocumentAdapter()
```

After `apply` has completed its existing normalization and receipt loading, call `self._save_adapter.capture(stored, self._payload(snapshot))`. Do not copy extension data into the gameplay snapshot.

- [ ] **Step 4: Merge every garage write onto the raw document**

Immediately before `port_config.write_json`, compute:

```python
write_payload = self._save_adapter.merge(payload)
```

Keep shrink/refusal checks based on the legacy `payload`, write `write_payload`, then call `self._save_adapter.commit(write_payload, payload)` only after the atomic write succeeds. Keep `_remember_saved(payload)` on the legacy projection so newer extension maps do not alter v0.7.7's safety counts.

- [ ] **Step 5: Run garage tests**

Run: `$env:PYTHONPATH='tests'; python -m unittest test_port_0922_garage test_port_0922_economy_regressions`

Expected: all tests pass, including the schema-8 preservation regression.

- [ ] **Step 6: Commit garage compatibility**

```bash
git add src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/garage_store.py tests/test_port_0922_garage.py
git commit -m "feat: preserve schema 8 garage extensions"
```

---

### Task 3: Extended Post-Battle Document Compatibility

**Files:**
- Modify: `src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/postbattle_store.py:900-920,1270-1335,1439-1459`
- Modify: `tests/test_port_0922_postbattle.py`

**Interfaces:**
- Consumes: `save_adapter.merge_document(raw, baseline, updated)` from Task 1.
- Produces: `PostBattleStore` schema-1 writes that preserve fork-only receipt, progress, award, and root fields.

- [ ] **Step 1: Add a failing extended post-battle test**

Start from a valid current file and add demonstrated fork fields:

```python
saved['futureRoot'] = {'owner': 'fork'}
saved['progress']['crystal'] = 41
saved['pending'][0]['friendly_fire_costs'] = {'credits_penalty': 3}
saved['pending'][0]['income'] = {'premium': True}
saved['pending'][0]['daily_missions'] = ['daily-1']
saved['pending'][0]['personal_missions'] = {'completed': [1]}
```

Reload the store, perform a legacy save-producing operation that does not acknowledge that pending receipt, and assert all extension fields remain unchanged while the intended legacy counters or pending rows change.

- [ ] **Step 2: Run the post-battle regression and verify failure**

Run: `$env:PYTHONPATH='tests'; python -m unittest test_port_0922_postbattle.PostBattleContractTests.test_legacy_write_preserves_extended_postbattle_fields`

Expected: the save drops one or more fork-only fields.

- [ ] **Step 3: Capture raw and normalized post-battle baselines**

Import the adapter and add one modular collaborator:

```python
self._save_adapter = save_adapter.SaveDocumentAdapter()
```

Refactor the dictionary currently built inline by `_save` into `_save_value()`. After `_load_from` validates and adopts a document, call `self._save_adapter.capture(value, self._save_value())`. This keeps fork fields out of `_pending`, `_history`, and `_progress` normalization logic while retaining them for disk writes.

- [ ] **Step 4: Merge post-battle writes through the adapter**

In `_save`, build the legacy value with `_save_value()`, call `self._save_adapter.merge(value)`, preserve the existing one-per-session backup rotation, and write compact JSON. After success, call `self._save_adapter.commit(write_value, value)`.

- [ ] **Step 5: Run post-battle tests**

Run: `$env:PYTHONPATH='tests'; python -m unittest test_port_0922_postbattle test_port_0922_account_rpc`

Expected: all tests pass, including extension preservation and existing rollback/idempotency coverage.

- [ ] **Step 6: Commit post-battle compatibility**

```bash
git add src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc/postbattle_store.py tests/test_port_0922_postbattle.py
git commit -m "feat: preserve extended postbattle save data"
```

---

### Task 4: Compatibility Validation

**Files:**
- Verify: all files changed in Tasks 1-3

**Interfaces:**
- Consumes: completed garage and post-battle adapters.
- Produces: test and syntax evidence suitable for review.

- [ ] **Step 1: Run the complete focused persistence suite**

Run:

```bash
$env:PYTHONPATH='tests'; python -m unittest test_port_0922_save_adapter test_port_0922_save_slots test_port_0922_garage test_port_0922_postbattle test_port_0922_account_rpc test_port_0922_economy_regressions
```

Expected: all tests pass.

- [ ] **Step 2: Compile client sources with the available Python 2.7 interpreter**

Run the repository's documented Python 2.7 compile loop over `src/res/scripts/client/**/*.py` with `PYTHONDONTWRITEBYTECODE=1`.

Expected: `CPython 2.7 source compile passed` with no syntax error. If Python 2.7 is unavailable on this host, report that boundary and run `python -m compileall -q` only as Python 3 syntax evidence.

- [ ] **Step 3: Inspect the final diff and repository status**

Run:

```bash
git diff --check
git status --short --branch
git diff --stat v0.7.7...HEAD
```

Expected: no whitespace errors; only planned source, test, and plan files are changed or committed.

- [ ] **Step 4: Commit validation-only adjustments if required**

```bash
git add src/res/scripts/client/gui/mods/offline_lan_0922/account_rpc tests docs/superpowers/plans/2026-09-29-save-format-adapter.md
git commit -m "test: cover forward-compatible save writes"
```

Skip this commit when validation requires no file adjustment.

---

## Self-Review

- Spec coverage: schema-8 reading is covered by Task 2; unknown/new structures remain outside legacy runtime state in Tasks 2-3; selective writes and intentional deletions are covered by Task 1; both demonstrated mutable save files are covered end-to-end.
- Scope: `save.json`, launcher balance editing, and `account_state.json` have no demonstrated fork format change and already preserve loaded dictionary members or remain unchanged, so no speculative adapter is added there.
- Type consistency: both stores call the single `merge_document(raw, baseline, updated)` interface defined in Task 1.
- Placeholder scan: every implementation and validation step names exact files, APIs, commands, and expected outcomes.
