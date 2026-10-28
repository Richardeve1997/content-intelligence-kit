#!/usr/bin/env python3
"""check_scenes.py: every .scene (and every caption .cap) must be a DIRECT child of #root.

One unclosed <div> nests every later scene inside an earlier one, so everything after it renders blank,
and `hyperframes lint` still reports no errors. Run this next to lint.

    python check_scenes.py <hf dir>/index.html     (exit 1 on a nested layer or unbalanced divs)
"""
import argparse
import sys
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad, self.n = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        d = dict(attrs)
        cls = (d.get("class") or "").split()
        parent = self.stack[-1] if self.stack else None
        if "scene" in cls or "cap" in cls:
            self.n += 1
            if not parent or parent[1] != "root":
                self.bad.append(f"line {self.getpos()[0]}: #{d.get('id', '?')} ({' '.join(cls)}) is inside "
                                f"<{parent[0] if parent else '-'} id={parent[1] if parent else '-'}>, not #root")
        self.stack.append((tag, d.get("id")))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            t, _ = self.stack.pop()
            if t == tag:
                break


def main():
    ap = argparse.ArgumentParser(description="Every scene and caption must sit directly in #root.")
    ap.add_argument("index")
    a = ap.parse_args()
    src = open(a.index, encoding="utf-8").read()
    body = src.split("<body", 1)[1] if "<body" in src else src
    p = Parser()
    p.feed("<body" + body)
    opens, closes = body.count("<div"), body.count("</div>")
    if opens != closes:
        p.bad.append(f"unbalanced divs: {opens} <div> against {closes} </div>")
    for problem in p.bad:
        print("FAIL", problem)
    print(f"{p.n} scene and caption layers checked, {len(p.bad)} problem(s)")
    sys.exit(1 if p.bad else 0)


if __name__ == "__main__":
    main()
