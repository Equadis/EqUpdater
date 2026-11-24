"""Move named methods of EqUpdaterApp into a mixin class in a core module.

    python tools/lift_methods.py equpdater/core/mods_ops.py ModsOps HEADER method [method ...]

Method source is copied verbatim (same indentation, comments above included),
removed from the Tk class, and the class gains the mixin as a base, so
``self._method()`` calls and ``EqUpdaterApp._method`` lookups keep working. A
Qt host gets the same behaviour by inheriting the mixin and implementing the
hook methods the Tk class implements. Edits to the moved text (dialog hooks
and the like) are made afterwards and reviewed in the diff.
"""
import ast
import sys

NL = chr(10)
dest, cls_name, hdr = sys.argv[1:4]
names = sys.argv[4:]
path = "equpdater/app.py"
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.splitlines(keepends=True)
app_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EqUpdaterApp")
picked = []
for node in app_cls.body:
    if isinstance(node, ast.FunctionDef) and node.name in names:
        start = node.lineno - 1
        if node.decorator_list:
            start = node.decorator_list[0].lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        picked.append((start, node.end_lineno, node.name))
missing = set(names) - {p[2] for p in picked}
if missing:
    sys.exit(f"not methods of EqUpdaterApp: {sorted(missing)}")
body = "".join("".join(lines[s:e]) + NL for s, e, _ in picked)
head = open(hdr, encoding="utf-8").read()
open(dest, "w", encoding="utf-8").write(
    head + NL + NL + "class " + cls_name + ":" + NL
    + '    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every' + NL
    + '    widget and dialog goes through a hook method the host implements."""' + NL + NL
    + body.rstrip() + NL)
remove = set()
for s, e, _ in picked:
    remove.update(range(s, e))
out = "".join(l for i, l in enumerate(lines) if i not in remove)
mod = dest.replace("\\", "/")[:-3].split("equpdater/")[-1].replace("/", ".")
out = out.replace("class EqUpdaterApp(tk.Tk):", "class EqUpdaterApp(" + cls_name + ", tk.Tk):", 1) \
    if "class EqUpdaterApp(tk.Tk):" in out else out.replace(
        "class EqUpdaterApp(", "class EqUpdaterApp(" + cls_name + ", ", 1)
marker = "# Secure networking lives in core/net.py"
out = out.replace(marker, "from ." + mod + " import " + cls_name + "  # noqa: E402" + NL + NL + marker, 1)
open(path, "w", encoding="utf-8").write(out)
print("moved", len(picked), "methods into", dest)
