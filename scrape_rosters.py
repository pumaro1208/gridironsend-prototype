#!/usr/bin/env python3
"""
GridironSend — D1 roster scraper (production starter)
=====================================================
Pulls football rosters for every school in schools.csv and writes a single
players_clean.csv in the same schema the prototype uses.

WHY THIS EXISTS: pulling ~262 D1 programs can't be done page-by-page in a chat.
This runs on a real machine, hits every school's public roster page directly,
and finishes the whole set in a few minutes.

Most D1 athletics sites run on the SIDEARM Sports platform, so one parser covers
the majority; a generic <table> fallback handles the rest. Some sites will still
need small per-site tweaks — that long tail is the real work, as the build plan notes.

USAGE:
    pip install requests beautifulsoup4 lxml
    python scrape_rosters.py schools.csv players_clean.csv

schools.csv columns:  school,division,conference,roster_url
(A seed list is provided in d1_schools_seed.csv — complete it to the full ~262.)

BE POLITE: this respects a delay between requests. Check each site's robots.txt
and terms of use before running at scale.
"""
import csv, re, sys, time, json
import requests
from bs4 import BeautifulSoup

DELAY_SECONDS = 1.5           # be a good citizen
TIMEOUT = 20
HEADERS = {"User-Agent": "GridironSend-RosterBot/1.0 (+contact@yourdomain.com)"}

POS_MAP = {
    "QB":"QB","RB":"RB","FB":"RB","HB":"RB",
    "WR":"WR","WR/RS":"WR","WR/RB":"WR","RB/WR":"WR",
    "TE":"TE",
    "OL":"OL","OT":"OL","OG":"OL","C":"OL","G":"OL","T":"OL","IOL":"OL",
    "DL":"DL","DE":"DL","DT":"DL","NT":"DL","NG":"DL","ER":"DL","JACK":"DL","EDGE":"DL","BUCK":"DL",
    "LB":"LB","OLB":"LB","ILB":"LB","MLB":"LB","WLB":"LB","SLB":"LB","ROVER":"LB","MONEY BACKER":"LB","$B":"LB","NICKEL":"DB",
    "DB":"DB","CB":"DB","S":"DB","FS":"DB","SS":"DB","NB":"DB","SAF":"DB","NI":"DB","NIC":"DB","N/S":"DB","DB/WR":"DB","STAR":"DB","HUSKY":"DB",
    "K":"ST","P":"ST","LS":"ST","K/P":"ST","PK":"ST","P/K":"ST","PK/P":"ST","P/PK":"ST","KP":"ST","SN":"ST","SNP":"ST","DS":"ST",
    # long-form names used by WMT/Nuxt sites when no abbreviation is set
    "QUARTERBACK":"QB","RUNNING BACK":"RB","FULLBACK":"RB","WIDE RECEIVER":"WR",
    "TIGHT END":"TE","OFFENSIVE LINE":"OL","OFFENSIVE LINEMAN":"OL",
    "OFFENSIVE TACKLE":"OL","OFFENSIVE GUARD":"OL","CENTER":"OL",
    "DEFENSIVE LINE":"DL","DEFENSIVE LINEMAN":"DL","DEFENSIVE END":"DL",
    "DEFENSIVE TACKLE":"DL","NOSE TACKLE":"DL","LINEBACKER":"LB",
    "DEFENSIVE BACK":"DB","CORNERBACK":"DB","SAFETY":"DB",
    "KICKER":"ST","PUNTER":"ST","PLACE KICKER":"ST","PLACEKICKER":"ST",
    "LONG SNAPPER":"ST","LONGSNAPPER":"ST","DEEP SNAPPER":"ST",
}
def norm_pos(p):
    key = re.sub(r"[.\s]+", " ", (p or "").strip().upper()).strip()
    if key in POS_MAP: return POS_MAP[key]
    # long labels ("Inside Linebacker") or card text with glued-on height/weight
    # ("Tight End TE 6'4\" 245 lbs"): find the longest known token in the string
    for k in sorted(POS_MAP, key=len, reverse=True):
        if re.search(r"(?<![A-Z$/])" + re.escape(k) + r"(?![A-Z])", key):
            return POS_MAP[k]
    return "OTHER"

def height_in(h):
    m = re.search(r"(\d)\D+(\d{1,2})", h or "")
    return int(m.group(1))*12 + int(m.group(2)) if m else None

