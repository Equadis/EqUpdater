"""One-off helper used while changing the interface text: drop translations whose English text no
longer exists in the code, and add new ones given in a JSON file.

    python tools/qa/i18n_sync.py add.json [--apply]

``add.json``: {"English text": {"de": "...", "es": "...", "pt_br": "...", "ru": "...", "zh_cn": "..."}}.
Without --apply it only reports. Entries are removed or appended as text lines, so the files keep
their layout and everything else in them is untouched.
"""

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def main():
    add = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 and sys.argv[1].endswith(".json") else {}
    apply = "--apply" in sys.argv
    from tests import test_i18n as T
    live = T.messages() | set(add)
    loc = os.path.join(ROOT, "equpdater", "locales")
    for fn in sorted(os.listdir(loc)):
        if fn.endswith(".py") and fn != "__init__.py":
            code = fn[:-3]
            path = os.path.join(loc, fn)
            text = open(path, encoding="utf-8").read()
            lines = text.split("\n")
            tree = ast.parse(text)
            dict_node = next(n.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict))
            drop = set()
            have = set()
            for k, v in zip(dict_node.keys, dict_node.values):
                have.add(k.value)
                if k.value not in live:
                    drop.update(range(k.lineno - 1, v.end_lineno))
            keep = [ln for i, ln in enumerate(lines) if i not in drop]
            end = max(i for i, ln in enumerate(keep) if ln.strip() == "}")
            new = []
            for english, tr in add.items():
                if english not in have and code in tr:
                    new.append(f"    {json.dumps(english, ensure_ascii=False)}: {json.dumps(tr[code], ensure_ascii=False)},")
            keep[end:end] = new
            print(f"{code}: drop {len(drop)} lines, add {len(new)} entries")
            if apply:
                open(path, "w", encoding="utf-8", newline="").write("\n".join(keep))


if __name__ == "__main__":
    main()
