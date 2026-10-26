import ast
import re
from pathlib import Path

import version

# X.Y.Z, optionally -beta.N or -rc.N (the tag formats the release workflow accepts)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-(?:beta|rc)\.\d+)?$")


def test_version_is_a_release_version_string():
    assert isinstance(version.__version__, str)
    assert VERSION_RE.fullmatch(version.__version__), version.__version__


def test_version_module_is_trivially_parseable():
    # The PyInstaller spec, Inno Setup build and release workflow read this
    # file, so __version__ must be a plain string literal and the module must
    # not import anything.
    tree = ast.parse(Path(version.__file__).read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "__version__" for t in node.targets)
    ]
    assert len(assignments) == 1
    assert isinstance(assignments[0].value, ast.Constant)
    assert assignments[0].value.value == version.__version__