CLASS_MAP = {
    "FRESHMAN":"FR","FR":"FR","F":"FR","FY":"FR","FIRST YEAR":"FR","1":"FR","1ST":"FR",
    "SOPHOMORE":"SO","SO":"SO","2":"SO","2ND":"SO",
    "JUNIOR":"JR","JR":"JR","J":"JR","3":"JR","3RD":"JR",
    "SENIOR":"SR","SR":"SR","4":"SR","4TH":"SR",
    "GRADUATE":"GR","GR":"GR","GRAD":"GR","GS":"GR","PG":"GR",
    "5":"GR","5TH":"GR","6":"GR","6TH":"GR","FIFTH YEAR":"GR","SIXTH YEAR":"GR",
    "POST-BACC":"GR","POST-GRAD":"GR","POSTGRADUATE":"GR",
}
def norm_class(c):
    up = re.sub(r"\s+", " ", re.sub(r"[. ]", " ", c or "")).strip().upper()
    rs = bool(re.search(r"\bREDSHIRT\b", up) or re.match(r"^RS?[-\s]", up)
              or re.match(r"^R(FR|SO|JR|SR|FY|F)\b", up))
    if up in ("RS", "R"): return "", True
    base = re.sub(r"\bREDSHIRT\b", " ", up)
    base = re.sub(r"^RS[-\s]+", " ", base)          # RS-Sophomore, RS Senior
    base = re.sub(r"^R[-\s]+", " ", base)           # R-Jr
    base = re.sub(r"^R(?=(FR|SO|JR|SR|FY|F)\b)", "", base.strip())  # RFr, RSo, RF, RFy
    base = re.sub(r"\s+", " ", base).strip(" -+")
    if base in CLASS_MAP: return CLASS_MAP[base], rs
    # compound labels: "5th-year Senior", "Sixth-Year Redshirt Senior", "5 (Graduate)"
    for k, v in [("GRADUATE","GR"),("POST","GR"),("SIXTH","GR"),("FIFTH","GR"),
                 ("6TH","GR"),("5TH","GR"),("FRESHMAN","FR"),("FIRST","FR"),
                 ("SOPHOMORE","SO"),("JUNIOR","JR"),("SENIOR","SR")]:
        if k in base: return v, rs
    m = re.match(r"^(\d)\b", base)
    if m: return {"1":"FR","2":"SO","3":"JR","4":"SR"}.get(m.group(1), "GR"), rs
    return (base[:2] if base else ""), rs

def clean_wt(w):
    m = re.search(r"\d{2,3}", w or "")
    return int(m.group(0)) if m else None

# ---------- SIDEARM parser (covers most D1 sites) ----------
def parse_sidearm(soup):
    players = []
    cards = soup.select("li.sidearm-roster-player, .sidearm-roster-player")
    for c in cards:
        def txt(sel):
            el = c.select_one(sel); return el.get_text(" ", strip=True) if el else ""
        name = txt(".sidearm-roster-player-name h3, .sidearm-roster-player-name a, .sidearm-roster-player-name")
        name = re.sub(r"^\s*#?\d{1,2}\s+", "", name)  # some sites glue the jersey number onto the name
        pos  = txt(".sidearm-roster-player-position .text-bold, .sidearm-roster-player-position span, .sidearm-roster-player-position")
        height = txt(".sidearm-roster-player-height")
        weight = txt(".sidearm-roster-player-weight")
        cls    = txt(".sidearm-roster-player-academic-year")
        home   = txt(".sidearm-roster-player-hometown")
        if not cls:
            # some sites (e.g. Texas State) put the class in the custom1 slot
            c1 = txt(".sidearm-roster-player-custom1")
            if re.match(r"(?i)^(r-|rs\b|redshirt|fr|so|jr|sr|gr|fresh|soph|jun|sen|grad)", c1.strip()):
                cls = c1
        a = c.select_one(".sidearm-roster-player-name a[href]") or c.select_one("a[href*='/roster/']")
        url = a.get("href") if a else None
        if name:
            players.append((name, pos, cls, height, weight, home, url))
    return players

