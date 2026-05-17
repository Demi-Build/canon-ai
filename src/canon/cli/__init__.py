"""Canon CLI package.

Imports are deferred to avoid the RuntimeWarning that arises when the package
is both imported (by this __init__) and then executed as __main__ via
`python -m canon.cli.main`.  Callers that need the Typer app object directly
should import from ``canon.cli.main`` instead.
"""

from __future__ import annotations


def __getattr__(name: str):  # type: ignore[return]
    if name in ("app", "main"):
        from canon.cli import main as _main_module

        return getattr(_main_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app", "main"]
