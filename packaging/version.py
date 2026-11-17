"""Print the project version, read from CHANGELOG.md like the package build does.

Uses the same `path` and `pattern` as `[tool.hatch.version]` in pyproject.toml, so the
.app, the DMG name and the release tag check can't drift from the package version.

    python packaging/version.py                # -> 0.1.0
    python packaging/version.py --check v0.1.0 # exit 1 unless the tag matches
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def project_version(root: Path = ROOT) -> str:
    """Return the first released version heading in the changelog.

    Args:
        root (Path): Project root containing pyproject.toml.

    Returns:
        str: The version, e.g. ``"0.1.0"``.
    """
    config = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["hatch"]["version"]
    text = (root / config["path"]).read_text()
    match = re.search(config["pattern"], text, re.MULTILINE)
    if not match:
        raise SystemExit(f"no released version heading found in {config['path']}")
    return match.group("version")


def main() -> None:
    """Print the version, or check it against a tag given with ``--check``."""
    version = project_version()
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        tag = sys.argv[2]
        if tag.removeprefix("v") != version:
            raise SystemExit(
                f"tag {tag} doesn't match CHANGELOG.md version {version}: "
                f"add a '## [{tag.removeprefix('v')}]' entry or retag as v{version}"
            )
    print(version)


if __name__ == "__main__":
    main()