# ---------- WMT / Nuxt parser (Auburn, LSU, Texas, Penn State, Clemson, ...) ----------
# These sites ship the roster as devalue-serialized JSON in <script id="__NUXT_DATA__">:
# a flat array where every dict/list value is an index into the same array.
def _nuxt_json(soup):
    tag = soup.find("script", id="__NUXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        data = json.loads(tag.string)
    except ValueError:
        return None
    return data if isinstance(data, list) else None

def _nuxt_deref(data, i):
    seen = set()
    while isinstance(i, int) and 0 <= i < len(data) and i not in seen:
        seen.add(i)
        v = data[i]
        if (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)
                and v[0] in ("ShallowReactive", "Reactive", "Ref", "ShallowRef")):
            i = v[1]
            continue
        return v
    return None

def parse_nuxt(soup):
    data = _nuxt_json(soup)
    if data is None:
        return []

    def deref(i):
        return _nuxt_deref(data, i)

    def field(d, key):
        return deref(d[key]) if isinstance(d, dict) and key in d else None

    best = []
    for node in data:
        if not (isinstance(node, dict) and "players" in node):
            continue
        lst = deref(node["players"])
        if not isinstance(lst, list):
            continue
        rows = []
        for pi in lst:
            entry = deref(pi)
            if not isinstance(entry, dict):
                continue
            url = None
            pl = field(entry, "player")
            if isinstance(pl, dict):
                # WMT shape: snake_case fields, player nested under the roster entry
                name = field(pl, "full_name") or " ".join(
                    x for x in (field(pl, "first_name"), field(pl, "last_name")) if x)
                pos_d = field(entry, "player_position")
                pos = (field(pos_d, "abbreviation") or field(pos_d, "name") or "") if isinstance(pos_d, dict) else ""
                cls_d = field(entry, "class_level")
                cls = (field(cls_d, "abbreviation") or field(cls_d, "name") or "") if isinstance(cls_d, dict) else ""
                hf, hi = field(entry, "height_feet"), field(entry, "height_inches")
                wt = field(entry, "weight")
                home = field(pl, "hometown") or ""
            elif "firstName" in entry:
                # Sidearm-Nuxt shape: camelCase fields directly on the entry
                name = " ".join(x for x in (field(entry, "firstName"), field(entry, "lastName")) if x)
                pos = field(entry, "positionShort") or field(entry, "positionLong") or ""
                cls = field(entry, "academicYearShort") or field(entry, "academicYear") or ""
                hf, hi = field(entry, "heightFeet"), field(entry, "heightInches")
                wt = field(entry, "weight")
                home = field(entry, "hometown") or ""
                url = field(entry, "call_to_action")  # player bio page, for backfill
            else:
                continue
            ht = f"{hf}-{hi}" if isinstance(hf, int) and isinstance(hi, int) else ""
            if name:
                rows.append((name, pos, cls, ht, str(wt or ""), home, url))
        if len(rows) > len(best):
            best = rows
    return best

# ---------- generic table fallback ----------
def parse_table(soup):
    players, best = [], None
    for tbl in soup.find_all("table"):
        heads = [th.get_text(strip=True).lower() for th in tbl.select("thead th, tr th")]
        if any("pos" in h for h in heads) and any("ht" in h or "height" in h for h in heads):
            best = (tbl, heads); break
    if not best: return players
    tbl, heads = best
    def idx(*keys):
        for i,h in enumerate(heads):
            if any(k in h for k in keys): return i
        return None
    ci = {k: idx(*v) for k,v in {
        "name":["name","player"], "pos":["pos"], "cls":["cl","yr","year","class"],
        "ht":["ht","height"], "wt":["wt","weight"], "home":["hometown","home"]}.items()}
    for tr in tbl.select("tbody tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td","th"])]
        if not tds or ci["name"] is None: continue
        g = lambda k: tds[ci[k]] if ci[k] is not None and ci[k] < len(tds) else ""
        if g("name"):
            players.append((g("name"), g("pos"), g("cls"), g("ht"), g("wt"), g("home")))
    return players

