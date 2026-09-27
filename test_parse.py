#!/usr/bin/env python3
"""Testet die Label-Auswertung: python3 test_parse.py"""
import logging
import sys

sys.path.insert(0, "/app")
from portforward import parse_label

logging.basicConfig(level="INFO", format="    %(levelname)s %(message)s", stream=sys.stdout)

CASES = [
    ("25565/tcp", [(25565, "TCP")]),
    ("25565", [(25565, "TCP")]),
    ("2456-2458/udp", [(2456, "UDP"), (2457, "UDP"), (2458, "UDP")]),
    ("27015/both,27020/udp", [(27015, "TCP"), (27015, "UDP"), (27020, "UDP")]),
    (" 7777/UDP , 7777/udp ", [(7777, "UDP")]),
    ("1000-1099/tcp", [(p, "TCP") for p in range(1000, 1100)]),  # genau 100 Ports: erlaubt
    ("1000-1100/tcp", []),          # 101 Ports: ignoriert
    ("abc", []),
    ("25565/sctp", []),
    ("0/tcp", []),
    ("70000/udp", []),
    ("3000-2000", []),
    ("80/tcp/udp", []),
    ("", []),
    ("25565/tcp,foo,34197/udp", [(25565, "TCP"), (34197, "UDP")]),
]

failed = 0
for value, expected in CASES:
    print("Label %r" % value)
    got = parse_label(value, "test")
    ok = got == expected
    failed += not ok
    shown = got if len(got) <= 6 else "%d Eintraege (%s .. %s)" % (len(got), got[0], got[-1])
    print("  %s -> %s" % ("OK  " if ok else "FAIL", shown))
print("\n%d/%d Faelle ok" % (len(CASES) - failed, len(CASES)))
sys.exit(1 if failed else 0)
