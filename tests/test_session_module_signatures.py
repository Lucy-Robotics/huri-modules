"""Session modules should expose only the arguments they actually accept."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MODULES = [
    ("huri-module-stt/huri_stt/module.py", "STT"),
    ("huri-module-emo/huri_emo/module.py", "EMO"),
    ("huri-module-rag/huri_rag/rag.py", "RAG"),
]


def get_constructor(source: str, class_name: str):
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == "__init__":
                    return member

    raise AssertionError(f"Constructor not found for {class_name}")


@pytest.mark.parametrize("module_path,class_name", MODULES)
def test_session_module_constructor_does_not_accept_arbitrary_kwargs(
    module_path, class_name
):
    source_path = ROOT / "packages" / module_path
    source = source_path.read_text()
    constructor = get_constructor(source, class_name)

    assert constructor.args.kwarg is None, (
        f"{class_name}.__init__ should not expose **kwargs"
    )
