"""Mechanically lift top-level names from equpdater/app.py into a core module.

    python tools/lift.py equpdater/core/tweaks.py NAME [NAME ...] --header FILE

Copies each named top-level assignment/function/class verbatim (source
segments from ``ast``), in the order they appear in app.py, removes them from
app.py and leaves a re-export import so no call site or test changes. No
logic is edited by this tool; run pyflakes and the suite afterwards.
"""
import ast
import sys

dest, rest = sys.argv[1], sys.argv[2:]
hdr = rest[rest.index("--header") + 1]
names = [n for n in rest[:rest.index("--header")]]
src_path = "equpdater/app.py"
src = open(src_path, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.splitlines(keepends=True)
want = set(names)
picked = []
for node in tree.body:
    tn = None
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        tn = [node.name]
    elif isinstance(node, ast.Assign):
        tn = [t.id for t in node.targets if isinstance(t, ast.Name)]
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        tn = [node.target.id]
    if tn and all(n in want for n in tn):
        start = node.lineno - 1
        if getattr(node, "decorator_list", None):
            start = node.decorator_list[0].lineno - 1
        # include comment lines directly above
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        picked.append((start, node.end_lineno, tn))
found = {n for _, _, tn in picked for n in tn}
missing = want - found
if missing:
    sys.exit(f"not found at top level: {sorted(missing)}")
body = "".join("".join(lines[s:e]) + "\n\n" for s, e, _ in picked)
open(dest, "w", encoding="utf-8").write(open(hdr, encoding="utf-8").read() + "\n" + body.rstrip() + "\n")
remove = set()
for s, e, _ in picked:
    remove.update(range(s, e))
kept = [l for i, l in enumerate(lines) if i not in remove]
open(src_path, "w", encoding="utf-8").write("".join(kept))
print("lifted", sorted(found))
