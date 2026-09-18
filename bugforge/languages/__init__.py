"""Language adapter registry.

One entry today. A second language is a new module here plus one line in
`_ADAPTERS` -- everything upstream asks for an adapter by name and never
imports a language-specific module.
"""
from __future__ import annotations

from bugforge.languages.base import LanguageAdapter, UnsupportedLanguageError
from bugforge.languages.python import PythonAdapter

_ADAPTERS: dict[str, LanguageAdapter] = {
    PythonAdapter.name: PythonAdapter(),
}

DEFAULT_LANGUAGE = PythonAdapter.name


def get_adapter(name: str = DEFAULT_LANGUAGE) -> LanguageAdapter:
    try:
        return _ADAPTERS[name.lower()]
    except KeyError:
        raise UnsupportedLanguageError(
            f"no LanguageAdapter for {name!r}; available: {', '.join(sorted(_ADAPTERS))}"
        ) from None


def available_languages() -> list[str]:
    return sorted(_ADAPTERS)


__all__ = [
    "DEFAULT_LANGUAGE",
    "LanguageAdapter",
    "PythonAdapter",
    "UnsupportedLanguageError",
    "available_languages",
    "get_adapter",
]
