"""Reading an add-on's `config_schema`: what a saved setting may hold.

The Configure forms and the option plumbing both need the same answers —
is this field a number, a whole number, what range — so they live here
rather than in each.

Numeric fields come as ``"type": "int"`` or ``"type": "number"`` (both are
in use across the published add-ons), with optional ``min`` / ``max`` /
``step``. A ``number`` is a whole number when its step and bounds are.
"""

NUMERIC_TYPES = ('int', 'number')


def fields_by_key(manifest: dict | None) -> dict:
    """The schema's fields, keyed by setting name."""
    schema = (manifest or {}).get('config_schema') or {}
    return {f['key']: f for f in (schema.get('fields') or [])
            if isinstance(f, dict) and f.get('key')}


def is_numeric(field: dict) -> bool:
    return (field.get('type') or '').lower() in NUMERIC_TYPES


def is_integral(field: dict) -> bool:
    """Whether the field holds whole numbers only."""
    if (field.get('type') or '').lower() == 'int':
        return True
    for key, fallback in (('step', 1), ('min', 0), ('max', 0)):
        value = field.get(key, fallback)
        try:
            if not float(value).is_integer():
                return False
        except (TypeError, ValueError):
            return False
    return True


def coerce_number(field: dict, value):
    """`value` as the field's kind of number, clamped to its range.

    None when it is not a number at all (an empty or garbled entry), so the
    add-on's own default applies instead.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    low, high = field.get('min'), field.get('max')
    try:
        if low is not None:
            number = max(float(low), number)
        if high is not None:
            number = min(float(high), number)
    except (TypeError, ValueError):
        pass
    return int(round(number)) if is_integral(field) else number


def normalize_options(manifest: dict | None, options: dict | None) -> dict:
    """Saved settings made safe to send: numeric fields clamped to their
    schema range (a value saved before the form enforced it, or typed into
    a config file, would otherwise reach the add-on as-is)."""
    fields = fields_by_key(manifest)
    normalized = dict(options or {})
    for key in list(normalized):
        field = fields.get(key)
        if field is None or not is_numeric(field):
            continue
        number = coerce_number(field, normalized[key])
        if number is None:
            del normalized[key]
        else:
            normalized[key] = number
    return normalized
