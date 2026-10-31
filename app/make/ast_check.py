"""Reject Blender scripts that import or call outside the script contract.

This is defence in depth. The Modal worker (no secrets, no network, one job
per container) is the boundary. A syntax error is a rejection, not a crash.
"""
from __future__ import annotations

import ast

ALLOWED_IMPORTS = frozenset({"bpy", "bmesh", "math", "mathutils", "json"})
BANNED_CALLS = frozenset({
    "exec", "eval", "open", "compile", "__import__", "breakpoint", "input",
    "globals", "locals", "vars",
})
_DYNAMIC_ATTR = frozenset({"getattr", "setattr", "delattr", "hasattr"})


class StaticCheck:
    def __init__(self, errors: list[str]):
        self.errors = errors

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def text(self) -> str:
        return "\n".join(self.errors)


def check(source: str) -> StaticCheck:
    """Return every contract violation in ``source``."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return StaticCheck([f"line {exc.lineno}: syntax error: {exc.msg}"])
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _import(errors, node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                errors.append(f"line {node.lineno}: relative import is not allowed")
            else:
                _import(errors, node.lineno, node.module or "")
        elif isinstance(node, ast.Call):
            _call(errors, node)
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"line {node.lineno}: dunder attribute {node.attr} is not allowed")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == "__import__":
            errors.append(f"line {node.lineno}: __import__ is not allowed")
    return StaticCheck(errors)


def _import(errors: list[str], lineno: int, module: str) -> None:
    root = module.split(".", 1)[0]
    if root not in ALLOWED_IMPORTS:
        errors.append(f"line {lineno}: import of {root!r} is not allowed")


def _call(errors: list[str], node: ast.Call) -> None:
    name = _called(node.func)
    chain = _chain(node.func)
    if name in BANNED_CALLS or (chain and chain[-1] in BANNED_CALLS):
        label = name or chain[-1]
        errors.append(f"line {node.lineno}: call to {label} is not allowed")
        return
    if name in _DYNAMIC_ATTR or (chain and chain[-1] in _DYNAMIC_ATTR):
        literal = _name_literal(node)
        if literal is None or "__" in literal:
            errors.append(f"line {node.lineno}: dynamic attribute access is not allowed")
    if chain[:3] == ["bpy", "ops", "wm"] and chain[-1].startswith("save"):
        errors.append(f"line {node.lineno}: bpy.ops.wm.{chain[-1]} is not allowed")


def _called(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    return None


def _chain(func: ast.AST) -> list[str]:
    parts: list[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    parts.reverse()
    return parts


def _name_literal(node: ast.Call) -> str | None:
    if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
        return None
    value = node.args[1].value
    if not isinstance(value, str):
        return None
    return value
