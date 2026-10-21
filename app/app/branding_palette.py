"""Factory branding colours of the interface (the built-in light/dark palette).

A colour equal to its factory value counts as "not customised": the interface then uses its own light and dark
shades instead of a fixed colour. The previous factory colours (before the 0.49.18x restyle) count the same way,
so installations that never changed them follow the new palette.
"""

DEFAULT_PRIMARY = "#1769d6"
DEFAULT_SIDEBAR = "#10192b"
DEFAULT_HIGHLIGHT = "#f79009"

DEFAULTS = {
    "primary": DEFAULT_PRIMARY,
    "sidebar": DEFAULT_SIDEBAR,
    "highlight": DEFAULT_HIGHLIGHT,
}

LEGACY_DEFAULTS = {
    "primary": "#1f5f8b",
    "sidebar": "#111827",
    "highlight": "#f59e0b",
}


def is_factory(key: str, value: str | None) -> bool:
    """True when the colour was never customised (current or previous factory value, or empty)."""
    value = (value or "").strip().lower()
    return not value or value in (DEFAULTS[key], LEGACY_DEFAULTS[key])


def effective(key: str, value: str | None) -> str:
    """The colour to show for a stored value: a previous factory colour reads as the current one."""
    if is_factory(key, value):
        return DEFAULTS[key]
    return (value or "").strip().lower()
