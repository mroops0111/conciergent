import ast
import importlib
import pathlib

import conciergent


_INIT = pathlib.Path(conciergent.__file__)


def _type_checking_imports() -> dict[str, str]:
    """The names the ``if typing.TYPE_CHECKING:`` block imports, mapped to the module each comes from."""
    tree = ast.parse(_INIT.read_text())
    (block,) = [node for node in tree.body if isinstance(node, ast.If) and 'TYPE_CHECKING' in ast.unparse(node.test)]
    return {
        alias.name: statement.module
        for statement in block.body
        if isinstance(statement, ast.ImportFrom) and statement.module is not None
        for alias in statement.names
    }


def test_the_lazy_exports_agree_with_the_type_checking_imports_and_all():
    assert _type_checking_imports() == conciergent._MODULES
    assert sorted(conciergent._MODULES) == sorted(conciergent.__all__)


def test_every_lazy_export_resolves_to_its_module_attribute():
    for name, module in conciergent._MODULES.items():
        assert getattr(conciergent, name) is getattr(importlib.import_module(module), name)


def test_dir_lists_the_lazy_exports_before_first_use():
    assert set(conciergent.__all__) <= set(dir(conciergent))
