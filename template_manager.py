"""Template manager — loads and renders response templates from files.

All bot responses are stored as .txt files in the templates/ directory.
This lets developers modify wording without touching code.

Templates use {variable_name} placeholders that are substituted at render time.
"""

import os
from pathlib import Path

# Directory containing template .txt files
TEMPLATE_DIR = Path(__file__).parent / "templates"

# Cache loaded templates so we don't hit disk on every render
_cache: dict[str, str] = {}


def load_template(name: str) -> str:
    """Load a template file by name (without .txt extension).

    Raises FileNotFoundError if the template doesn't exist.
    """
    if name in _cache:
        return _cache[name]

    path = TEMPLATE_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    content = path.read_text(encoding="utf-8")
    _cache[name] = content
    return content


def render(name: str, **variables: str) -> str:
    """Load a template by name and substitute {variable} placeholders.

    Missing variables are left as-is (the {var_name} text remains visible,
    which makes debugging easier than silently dropping them).
    """
    template = load_template(name)

    # Use str.format_map with a dict that returns the original placeholder
    # for missing keys, so we don't crash on missing variables.
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(_SafeDict(**variables))


def reload_all() -> None:
    """Clear the cache so templates are re-read from disk on next access.

    Useful during development when editing template files.
    """
    _cache.clear()