# Some sites omit height/weight from the roster list entirely (Nuxt sites like
# Oregon/UNC, and many D2 Sidearm sites); it only exists on each player's bio page.
def fetch_player_htwt(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException:
        return "", None
    soup = BeautifulSoup(r.text, "lxml")
    data = _nuxt_json(soup)
    if data is None:
        # classic Sidearm bio page: labeled vitals spans, else first ht/wt pattern in the bio header
        el = soup.select_one(".sidearm-roster-player-height")
        ht = el.get_text(strip=True) if el else ""
        el = soup.select_one(".sidearm-roster-player-weight")
        wtxt = el.get_text(strip=True) if el else ""
        text = soup.get_text(" ", strip=True)[:5000]
        if not ht:
            m = re.search(r"\b(\d)'\s?(\d{1,2})\"", text)
            if m: ht = f"{m.group(1)}-{m.group(2)}"
        if not wtxt:
            m = re.search(r"\b(\d{2,3})\s*lbs", text)
            if m: wtxt = m.group(1)
        return ht, clean_wt(wtxt)
    m = re.search(r"/(\d+)/?$", url)
    pid = int(m.group(1)) if m else None
    cands = []
    for v in data:
        if not (isinstance(v, dict) and "heightFeet" in v):
            continue
        hf = _nuxt_deref(data, v.get("heightFeet"))
        hi = _nuxt_deref(data, v.get("heightInches"))
        wt = _nuxt_deref(data, v.get("weight"))
        # 0-0 / 0 lbs are placeholder records, not real measurements
        ht = f"{hf}-{hi}" if isinstance(hf, int) and hf > 0 and isinstance(hi, int) else ""
        wt = wt if isinstance(wt, int) and wt > 0 else None
        if not ht and wt is None:
            continue
        rid = _nuxt_deref(data, v.get("rosterPlayerId")) if "rosterPlayerId" in v else None
        if pid is not None and rid == pid:
            return ht, wt
        # season-history entries carry a year; prefer the most recent
        yr = _nuxt_deref(data, v.get("year")) if "year" in v else None
        cands.append((yr if isinstance(yr, int) else 0, ht, wt))
    if not cands:
        return "", None
    _, ht, wt = max(cands, key=lambda c: c[0])
    return ht, wt

def scrape_school(row, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(row["roster_url"], headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == retries: raise
            time.sleep(3 * (attempt + 1))
    soup = BeautifulSoup(r.text, "lxml")
    players = parse_sidearm(soup) or parse_nuxt(soup) or parse_table(soup)
    site_root = re.match(r"https?://[^/]+", r.url).group(0)
    out = []
    for rec in players:
        name, pos, cls, ht, wt, home = rec[:6]
        if re.match(r"(?i)\s*retired", name):  # retired-jersey placeholders (e.g. Montana)
            continue
        bio_url = rec[6] if len(rec) > 6 else None
        if bio_url and (height_in(ht) is None or clean_wt(wt) is None):
            time.sleep(0.5)
            if bio_url.startswith("/"): bio_url = site_root + bio_url
            ht2, wt2 = fetch_player_htwt(bio_url)
            if height_in(ht) is None: ht = ht2 or ht
            if clean_wt(wt) is None: wt = str(wt2) if wt2 else wt
        cn, rs = norm_class(cls)
        out.append(dict(
            school=row["school"], division=row["division"], conference=row["conference"],
            name=name, position_raw=pos, position_group=norm_pos(pos),
            class_raw=cls, class_norm=cn, redshirt=rs,
            height_in=height_in(ht) or "", height_disp=ht,
            weight_lb=clean_wt(wt) or "",
            hometown=home, home_state=home.split(",")[-1].strip() if "," in home else "",
        ))
    return out

def main(schools_csv, out_csv):
    schools = list(csv.DictReader(open(schools_csv, encoding="utf-8")))
    cols = ["school","division","conference","name","position_raw","position_group",
            "class_raw","class_norm","redshirt","height_in","height_disp","weight_lb",
            "hometown","home_state"]
    total, ok, fail = 0, 0, 0
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for i, row in enumerate(schools, 1):
            try:
                rows = scrape_school(row)
                if not rows:
                    print(f"[{i}/{len(schools)}] {row['school']:<28} 0 players — CHECK SITE (needs per-site tweak)", flush=True)
                    fail += 1
                else:
                    for r in rows: w.writerow(r)
                    total += len(rows); ok += 1
                    print(f"[{i}/{len(schools)}] {row['school']:<28} {len(rows)} players", flush=True)
            except Exception as e:
                fail += 1
                print(f"[{i}/{len(schools)}] {row['school']:<28} ERROR: {e}", flush=True)
            time.sleep(DELAY_SECONDS)
    print(f"\nDONE. {ok} schools OK, {fail} to review, {total} players -> {out_csv}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python scrape_rosters.py schools.csv players_clean.csv"); sys.exit(1)
    main(sys.argv[1], sys.argv[2])
