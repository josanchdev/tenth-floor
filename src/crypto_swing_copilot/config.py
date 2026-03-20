"""
Central path resolver for project root and config directory.

Solves the fragile ``Path(__file__).resolve().parents[N]`` pattern
that breaks when the package is installed as a wheel into
site-packages.

Resolution order:
  1. ``CRYPTO_SWING_COPILOT_ROOT`` env var (explicit override)
  2. Walk up from CWD looking for ``pyproject.toml`` (dev mode)
  3. Walk up from this file looking for ``pyproject.toml`` (fallback)

Raises ``RuntimeError`` if no project root can be resolved.

Usage::

    from crypto_swing_copilot.config import CONFIG_DIR, DATA_DIR, PROJECT_ROOT
"""

from __future__ import annotations

import os
from pathlib import Path

_SENTINEL = "pyproject.toml"


def _resolve_project_root() -> Path:
    """Find the project root by locating ``pyproject.toml``.

    Resolution order:

    1. ``CRYPTO_SWING_COPILOT_ROOT`` environment variable — if set,
       used verbatim with no further checks.
    2. Walk up from the current working directory looking for
       ``pyproject.toml``.  This is the fast path for development
       (``pip install -e .`` or running from the source tree).
    3. Walk up from *this file's* location looking for
       ``pyproject.toml``.  Fallback for cases where CWD differs
       from the project root.

    .. note::

        Wheel installations (``pip install .`` without ``-e``) place
        the package inside ``site-packages``, where the file-walk
        fallback will **not** reliably locate the project root.
        In that scenario you **must** set the
        ``CRYPTO_SWING_COPILOT_ROOT`` environment variable to the
        absolute path of the project checkout.

    Raises
    ------
    RuntimeError
        If none of the three strategies can locate the project root.
    """
    # 1. Explicit env var
    env_root = os.environ.get("CRYPTO_SWING_COPILOT_ROOT")
    if env_root:
        return Path(env_root).resolve()

    # 2. Walk up from CWD
    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        if (parent / _SENTINEL).is_file():
            return parent

    # 3. Walk up from this file
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        if (parent / _SENTINEL).is_file():
            return parent

    raise RuntimeError(
        "Cannot locate project root (looked for pyproject.toml). "
        "Set CRYPTO_SWING_COPILOT_ROOT environment variable to the "
        "project checkout directory."
    )


PROJECT_ROOT: Path = _resolve_project_root()
CONFIG_DIR: Path = PROJECT_ROOT / "config"
DATA_DIR: Path = PROJECT_ROOT / "data"
