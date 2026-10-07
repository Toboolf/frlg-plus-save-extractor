"""Small helpers for reading constants and tables out of the FRLG+ C source.

Only the shapes this skill needs are supported: object-like `#define`s whose
value is an arithmetic expression over other defines, `enum { ... }` blocks,
`[KEY] = _("text")` name tables, and designated-initialiser struct arrays.
Nothing here evaluates arbitrary C.
"""
from __future__ import annotations

import os
import re

DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*(?://.*)?$")
ENUM_ENTRY_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:=\s*([^,]+?))?\s*,?\s*(?://.*)?$")
NAME_ENTRY_RE = re.compile(r"\[\s*([A-Za-z_]\w*)\s*\]\s*=\s*_\(\s*\"((?:[^\"\\]|\\.)*)\"\s*\)")


class Consts(dict):
    """`#define` / enum values, with just enough expression evaluation."""

    def load_defines(self, path):
        with open(path, encoding="utf-8") as f:
            text = _strip_block_comments(f.read())
        pending = []
        for line in text.splitlines():
            m = DEFINE_RE.match(line)
            if not m:
                continue
            name, value = m.group(1), m.group(2).strip()
            if "(" in name or name.endswith(")") or "\\" in value:
                continue
            if _is_function_macro(line, name):
                continue
            pending.append((name, value))
        self._resolve(pending)
        return self

    def load_enum(self, path, first_name):
        """Read the enum block that starts with `first_name` (values auto-increment)."""
        with open(path, encoding="utf-8") as f:
            text = _strip_block_comments(f.read())
        start = text.index(first_name)
        start = text.rindex("{", 0, start)
        end = text.index("}", start)
        counter, pending = 0, []
        for line in text[start + 1:end].splitlines():
            line = line.split("//")[0]
            if not line.strip():
                continue
            m = ENUM_ENTRY_RE.match(line)
            if not m:
                continue
            name, explicit = m.group(1), m.group(2)
            if explicit:
                pending.append((name, explicit.strip()))
                self._resolve(pending[-1:])
                counter = self[name] + 1
            else:
                self[name] = counter
                counter += 1
        return self

    def _resolve(self, pending):
        for _ in range(12):
            left = []
            for name, value in pending:
                v = self.evaluate(value)
                if v is None:
                    left.append((name, value))
                else:
                    self[name] = v
            if not left or len(left) == len(pending):
                break
            pending = left

    def evaluate(self, expr):
        """Evaluate a C integer expression over known constants, else None."""
        expr = expr.strip().rstrip(";")
        if not expr or not re.fullmatch(r"[\w\s()+\-*/|&<>^.]*", expr):
            return None
        py = re.sub(r"\b(?!0[xXbB])([A-Za-z_]\w*)\b", lambda m: str(self[m.group(1)])
                    if m.group(1) in self else m.group(1), expr)
        # anything left that starts a word and is not a hex/binary literal is an unknown name
        if re.search(r"\b(?!0[xXbB])[A-Za-z_]\w*", py):
            return None
        try:
            v = eval(py, {"__builtins__": {}}, {})  # noqa: S307 - digits and operators only
        except Exception:
            return None
        return v if isinstance(v, int) else None


def _is_function_macro(line, name):
    idx = line.index(name) + len(name)
    return idx < len(line) and line[idx] == "("


def _strip_block_comments(text):
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def read_name_table(path, table_name):
    """`[KEY] = _("NAME")` entries of one table -> {KEY: NAME}."""
    with open(path, encoding="utf-8") as f:
        text = _strip_block_comments(f.read())
    start = text.index(table_name)
    start = text.index("{", start)
    end = _matching_brace(text, start)
    return {m.group(1): _unescape(m.group(2)) for m in NAME_ENTRY_RE.finditer(text[start:end])}


def read_struct_table(path, table_name):
    """Designated-initialiser array -> {KEY: {field: raw string}} in file order."""
    with open(path, encoding="utf-8") as f:
        text = _strip_block_comments(f.read())
    start = text.index(table_name)
    start = text.index("{", start)
    end = _matching_brace(text, start)
    body = text[start + 1:end]
    out = {}
    for m in re.finditer(r"\[\s*([A-Za-z_]\w*)\s*\]\s*=\s*", body):
        rest = body[m.end():]
        if not rest.lstrip().startswith("{"):
            continue
        b0 = m.end() + rest.index("{")
        b1 = _matching_brace(body, b0)
        out[m.group(1)] = parse_fields(body[b0 + 1:b1])
    return out


def parse_fields(body):
    """`.a = 1, .b = {X, Y},` -> {'a': '1', 'b': '{X, Y}'} (brace-aware splitting)."""
    fields, depth, start = {}, 0, 0
    parts = []
    for i, ch in enumerate(body):
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    for part in parts:
        m = re.match(r"\s*\.(\w+)\s*=\s*(.*)", part, re.S)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def read_array_entries(path, table_name):
    """Array of brace-delimited rows -> list of the raw text inside each row."""
    with open(path, encoding="utf-8") as f:
        text = _strip_block_comments(f.read())
    start = text.index(table_name)
    start = text.index("{", start)
    end = _matching_brace(text, start)
    body, out, i = text[start + 1:end], [], 0
    while True:
        j = body.find("{", i)
        if j < 0:
            break
        k = _matching_brace(body, j)
        out.append(body[j + 1:k])
        i = k + 1
    return out


def read_flat_list(path, table_name):
    """`const T name[] = { A, B, C };` -> ['A', 'B', 'C'] (no nested braces)."""
    with open(path, encoding="utf-8") as f:
        text = _strip_block_comments(f.read())
    start = text.index(table_name)
    start = text.index("{", start)
    end = _matching_brace(text, start)
    items = []
    for part in text[start + 1:end].split(","):
        part = re.sub(r"//.*", "", part).strip()
        if part:
            items.append(part)
    return items


def _matching_brace(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces")


def _unescape(s):
    return s.replace("\\\"", "\"").replace("\\\\", "\\")


def repo_path(repo, *parts):
    p = os.path.join(repo, *parts)
    if not os.path.exists(p):
        raise SystemExit(f"not found in the FRLG+ repo: {os.path.join(*parts)}")
    return p
