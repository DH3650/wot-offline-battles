"""Preserve save data owned by newer builds around legacy updates.

Stores keep their existing serializers and validators.  This adapter compares
the normalized document loaded by that serializer with its next output, then
applies only those changes to the original document.  Fields the serializer
does not know therefore never enter runtime state and never disappear on save.
"""

import copy


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


_IDENTITY_KEYS = ('receipt_id', 'arena_unique_id', 'id', 'key')
_MISSING = object()


def _identity(value):
    if not isinstance(value, dict):
        return None
    for name in _IDENTITY_KEYS:
        if name in value and value[name] is not None:
            return (name, str(value[name]))
    return None


def _identity_map(values):
    result = {}
    for value in values:
        identity = _identity(value)
        if identity is None or identity in result:
            return None
        result[identity] = value
    return result


def _merge_list(raw, baseline, updated):
    raw_rows = _identity_map(raw) if isinstance(raw, list) else None
    baseline_rows = _identity_map(baseline)
    updated_rows = _identity_map(updated)
    if (raw_rows is None or baseline_rows is None or updated_rows is None or
            (baseline and not baseline_rows) or (updated and not updated_rows)):
        return copy.deepcopy(updated)

    result = []
    for value in updated:
        identity = _identity(value)
        if identity in baseline_rows and identity in raw_rows:
            result.append(_merge(
                raw_rows[identity], baseline_rows[identity], value))
        else:
            result.append(copy.deepcopy(value))

    # A row absent from the normalized baseline was unknown to the legacy
    # reader.  Keep it after the rows that reader still owns.
    known = set(baseline_rows)
    emitted = set(updated_rows)
    for value in raw:
        identity = _identity(value)
        if identity not in known and identity not in emitted:
            result.append(copy.deepcopy(value))
    return result


def _newer_schema(raw, updated):
    if (isinstance(raw, bool) or isinstance(updated, bool) or
            not isinstance(raw, integer_types) or
            not isinstance(updated, integer_types)):
        return copy.deepcopy(updated)
    return max(int(raw), int(updated))


def _merge(raw, baseline, updated, field_name=None):
    if baseline == updated:
        return copy.deepcopy(raw if raw is not _MISSING else updated)

    if isinstance(baseline, dict) and isinstance(updated, dict):
        result = copy.deepcopy(raw) if isinstance(raw, dict) else {}
        for name in set(baseline) | set(updated):
            if name not in updated:
                result.pop(name, None)
            elif name not in baseline:
                result[name] = copy.deepcopy(updated[name])
            else:
                raw_value = (raw.get(name, _MISSING)
                             if isinstance(raw, dict) else _MISSING)
                result[name] = _merge(
                    raw_value, baseline[name], updated[name], name)
        return result

    if isinstance(baseline, list) and isinstance(updated, list):
        return _merge_list(raw, baseline, updated)

    if field_name == 'schema' and raw is not _MISSING:
        return _newer_schema(raw, updated)
    return copy.deepcopy(updated)


def merge_document(raw, baseline, updated):
    """Apply legacy changes to ``raw`` without mutating any input."""
    if not isinstance(updated, dict):
        raise TypeError('updated save document must be an object')
    if not isinstance(raw, dict) or not isinstance(baseline, dict):
        return copy.deepcopy(updated)
    return _merge(raw, baseline, updated)


class SaveDocumentAdapter(object):
    """Own compatibility state so stores need only three narrow hooks."""

    def __init__(self):
        self._raw = None
        self._baseline = None

    def capture(self, raw, baseline):
        self._raw = copy.deepcopy(raw) if isinstance(raw, dict) else None
        self._baseline = (copy.deepcopy(baseline)
                          if isinstance(baseline, dict) else None)

    def merge(self, updated):
        return merge_document(self._raw, self._baseline, updated)

    def commit(self, written, updated):
        self.capture(written, updated)
