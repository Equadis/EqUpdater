"""Write a deterministic news fixture: long posts, many posts, links.

    python tools/bench/make_news_fixture.py OUT.json [--posts 60] [--long 1]
"""
import argparse, json, random

def para(rng, n):
    words = ("arena gear honor blood token conquest realm hardcore patch vendor "
             "raid dungeon fix bug stat talent spell mount companion").split()
    return " ".join(rng.choice(words) for _ in range(n)).capitalize() + "."

def post(rng, i, long_):
    parts, links, pos = [], [], 0
    n_par = rng.randint(30, 60) if long_ and i % 4 == 0 else rng.randint(3, 10)
    for p in range(n_par):
        line = "• " + para(rng, rng.randint(12, 40))
        if p % 5 == 0:
            label = "forum thread"
            line += " See the " + label
            start = pos + len(line) - len(label)
            links.append([start, start + len(label), f"https://octowow.st/forum/viewtopic.php?t={i}"])
        parts.append(line)
        pos += len(line) + 2
    return "\n\n".join(parts), links

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--posts", type=int, default=60)
    ap.add_argument("--long", type=int, default=1); ap.add_argument("--latency", type=int, default=0)
    a = ap.parse_args(); rng = random.Random(7)
    sections = {}
    for key, base in (("announcements", 5000), ("changelog", 9000)):
        rows = []
        for i in range(a.posts):
            body, links = post(rng, i, bool(a.long))
            day = 28 - (i * 3) % 27
            rows.append({"id": str(base + a.posts - i), "title": f"{key.title()} {a.posts - i}: " + para(rng, 4),
                         "author": "Kestrel", "date": f"2026-{11 - (i * 3) // 27:02d}-{day:02d}T10:00:00+00:00",
                         "url": f"https://octowow.st/forum/viewtopic.php?t={base + a.posts - i}",
                         "body": body, "links": links})
        sections[key] = rows
    json.dump({"latency_ms": a.latency, "page_size": 10, "sections": sections}, open(a.out, "w"))
main()
