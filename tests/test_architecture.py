"""Architecture rules from CLAUDE.md that a test can check."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "smart_trainer"
UI_FREE = ["core", "ble", "export", "sync"]  # layers that must work without the UI


def _imports(path: Path) -> set[str]:
    """Modules a file imports, absolute names only.

    Args:
        path (Path): Python source file.

    Returns:
        set[str]: Imported module names.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("layer", UI_FREE)
def test_layer_never_imports_the_ui(layer):
    offenders = [
        f"{path.relative_to(PACKAGE.parent)} imports {name}"
        for path in sorted((PACKAGE / layer).rglob("*.py"))
        for name in _imports(path)
        if name == "smart_trainer.ui" or name.startswith("smart_trainer.ui.")
    ]
    assert offenders == []
