"""``canon.keyfile`` — the ONE source of provider keys: a ``.env`` file.

The owner's rule: users bring their own keys, and those keys live only in a
``.env`` file. A provider key exported by the shell is never read. Before this
module canon loaded ``--env-file`` with *setdefault* semantics, so a stale
``export ANTHROPIC_API_KEY=…`` in ``~/.zshrc`` silently won over a fresh
``.env`` and billed the wrong account (PASS4 findings, "Your shell is
shadowing your fresh keys").

What :func:`apply` does, at the start of every canon entry point:

1. every provider key name (:func:`key_names` — the rows in
   :mod:`canon.providers`, so it is data) is REMOVED from ``os.environ``.
   Whatever the shell exported under those names is gone, and its name is
   remembered so ``canon keys status`` can say it was ignored;
2. the file's provider keys are set, overriding. Its other lines (non-key
   settings such as ``CANON_PLAT_SEED``) keep the old setdefault behaviour:
   the shell may still override a setting, never a key.

Which file (:func:`resolve_path`): an explicit ``--env-file``, else
``CANON_ENV_FILE`` (how cradle names its key file), else ``./.env``.

First use: ``canon keys setup`` asks for each key and creates the file;
``canon keys set`` rewrites one key's line. Both preserve every other line
and comment, and both create the file owner-only (0600).

Secrets discipline: a VALUE goes from the file into ``os.environ`` and from a
hidden prompt into the file. Nothing here prints, logs, or returns one.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

#: The variable a host (cradle) sets to name its key file.
ENV_FILE_VAR = "CANON_ENV_FILE"

#: The file canon reads when nothing names one: ``.env`` in the working dir.
DEFAULT_FILE = ".env"

#: What a freshly created file starts with. It has to make sense to someone
#: who opens it in an editor.
HEADER = (
    "# Provider keys for canon and cradle. One KEY=VALUE per line; # starts a comment.",
    "# This file is the only place canon reads a provider key from: shell exports are ignored.",
)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Names a shell exported under a provider key name, seen by :func:`apply`
#: and dropped. Names only — the value is never kept.
_IGNORED: set[str] = set()
#: What :func:`apply` itself put into the environment, so a second
#: :func:`apply` (the root callback, then a verb's ``--env-file``) can tell
#: its own earlier value from a shell export.
_LOADED: dict[str, str] = {}


def key_names() -> list[str]:
    """Every provider key name canon reads: each row's ``env_var``, its
    ``aliases`` and its ``also_reads``, in table order, de-duplicated."""
    from canon.providers import PROVIDER_ROWS

    out: list[str] = []
    for r in PROVIDER_ROWS:
        for name in [r["env_var"], *r.get("aliases", []), *r.get("also_reads", [])]:
            if name not in out:
                out.append(name)
    return out


def resolve_path(explicit: Path | None = None, environ: MutableMapping[str, str] | None = None) -> Path:
    """The key file: ``explicit``, else ``$CANON_ENV_FILE``, else ``./.env``."""
    env = os.environ if environ is None else environ
    if explicit is not None:
        return Path(explicit).expanduser()
    named = env.get(ENV_FILE_VAR, "").strip()
    if named:
        return Path(named).expanduser()
    return Path.cwd() / DEFAULT_FILE


def parse_line(line: str) -> tuple[str, str] | None:
    """One ``KEY=VALUE`` line → ``(key, value)``, or ``None`` for a blank
    line, a comment, or a line with no ``=``. An ``export`` prefix is allowed
    and surrounding quotes are stripped — the same dialect cradle's
    ``keys.rs`` reads, so both agree on which line is a key's."""
    line = line.strip()
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None
    return key, value.strip().strip('"').strip("'")


