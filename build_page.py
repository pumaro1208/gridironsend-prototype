#!/usr/bin/env python3
"""Rebuild index.html from players_clean.csv + proto_template.html.
Dedupes school+name rows, embeds the dataset, and updates the counts shown
in the page copy."""
import csv, json, re

import os
QUESTIONNAIRES = {}
if os.path.exists("questionnaires.csv"):
    for r in csv.DictReader(open("questionnaires.csv", encoding="utf-8")):
        if r.get("questionnaire_url", "").strip():
            QUESTIONNAIRES[r["school"]] = r["questionnaire_url"].strip()

rows = list(csv.DictReader(open("players_clean.csv", encoding="utf-8")))
seen = {}
for r in rows:
    key = (r["school"], " ".join(r["name"].split()))
    score = sum(1 for f in ("height_in","weight_lb","class_norm","hometown") if r[f])
    if key not in seen or score > seen[key][0]:
        seen[key] = (score, r)
players = [dict(
    s=r["school"], d=r["division"], c=r["conference"], n=" ".join(r["name"].split()),
    p=r["position_group"], cl=r["class_norm"], rs=r["redshirt"]=="True",
    h=int(r["height_in"]) if r["height_in"] else None,
    w=int(r["weight_lb"]) if r["weight_lb"] else None,
    ht=r["hometown"], st=r["home_state"]) for _, r in seen.values()]

n_players, n_schools = len(players), len({p["s"] for p in players})
divs = sorted({p["d"] for p in players})
data = json.dumps({"players": players, "questionnaires": QUESTIONNAIRES}, separators=(",",":"))

html = open("proto_template.html", encoding="utf-8").read()
html = html.replace("/*__DATA__*/", data)
html = html.replace("512 real players across 6 programs",
                    f"{n_players:,} real players across {n_schools} programs — {', '.join(divs)}")
html = html.replace("512 players / 6 schools",
                    f"{n_players:,} players / {n_schools} schools ({'+'.join(divs)})")
open("index.html", "w", encoding="utf-8").write(html)
print(f"built index.html: {n_players:,} players, {n_schools} schools, divisions {divs}")