def read_pairs(path: Path) -> dict[str, str]:
    """``{name: value}`` for every non-empty pair in ``path``; a later
    duplicate wins. A missing file is a file with nothing in it."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed and parsed[1]:
            out[parsed[0]] = parsed[1]
    return out


def apply(path: Path | None = None, environ: MutableMapping[str, str] | None = None) -> dict[str, Any]:
    """Make the key file the only source of provider keys in ``environ``
    (default ``os.environ``). Returns ``{env_file, exists, loaded,
    ignored_shell}`` — names only."""
    env = os.environ if environ is None else environ
    file = resolve_path(path, env)
    names = key_names()
    for name in names:
        if name in env:
            if env[name] and _LOADED.get(name) != env[name]:
                _IGNORED.add(name)
            del env[name]
    for name in names:
        _LOADED.pop(name, None)
    loaded: list[str] = []
    for key, value in read_pairs(file).items():
        if key in names:
            env[key] = value
            _LOADED[key] = value
            loaded.append(key)
        else:
            env.setdefault(key, value)
    return {
        "env_file": str(file),
        "exists": file.is_file(),
        "loaded": loaded,
        "ignored_shell": ignored_shell_names(),
    }


def ignored_shell_names() -> list[str]:
    """Provider key names the shell exported and :func:`apply` dropped."""
    return [n for n in key_names() if n in _IGNORED]


def _reset_for_tests() -> None:
    _IGNORED.clear()
    _LOADED.clear()


# ---------------------------------------------------------------------------
# Writing — first use and changing one key
# ---------------------------------------------------------------------------


def _check_name(var: str) -> str:
    var = var.strip()
    if not _NAME.match(var):
        raise ValueError(f"{var or 'an empty name'} is not a variable name (letters, digits, _ only)")
    return var


def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write(path: Path, lines: list[str]) -> None:
    """Write ``lines`` owner-only from the start: a temp file created 0600 in
    the same directory, then an atomic replace — no moment where the file
    holding key values is world-readable or half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".keys-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def create(path: Path) -> bool:
    """First use: create the file with its header and no keys. ``False``
    (nothing touched) when it already exists."""
    if Path(path).is_file():
        return False
    _write(path, list(HEADER))
    return True


def set_key(path: Path, var: str, value: str) -> None:
    """Set ``var`` to ``value``: that ONE line is rewritten in place (later
    duplicates dropped), every other line and comment is kept verbatim, a new
    name is appended, and a missing file is created with the header."""
    var = _check_name(var)
    value = value.strip()
    if not value:
        raise ValueError(f"empty value for {var} — use `canon keys remove {var}` to clear a key")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{var} cannot hold a line break")
    text = _read_text(path)
    lines: list[str] = []
    replaced = False
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed and parsed[0] == var:
            if not replaced:
                lines.append(f"{var}={value}")
                replaced = True
            continue
        lines.append(line)
    if not replaced:
        if not text:
            lines.extend(HEADER)
        lines.append(f"{var}={value}")
    _write(path, lines)


def remove_key(path: Path, var: str) -> bool:
    """Delete ``var``'s line(s), keeping everything else. ``False`` (and no
    rewrite) when there was nothing to delete."""
    var = var.strip()
    text = _read_text(path)
    if not text:
        return False
    kept = [line for line in text.splitlines() if (parse_line(line) or ("",))[0] != var]
    if len(kept) == len(text.splitlines()):
        return False
    _write(path, kept)
    return True


def status(path: Path | None = None, environ: MutableMapping[str, str] | None = None) -> dict[str, Any]:
    """``{env_file, exists, providers: [{id, env_var, set, set_via}],
    ignored_shell}`` read from the FILE — names and presence only."""
    from canon.providers import PROVIDER_ROWS

    env = os.environ if environ is None else environ
    file = resolve_path(path, env)
    pairs = read_pairs(file)
    rows = []
    for r in PROVIDER_ROWS:
        names = [r["env_var"], *r.get("aliases", [])]
        set_via = next((n for n in names if n in pairs), None)
        rows.append({"id": r["id"], "env_var": r["env_var"], "set": set_via is not None, "set_via": set_via})
    return {
        "env_file": str(file),
        "exists": file.is_file(),
        "providers": rows,
        "ignored_shell": ignored_shell_names(),
    }


__all__ = [
    "DEFAULT_FILE",
    "ENV_FILE_VAR",
    "HEADER",
    "apply",
    "create",
    "ignored_shell_names",
    "key_names",
    "parse_line",
    "read_pairs",
    "remove_key",
    "resolve_path",
    "set_key",
    "status",
]
