"""Map every rap artist to the region / city scene they came up in, plus a fame proxy.

Sources
  1. Wikidata (primary). Bulk SPARQL pulls every hip-hop related person or group (occupation
     rapper / hip-hop musician / hip-hop producer / beatmaker, or a hip-hop genre), then their
     labels, aliases, places (P19 birth, P740 formation, P551 residence), genres, members and
     sitelinks (fame proxy). Places are then resolved by walking P131 up to a US state or other
     first-level subdivision, with coordinates (P625) and country (P17).
     Items that carry a Genius artist ID (P2373) matching a Genius name are pulled too, even when
     they are not tagged hip-hop (e.g. R&B singers with rap songs).
  2. English Wikipedia infobox `origin` field for matched items (the "where they came up" field:
     Nicki Minaj -> New York City, Pusha T -> Virginia Beach, J. Cole -> Fayetteville), resolved to
     Wikidata places through page props. Wikidata rarely records these moves (P551 is sparse).
  3. MusicBrainz (secondary): artists matched to Wikidata but without any usable place (>= 10 rap
     songs, looked up by the item's MusicBrainz ID P434), and unmatched artists with >= 50 rap songs
     (artist search, top hit with score >= 95 and a normalized-name match). begin-area (else area)
     -> Wikidata place via the area's url-rels.

Name matching tiers: genius_id (P2373 slug) > exact_label > exact_alias > normalized (casefold,
accents and punctuation stripped; $->s as a last resort). Ties go to the rapper-occupation item with
the most sitelinks. A label hit is dropped when that item's own Genius slug is a different name, and
when the Genius catalogue ends before the person turned 12. Generic names (dictionary words or <= 4
characters) match through aliases / normalization only for items with >= 10 sitelinks.

Origin priority (first that applies):
  P740 formation place (groups)
  > Wikipedia infobox origin
  > scene-specific genre claim ("West Coast hip-hop", "Chicago drill", "grime", ...) -- picks the
    residence/birthplace that lies in that region, or sets the region from the genre alone
  > P551 residence (US/UK/Canada) -- only when the birthplace is missing, abroad, in the same
    region, or the residence started in childhood; a cross-region residence with no dates is
    treated as a later move (e.g. Chicago -> Los Angeles) and loses to the birthplace
  > P19 birthplace
  > group members' birthplaces (majority)
  > country only (P495 country of origin / P27 citizenship, non-US only)
  > MusicBrainz begin-area / area

Every raw API response is cached under .cache/regions/{wd,wp,mb}/ so reruns are offline and fast.
Run with --skip-mb to leave out the (slow, 1 request/s) MusicBrainz step, or --mb-cached-only to use
whatever MusicBrainz responses are already cached without making new requests.

Outputs (in derived/):
  artist_regions.parquet         one row per rap artist
  artist_regions_top300.csv      the 300 artists with most rap songs, for spot checks
"""
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import duckdb
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "regions"
(CACHE / "wd").mkdir(parents=True, exist_ok=True)
(CACHE / "mb").mkdir(parents=True, exist_ok=True)
(CACHE / "wp").mkdir(parents=True, exist_ok=True)

UA = "RapSlangResearch/0.1 (+https://github.com/toddsherman/RapAnalysis)"
WDQS = "https://query.wikidata.org/sparql"
MB = "https://musicbrainz.org/ws/2"
WP_API = "https://en.wikipedia.org/w/api.php"
MB_MIN_SONGS = 50        # MusicBrainz fallback only for artists this prolific
MB_CAP = 1500            # ...and at most this many of them
EXTRA_SCENE_MIN = 20     # optional scenes are kept only with this many matched artists
MB_CACHED_ONLY = "--mb-cached-only" in sys.argv   # use MusicBrainz responses already cached, no new requests

session = requests.Session()
session.headers["User-Agent"] = UA
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------- HTTP + cache

def _cache_get(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:          # truncated by an interrupted run
            path.unlink()
    return None


def sparql(query, tag):
    """Run a SPARQL query (cached). Returns rows as dicts of plain strings (QIDs for entities)."""
    key = hashlib.sha1(query.encode()).hexdigest()[:20]
    path = CACHE / "wd" / f"{tag}_{key}.json"
    data = _cache_get(path)
    if data is None:
        for attempt in range(6):
            try:
                r = session.post(WDQS, data={"query": query}, timeout=90,
                                 headers={"Accept": "application/sparql-results+json"})
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 30))
                    log(f"429 from WDQS, sleeping {wait}s"); time.sleep(wait); continue
                if r.status_code in (500, 502, 503, 504):
                    raise requests.HTTPError(f"{r.status_code}")
                r.raise_for_status()
                data = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                if attempt == 5:
                    raise
                wait = 5 * 2 ** attempt
                log(f"WDQS error ({e}) on {tag}, retry in {wait}s"); time.sleep(wait)
        path.write_text(json.dumps(data))
        time.sleep(0.3)
    out = []
    for b in data["results"]["bindings"]:
        row = {}
        for k, v in b.items():
            val = v["value"]
            if v["type"] == "uri" and val.startswith("http://www.wikidata.org/"):
                val = val.rsplit("/", 1)[-1]
            row[k] = val
            if "xml:lang" in v:
                row[k + "_lang"] = v["xml:lang"]
        out.append(row)
    return out


def sparql_batched(template, qids, tag, size=400):
    """Fill {values} in template with QID batches; splits a batch in half if it keeps failing."""
    qids = sorted(set(qids), key=lambda q: int(q[1:]))
    rows = []

    def run(chunk):
        q = template.replace("{values}", " ".join("wd:" + x for x in chunk))
        try:
            rows.extend(sparql(q, tag))
        except Exception as e:
            if len(chunk) <= 25:
                log(f"giving up on {tag} chunk of {len(chunk)}: {e}")
                return
            h = len(chunk) // 2
            run(chunk[:h]); run(chunk[h:])

    for i in range(0, len(qids), size):
        run(qids[i:i + size])
    return rows


_last_mb = [0.0]


def mb_get(url):
    """MusicBrainz GET, cached, rate-limited to 1 request / 1.1 s."""
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    path = CACHE / "mb" / f"{key}.json"
    data = _cache_get(path)
    if data is not None or MB_CACHED_ONLY:
        return data
    for attempt in range(5):
        wait = 1.1 - (time.time() - _last_mb[0])
        if wait > 0:
            time.sleep(wait)
        _last_mb[0] = time.time()
        try:
            r = session.get(url, timeout=30, headers={"Accept": "application/json"})
            if r.status_code == 404:
                data = {"_status": 404}
                break
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            if attempt == 4:
                log(f"MB failed {url}: {e}")
                return None
            time.sleep(3 * 2 ** attempt)
    path.write_text(json.dumps(data))
    return data


def wp_get(params, tag):
    """English Wikipedia API GET (cached)."""
    params = {**params, "format": "json", "formatversion": "2", "maxlag": "5"}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
    path = CACHE / "wp" / f"{tag}_{key}.json"
    data = _cache_get(path)
    if data is not None:
        return data
    for attempt in range(6):
        try:
            r = session.get(WP_API, params=params, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504) or "maxlag" in r.text[:200]:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            if attempt == 5:
                raise
            time.sleep(5 * 2 ** attempt)
    path.write_text(json.dumps(data))
    time.sleep(0.2)
    return data


def parse_origin(wikitext):
    """Candidate place titles from the infobox `origin` field, most specific first."""
    m = re.search(r"^\s*\|\s*origin\s*=(.*?)(?=\n\s*\||\n\s*\}\})", wikitext, re.M | re.S | re.I)
    if not m:
        return []
    v = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    v = re.sub(r"<ref[^>]*/>", "", v)
    v = re.sub(r"<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    links = [l.strip() for l in re.findall(r"\[\[([^\]|#]+)", v) if l.strip()]
    if links:
        return links
    v = re.sub(r"\{\{\s*(?:nowrap|nobr|no wrap|small|nowrap begin)\s*\|([^{}]*)\}\}", r"\1", v, flags=re.I)
    prev = None
    while prev != v:
        prev, v = v, re.sub(r"\{\{[^{}]*\}\}", "", v)
    v = re.split(r"<br\s*/?>|\n", re.sub(r"<(?!br)[^>]+>", "", v))[0]
    parts = [x.strip(" .") for x in v.split(",") if x.strip(" .")]
    if not parts:
        return []
    return ([", ".join(parts[:2])] if len(parts) > 1 else []) + [parts[0]]


def wikipedia_origins(qids):
    """qid -> list of place QIDs named in the enwiki infobox `origin` field."""
    title = {}
    for r in sparql_batched("""SELECT ?item ?title WHERE { VALUES ?item { {values} }
            ?a schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?title . }""",
                            qids, "enwiki"):
        title[r["item"]] = r["title"]
    by_title = {t: q for q, t in title.items()}
    titles = sorted(by_title)
    cand = {}
    for i in range(0, len(titles), 50):
        d = wp_get({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                    "rvsection": "0", "titles": "|".join(titles[i:i + 50])}, "wikitext")
        fix = {n["to"]: n["from"] for n in d["query"].get("normalized", [])}
        for pg in d["query"].get("pages", []):
            q = by_title.get(pg["title"]) or by_title.get(fix.get(pg["title"], ""))
            if q and pg.get("revisions"):
                c = parse_origin(pg["revisions"][0]["slots"]["main"]["content"])
                if c:
                    cand[q] = c
    # resolve candidate titles to Wikidata items
    ctitles = sorted({t for c in cand.values() for t in c})
    t2q = {}
    for i in range(0, len(ctitles), 50):
        chunk = ctitles[i:i + 50]
        d = wp_get({"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": "1",
                    "titles": "|".join(chunk)}, "pageprops")
        qd = d["query"]
        final = {pg["title"]: pg.get("pageprops", {}).get("wikibase_item") for pg in qd.get("pages", [])}
        norm = {n["from"]: n["to"] for n in qd.get("normalized", [])}
        redir = {n["from"]: n["to"] for n in qd.get("redirects", [])}
        for t in chunk:
            u = norm.get(t, t)
            u = redir.get(u, u)
            if final.get(u):
                t2q[t] = final[u]
    out = {q: [t2q[t] for t in c if t in t2q] for q, c in cand.items()}
    log(f"wikipedia: {len(title)} enwiki articles, {len(cand)} with an infobox origin, "
        f"{sum(1 for v in out.values() if v)} resolved to items")
    return out


# ----------------------------------------------------------------------------- Genius side

def load_genius():
    con = duckdb.connect()
    return con.sql("""
      select a.artist_id, a.artist, count(*)::int rap_songs,
             min(s.year)::int first_year, max(s.year)::int last_year
      from 'derived/songs.parquet' s join 'derived/artists.parquet' a using (artist_id)
      where s.tag = 'rap'
      group by 1, 2
    """).df()


# ----------------------------------------------------------------------------- Wikidata pull

VALUES_PROPS = "wdt:P19 wdt:P740 wdt:P551 wdt:P136 wdt:P31 wdt:P106 wdt:P527 wdt:P495 wdt:P27"


def discover_hiphop_qids():
    """Every person/group that is hip-hop by occupation or by genre."""
    occ = sparql("""SELECT DISTINCT ?item WHERE {
        { ?occ wdt:P279* wd:Q2252262 } UNION { ?occ wdt:P279* wd:Q100493654 }
        UNION { VALUES ?occ { wd:Q19826506 wd:Q4087517 } }
        ?item wdt:P106 ?occ . ?item wdt:P31 wd:Q5 . }""", "occ")
    ens = {r["c"] for r in sparql("SELECT ?c WHERE { ?c wdt:P279* wd:Q2088357 . }", "ensembles")}
    humans = sparql("""SELECT DISTINCT ?item WHERE {
        ?g wdt:P279* wd:Q11401 . ?item wdt:P136 ?g ; wdt:P31 wd:Q5 . }""", "genre_humans")
    groups = sparql("""SELECT DISTINCT ?item WHERE {
        VALUES ?c { %s } ?g wdt:P279* wd:Q11401 . ?item wdt:P136 ?g ; wdt:P31 ?c . }"""
                    % " ".join("wd:" + c for c in sorted(ens)), "genre_groups")
    qids = {r["item"] for r in occ + humans + groups if r["item"].startswith("Q")}
    log(f"wikidata items: {len(occ)} by occupation, {len(humans)} humans + {len(groups)} groups by genre "
        f"-> {len(qids)} unique")
    return qids


def fetch_items(qids, prefix=""):
    """Labels, aliases, places, genres, members, sitelinks, dates for a set of items."""
    items = {q: {"labels": set(), "aliases": set(), "P19": [], "P740": [], "P551": [], "P136": [],
                 "P31": [], "P106": [], "P527": [], "P495": [], "P27": [], "sitelinks": 0,
                 "birth_year": None, "inception": None, "res_start": {}, "wp_origin": []} for q in qids}
    if not qids:
        return items
    for r in sparql_batched(f"""SELECT ?item ?p ?v WHERE {{ VALUES ?item {{ {{values}} }}
            VALUES ?p {{ {VALUES_PROPS} }} ?item ?p ?v . FILTER(isIRI(?v)) }}""", qids, prefix + "props"):
        items[r["item"]][r["p"].rsplit("/", 1)[-1]].append(r["v"])
    for r in sparql_batched("""SELECT ?item ?kind ?text WHERE { VALUES ?item { {values} }
            { ?item rdfs:label ?text BIND("labels" AS ?kind) } UNION
            { ?item skos:altLabel ?text BIND("aliases" AS ?kind) }
            FILTER(LANG(?text) = "en" || LANG(?text) = "mul") }""", qids, prefix + "labels"):
        items[r["item"]][r["kind"]].add(r["text"])
    for r in sparql_batched("""SELECT ?item ?sl ?dob ?inc WHERE { VALUES ?item { {values} }
            OPTIONAL { ?item wikibase:sitelinks ?sl } OPTIONAL { ?item wdt:P569 ?dob }
            OPTIONAL { ?item wdt:P571 ?inc } }""", qids, prefix + "misc"):
        it = items[r["item"]]
        it["sitelinks"] = max(it["sitelinks"], int(r.get("sl", 0)))
        for k, f in (("dob", "birth_year"), ("inc", "inception")):
            y = _year(r.get(k))
            if y and (it[f] is None or y < it[f]):
                it[f] = y
    for r in sparql_batched("""SELECT ?item ?v ?start ?end WHERE { VALUES ?item { {values} }
            ?item p:P551 ?st . ?st ps:P551 ?v . OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end } }""",
                            qids, prefix + "residence"):
        y = _year(r.get("start"))
        if y:
            items[r["item"]]["res_start"][r["v"]] = y
    for it in items.values():
        for k in ("P19", "P740", "P551", "P136", "P31", "P106", "P527", "P495", "P27"):
            it[k] = list(dict.fromkeys(it[k]))
    return items


def _year(s):
    if not s:
        return None
    m = re.match(r"^(-?\d{1,4})-", s)
    return int(m.group(1)) if m else None


def pull_labels(qids, tag):
    out = {}
    for r in sparql_batched("""SELECT ?item ?text WHERE { VALUES ?item { {values} }
            ?item rdfs:label ?text . FILTER(LANG(?text) = "en" || LANG(?text) = "mul") }""", qids, tag):
        if r["item"] not in out or r.get("text_lang") == "en":
            out[r["item"]] = r["text"]
    return out


# ----------------------------------------------------------------------------- places

def pull_places(seed, places=None, tag="places"):
    """BFS over P131 from the seed places; returns qid -> {label, coord, country, parents, iso}."""
    places = {} if places is None else places
    frontier = set(seed)
    depth = 0
    while frontier and depth < 14:
        todo = sorted(q for q in frontier if q not in places and re.fullmatch(r"Q\d+", q))
        if not todo:
            break
        for q in todo:
            places[q] = {"label": None, "coord": None, "country": [], "parents": [], "iso": []}
        for r in sparql_batched("""SELECT ?place ?p ?v WHERE { VALUES ?place { {values} }
                VALUES ?p { wdt:P17 wdt:P131 wdt:P300 wdt:P625 } ?place ?p ?v . }""", todo, f"{tag}{depth}", 500):
            pl = places[r["place"]]
            p = r["p"].rsplit("/", 1)[-1]
            if p == "P625":
                m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", r["v"])
                if m and pl["coord"] is None and "<" not in r["v"]:   # skip non-Earth globes
                    pl["coord"] = (float(m.group(2)), float(m.group(1)))
            elif p == "P300":
                pl["iso"].append(r["v"])
            elif p == "P17":
                pl["country"].append(r["v"]) if r["v"] not in pl["country"] else None
            elif p == "P131":
                pl["parents"].append(r["v"]) if r["v"] not in pl["parents"] else None
        for q, lab in pull_labels(todo, f"{tag}labels{depth}").items():
            places[q]["label"] = lab
        frontier = {x for q in todo for x in places[q]["parents"] + places[q]["country"]}
        depth += 1
    log(f"places fetched: {len(places)} (depth {depth})")
    return places


US, UK, CANADA = "Q30", "Q145", "Q16"
REGION_BY_STATE = {}
for reg, states in {
    "West": "CA NV OR WA AZ CO UT NM HI AK ID MT WY",
    "Midwest": "IL MI OH MO MN WI IN KS NE IA ND SD",
    "South": "TX GA TN LA FL AL MS NC SC AR KY OK WV",
    "East": "NY NJ PA MA CT RI NH VT ME MD DC DE VA",
}.items():
    for s in states.split():
        REGION_BY_STATE[s] = reg


class PlaceResolver:
    def __init__(self, places):
        self.p = places
        self.countries = {c for pl in places.values() for c in pl["country"]}
        self.memo = {}

    def resolve(self, q, _seen=None):
        """-> dict(label, state, country_qid, coord, level) ; state = US postal code or first-level name."""
        if q in self.memo:
            return self.memo[q]
        _seen = (_seen or set()) | {q}
        pl = self.p.get(q)
        if pl is None:
            return None
        own_iso = next((i for i in pl["iso"] if re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{1,3}", i)), None)
        is_country = q in self.countries and (not pl["country"] or q in pl["country"])
        best = None
        for par in pl["parents"]:
            # never climb into the place's own country: the first-level unit is the last step below it
            if par in _seen or par in pl["country"] or (par in self.countries and par not in self.p):
                continue
            r = self.resolve(par, _seen)
            if r and (best is None or (r["state"] and not best["state"])
                      or (r["country"] == US and r["state"] and best["country"] != US)):
                best = r
        state = best["state"] if best and best["state"] else None
        if state is None and own_iso and not is_country:
            state = own_iso[3:] if own_iso.startswith("US-") else pl["label"]
        country = pl["country"][0] if pl["country"] else (best["country"] if best else None)
        if US in pl["country"]:
            country = US
        if is_country:
            country = q
        # A country's or first-level unit's P625 is a centroid (California's lands near Fresno), so it
        # is neither reported nor used for scenes. DC is the one first-level unit that is a city.
        first_level = bool(own_iso) and not (best and best["state"]) and own_iso != "US-DC"
        coord = None if (is_country or first_level) else pl["coord"]
        if coord is None and not (is_country or first_level) and best and best.get("coord_ok"):
            coord = best["coord"]
        coord_ok = not own_iso and not is_country   # our coord is a city-ish point (usable by children)
        out = {"label": pl["label"], "state": state, "country": country, "coord": coord,
               "coord_ok": coord_ok and coord is not None, "is_country": is_country}
        # US state postal codes only for real states / DC
        if country == US and state and len(state) != 2:
            out["state"] = None
        self.memo[q] = out
        return out


# ----------------------------------------------------------------------------- scenes

# (scene, lat, lon, radius_km, country)
SCENE_CENTERS = [
    ("Bay Area", 37.80, -122.27, 55, US), ("Bay Area", 37.34, -121.89, 30, US),
    ("Bay Area", 38.10, -122.26, 30, US),
    ("Los Angeles", 34.05, -118.24, 60, US), ("Los Angeles", 34.00, -117.30, 45, US),   # + Inland Empire
    ("San Diego", 32.72, -117.16, 50, US),
    ("Sacramento", 38.58, -121.49, 45, US),
    ("Seattle", 47.61, -122.33, 60, US),
    ("Phoenix", 33.45, -112.07, 60, US),
    ("Denver", 39.74, -104.99, 60, US),
    ("New York City", 40.73, -73.95, 40, US), ("New York City", 40.76, -73.30, 40, US),  # + Long Island
    ("New Jersey", 40.73, -74.17, 45, US),
    ("Philadelphia", 39.95, -75.17, 50, US),
    ("Boston", 42.36, -71.06, 50, US),
    ("DMV", 38.90, -77.04, 45, US), ("DMV", 39.29, -76.61, 40, US),
    ("Virginia", 36.87, -76.29, 45, US), ("Virginia", 37.54, -77.44, 40, US),
    ("Atlanta", 33.75, -84.39, 60, US),
    ("Houston", 29.76, -95.37, 70, US), ("Houston", 29.95, -94.02, 35, US),   # + Beaumont/Port Arthur
    ("Dallas", 32.78, -96.80, 60, US),
    ("San Antonio/Austin", 29.42, -98.49, 50, US), ("San Antonio/Austin", 30.27, -97.74, 45, US),
    ("Memphis", 35.15, -90.05, 50, US),
    ("Nashville", 36.16, -86.78, 50, US),
    ("New Orleans", 29.95, -90.07, 50, US),
    ("Baton Rouge", 30.45, -91.19, 40, US),
    ("Miami/South Florida", 25.76, -80.19, 45, US), ("Miami/South Florida", 26.20, -80.15, 35, US),
    ("Miami/South Florida", 26.71, -80.06, 35, US),
    ("Jacksonville", 30.33, -81.66, 50, US),
    ("Tampa/Orlando", 27.95, -82.46, 50, US), ("Tampa/Orlando", 28.54, -81.38, 50, US),
    ("Chicago", 41.88, -87.63, 60, US),
    ("Detroit", 42.33, -83.05, 60, US),
    ("St. Louis", 38.63, -90.20, 50, US),
    ("Minneapolis", 44.98, -93.27, 50, US),
    ("Kansas City", 39.10, -94.58, 50, US),
    ("Milwaukee", 43.04, -87.91, 40, US),
    ("London", 51.51, -0.13, 50, UK),
    ("Manchester/Birmingham", 53.48, -2.24, 40, UK), ("Manchester/Birmingham", 52.49, -1.89, 40, UK),
    ("Toronto", 43.65, -79.38, 60, CANADA),
    ("Montreal", 45.50, -73.57, 50, CANADA),
]
# Optional scenes, kept only if they end up with >= EXTRA_SCENE_MIN matched artists.
EXTRA_CENTERS = [
    ("Fresno", 36.74, -119.79, 50, US), ("Portland", 45.52, -122.68, 50, US),
    ("Las Vegas", 36.17, -115.14, 50, US), ("Salt Lake City", 40.76, -111.89, 50, US),
    ("Albuquerque", 35.08, -106.65, 50, US), ("Pittsburgh", 40.44, -80.00, 50, US),
    ("Buffalo", 42.89, -78.88, 45, US), ("Rochester", 43.16, -77.61, 40, US),
    ("Connecticut", 41.40, -72.90, 60, US), ("Providence", 41.82, -71.41, 30, US),
    ("Indianapolis", 39.77, -86.16, 50, US), ("Louisville", 38.25, -85.76, 45, US),
    ("Birmingham AL", 33.52, -86.80, 50, US), ("Jackson MS", 32.30, -90.18, 50, US),
    ("Shreveport", 32.52, -93.75, 45, US), ("Little Rock", 34.75, -92.29, 50, US),
    ("Oklahoma City/Tulsa", 35.47, -97.52, 50, US), ("Oklahoma City/Tulsa", 36.15, -95.99, 45, US),
    ("Omaha", 41.26, -95.93, 45, US), ("Flint", 43.01, -83.69, 35, US),
    ("Columbia SC", 34.00, -81.03, 45, US), ("Mobile", 30.69, -88.04, 45, US),
    ("Vancouver", 49.28, -123.12, 50, CANADA), ("Ottawa", 45.42, -75.70, 45, CANADA),
    ("Calgary/Edmonton", 51.05, -114.07, 45, CANADA), ("Calgary/Edmonton", 53.55, -113.49, 45, CANADA),
    ("Winnipeg", 49.90, -97.14, 45, CANADA),
    ("Leeds/Sheffield", 53.80, -1.55, 35, UK), ("Leeds/Sheffield", 53.38, -1.47, 30, UK),
    ("Bristol", 51.45, -2.59, 35, UK), ("Liverpool", 53.41, -2.99, 30, UK),
    ("Nottingham", 52.95, -1.15, 30, UK), ("Glasgow", 55.86, -4.25, 40, UK),
]
STATEWIDE_SCENES = {"OH": "Cleveland/Ohio", "NC": "Charlotte/NC"}
# scenes that must not leak across state lines (e.g. Norwalk CT is near Long Island but not NYC)
SCENE_STATES = {"New York City": {"NY"}, "New Jersey": {"NJ"}, "DMV": {"DC", "MD", "VA"}, "Virginia": {"VA"}}


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def scene_for(res, centers):
    """Scene from a resolved place (coords + state), or None."""
    if res is None:
        return None
    st, country = res["state"], res["country"]
    if country == US and st in STATEWIDE_SCENES:
        return STATEWIDE_SCENES[st]
    if res["coord"] is None:
        return None
    best, bd = None, 1e9
    for name, la, lo, rad, c in centers:
        if c != country and not (c == US and country is None):
            continue
        if st and name in SCENE_STATES and st not in SCENE_STATES[name]:
            continue
        d = haversine(res["coord"], (la, lo))
        if d <= rad and d < bd:
            best, bd = name, d
    if country == US and st == "NJ":   # all of NJ is its own scene, except the Camden side of Philly
        return "Philadelphia" if best == "Philadelphia" and bd < 30 else "New Jersey"
    return best


def region_for(res):
    if res is None:
        return None
    c = res["country"]
    if c == US:
        return REGION_BY_STATE.get(res["state"])   # None if state unknown or a territory
    if c == UK:
        return "UK"
    if c == CANADA:
        return "Canada"
    return "Other" if c else None


# Scene-specific genres -> (region, scene or None)
GENRE_REGION = {
    "Q429264": ("West", None),            # West Coast hip-hop
    "Q1045541": ("West", None),           # G-funk
    "Q2043175": ("West", "Bay Area"),     # hyphy
    "Q17995961": ("West", "Bay Area"),    # mobb music
    "Q139033419": ("West", "Bay Area"),   # Bay Area hip-hop
    "Q124692508": ("West", "Los Angeles"),  # jerk
    "Q135239105": ("West", "Los Angeles"),  # jerk rap
    "Q2267754": ("West", None),           # Northwest hip hop
    "Q876171": ("East", None),            # East Coast hip-hop
    "Q21261423": ("East", "New York City"),  # Nuyorican rap
    "Q104847359": ("East", "New York City"),  # Brooklyn drill
    "Q126281100": ("East", "New York City"),  # New York drill
    "Q124352838": ("East", "New York City"),  # Bronx drill
    "Q115483855": ("East", "New Jersey"),     # Jersey drill
    "Q124399713": ("East", "New Jersey"),     # Jersey club rap
    "Q134997892": ("East", "Philadelphia"),   # philly drill
    "Q125653755": ("East", "Philadelphia"),   # Philly club rap
    "Q7293809": ("East", "DMV"),          # DMV hip-hop
    "Q4852979": ("East", "DMV"),          # Baltimore club
    "Q2697917": ("East", "DMV"),          # go-go
    "Q1253172": ("South", None),          # Southern hip-hop
    "Q8560269": ("South", None),          # dirty south
    "Q936573": ("South", None),           # crunk
    "Q2991840": ("South", None),          # snap music
    "Q1849006": ("South", "Houston"),     # Houston hip-hop
    "Q136908587": ("South", "Houston"),   # Houston sound
    "Q2261628": ("South", None),          # chopped and screwed (technique used well beyond Houston)
    "Q4816198": ("South", "Atlanta"),     # Atlanta hip-hop
    "Q106642094": ("South", "Atlanta"),   # Atlanta bass
    "Q6010117": ("South", "Memphis"),     # Memphis rap
    "Q135089743": ("South", "New Orleans"),  # New Orleans hip-hop
    "Q4949812": ("South", "New Orleans"),    # bounce music
    "Q136653467": ("South", "New Orleans"),  # sissy bounce
    "Q546206": ("South", None),           # Miami bass (also Atlanta/Orlando bass)
    "Q135239483": ("South", "Miami/South Florida"),  # South Florida SoundCloud rap
    "Q2517263": ("Midwest", None),        # Midwest hip-hop
    "Q2963337": ("Midwest", "Chicago"),   # Chicago hip-hop
    "Q115691081": ("Midwest", "Chicago"),  # Chicago drill
    "Q7858125": ("Midwest", "Minneapolis"),  # Twin Cities hip hop
    "Q123907465": ("Midwest", None),      # Michigan rap
    "Q106839650": ("Midwest", "Detroit"),  # Detroit sound
    "Q125848864": ("Midwest", "Milwaukee"),  # Milwaukee hip hop
    "Q24061928": ("Midwest", "St. Louis"),   # St. Louis bounce
    "Q65943851": ("UK", None),            # UK drill
    "Q1166726": ("UK", None),             # grime
    "Q469343": ("UK", None),              # UK rap
    "Q125687720": ("UK", None),           # road rap
    "Q85740017": ("UK", None),            # Afroswing
    "Q139266893": ("UK", None),           # UK underground rap
    "Q918095": ("UK", None),              # Britcore
    "Q469630": ("Canada", None),          # Canadian hip-hop
    "Q129312602": ("Canada", "Toronto"),  # Toronto sound
    "Q3135794": ("Canada", "Montreal"),   # Quebec hip hop
}


# ----------------------------------------------------------------------------- name matching

def k_exact(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).casefold()).strip()


def _strip(s):
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def k_norm(s):
    s = _strip(s).replace("&", "and")
    return "".join(ch for ch in s if ch.isalnum())


def k_dollar(s):
    return k_norm(s.replace("$", "s"))


RAPPER_OCC = {"Q2252262", "Q73399344", "Q71194203", "Q140251484", "Q100493654"}


def build_index(items):
    idx = {m: defaultdict(set) for m in ("exact_label", "exact_alias", "normalized", "dollar")}
    for q, it in items.items():
        for lab in it["labels"]:
            idx["exact_label"][k_exact(lab)].add(q)
        for al in it["aliases"]:
            idx["exact_alias"][k_exact(al)].add(q)
        for name in it["labels"] | it["aliases"]:
            if len(k_norm(name)) >= 3:
                idx["normalized"][k_norm(name)].add(q)
            if "$" in name or "s" in name.casefold():
                if len(k_dollar(name)) >= 3:
                    idx["dollar"][k_dollar(name)].add(q)
    return idx


def pull_genius_ids():
    """Wikidata P2373 (Genius artist slug) -> normalized key -> items."""
    idx = defaultdict(set)
    for r in sparql("SELECT ?item ?id WHERE { ?item wdt:P2373 ?id . }", "genius_ids"):
        key = k_norm(r["id"].replace("-", " "))
        if len(key) >= 2 and r["item"].startswith("Q"):
            idx[key].add(r["item"])
    return idx


DICT_WORDS = set()
if Path("/usr/share/dict/words").exists():
    DICT_WORDS = {w.strip().lower() for w in open("/usr/share/dict/words")}


def generic_name(name):
    k = k_norm(name)
    return len(k) <= 4 or k in DICT_WORDS


def genius_id_candidates(genius, gid_idx):
    """Genius artist_id -> items whose Genius slug matches. When several Genius names share a
    key (e.g. 'Future' / 'FUTURE'), only the artist with the most rap songs gets the slug."""
    g = genius.assign(key=genius.artist.map(k_norm))
    g = g[g.key.isin(gid_idx.keys())].sort_values("rap_songs", ascending=False).drop_duplicates("key")
    return {r.artist_id: gid_idx[r.key] for r in g.itertuples(index=False)}


def plausible(it, g):
    """Reject matches where the Genius catalogue predates the person (same-name amateurs)."""
    by = it["birth_year"]
    if by and g.last_year < by + 12:
        return False
    return True


def pick(cands, items):
    return max(cands, key=lambda q: (bool(RAPPER_OCC & set(items[q]["P106"])), items[q]["sitelinks"], -int(q[1:])))


def match_artists(genius, items, idx, gid_cands, gid_idx):
    """Tiers: genius_id > exact_label > exact_alias > normalized ($->s last). A label/alias candidate
    is dropped when the item carries a Genius slug for a *different* name (it is another Genius
    artist) -- always for label/normalized hits, and for alias hits when the name is generic."""
    slugs = defaultdict(set)
    for key, qs in gid_idx.items():
        for q in qs:
            slugs[q].add(key)
    out = {}
    for g in genius.itertuples(index=False):
        name = g.artist
        cands = [q for q in gid_cands.get(g.artist_id, ()) if q in items and plausible(items[q], g)]
        if cands:
            out[g.artist_id] = (pick(cands, items), "genius_id", len(cands))
            continue
        keys = [("exact_label", k_exact(name)), ("exact_alias", k_exact(name)),
                ("normalized", k_norm(name))]
        if "$" in name:
            keys.append(("dollar", k_dollar(name)))
        for method, key in keys:
            cands = [q for q in idx[method].get(key, ()) if plausible(items[q], g)
                     and not (slugs.get(q) and k_norm(name) not in slugs[q]
                              and (method != "exact_alias" or generic_name(name)))
                     # generic aliases ("Jesse", "Big C", "Mic") only for well-known items
                     and not (method != "exact_label" and generic_name(name) and items[q]["sitelinks"] < 10)]
            if cands:
                out[g.artist_id] = (pick(cands, items), "normalized" if method == "dollar" else method, len(cands))
                break
    return out


# ----------------------------------------------------------------------------- MusicBrainz

def lucene_escape(s):
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", s)


def mb_area_to_qid(area_id, depth=0):
    """MusicBrainz area -> Wikidata QID via url-rels, climbing 'part of' if the area has none."""
    if depth > 4:
        return None
    a = mb_get(f"{MB}/area/{area_id}?inc=url-rels+area-rels&fmt=json")
    if not a or a.get("_status") == 404:
        return None
    for rel in a.get("relations", []):
        url = (rel.get("url") or {}).get("resource", "")
        if rel.get("type") == "wikidata" and "wikidata.org/wiki/Q" in url:
            return url.rsplit("/", 1)[-1]
    for rel in a.get("relations", []):
        if rel.get("type") == "part of" and rel.get("direction") == "backward" and rel.get("area"):
            return mb_area_to_qid(rel["area"]["id"], depth + 1)
    return None


def _mb_area(art):
    area = art.get("begin-area") or art.get("area")
    if not area:
        return {"area_qid": None, "area_name": None, "area_is_begin": False}
    return {"area_qid": mb_area_to_qid(area["id"]), "area_name": area["name"],
            "area_is_begin": bool(art.get("begin-area"))}


def musicbrainz_fallback(genius, matched, need_area):
    """(a) Wikidata-matched artists without a usable place: look up their MusicBrainz ID (P434).
    (b) Unmatched artists with >= MB_MIN_SONGS rap songs: artist search, top hit only if score >= 95
        and the name matches after normalization. Capped at MB_CAP searches."""
    out = {}
    for aid, mbid in need_area.items():
        art = mb_get(f"{MB}/artist/{mbid}?fmt=json")
        if art and art.get("id"):
            out[aid] = {"mb_id": mbid, **_mb_area(art)}
    log(f"MusicBrainz lookups by P434 for {len(need_area)} matched-but-placeless artists; "
        f"with area: {sum(1 for v in out.values() if v['area_qid'])}")

    cands = genius[(genius.rap_songs >= MB_MIN_SONGS) & ~genius.artist_id.isin(matched)]
    cands = cands.sort_values("rap_songs", ascending=False).head(MB_CAP)
    log(f"MusicBrainz search for {len(cands)} unmatched artists with >= {MB_MIN_SONGS} songs")
    n = 0
    for i, g in enumerate(cands.itertuples(index=False)):
        if i % 200 == 0:
            log(f"  MB {i}/{len(cands)} ({n} matched)")
        url = f"{MB}/artist/?query=artist:%22{quote(lucene_escape(g.artist))}%22&fmt=json&limit=5"
        arts = (mb_get(url) or {}).get("artists") or []
        if not arts:
            continue
        top = arts[0]
        names = [top.get("name", ""), top.get("sort-name", "")] + [x.get("name", "") for x in top.get("aliases") or []]
        if int(top.get("score", 0)) < 95 or not any(k_norm(x) and k_norm(x) == k_norm(g.artist) for x in names):
            continue
        out[g.artist_id] = {"mb_id": top["id"], "search": True, **_mb_area(top)}
        n += 1
    log(f"MusicBrainz search matched {n}; with area: "
        f"{sum(1 for v in out.values() if v.get('search') and v['area_qid'])}")
    return out


# ----------------------------------------------------------------------------- assignment

def assign(it, R, centers, genre_labels):
    """Pick origin for one Wikidata item. Returns dict of origin fields."""
    def res(q):
        r = R.resolve(q)
        if r is None:
            return None
        return {**r, "qid": q}

    def pack(r, source, region=None, scene=None):
        region = region or region_for(r)
        return {"place": r, "region": region, "scene": scene if scene is not None else scene_for(r, centers)
                if r else scene, "region_source": source}

    births = [r for r in (res(q) for q in it["P19"]) if r]
    resid = [r for r in (res(q) for q in it["P551"]) if r]
    formed = [r for r in (res(q) for q in it["P740"]) if r]

    origins = [r for r in (res(q) for q in it["wp_origin"]) if r]

    def refine(r, source):
        """A state/country-level origin is sharpened by a Wikipedia origin, residence or birthplace
        inside it (Clipse: formed in 'Virginia' -> Wikipedia origin Virginia Beach)."""
        if r["coord"] is None:
            for x in origins + resid + births:
                if x["coord"] and x["country"] == r["country"] and (r["is_country"] or x["state"] == r["state"]):
                    return pack(x, source + "+refined")
        return pack(r, source)

    # 1. formation location (groups)
    for r in formed:
        if region_for(r):
            return refine(r, "P740_formation")

    # 1b. English Wikipedia infobox "origin" (where the artist/group came up)
    for r in origins:
        if region_for(r):
            return refine(r, "wikipedia_origin")

    # 2. scene-specific genre
    gclaims = [GENRE_REGION[g] for g in it["P136"] if g in GENRE_REGION]
    if gclaims:
        # prefer genre claims that agree with a known place; else the most specific claim
        places = sorted(resid + births, key=lambda r: r["coord"] is None)   # city-level places first
        for greg, gscene in sorted(gclaims, key=lambda x: x[1] is None):
            in_scene = [r for r in places if gscene and scene_for(r, centers) == gscene]
            if in_scene:
                return pack(in_scene[0], "genre+place")
        for greg, gscene in sorted(gclaims, key=lambda x: x[1] is None):
            in_region = [r for r in places if region_for(r) == greg]
            if in_region:
                r = in_region[0]
                return pack(r, "genre+place", greg, gscene or scene_for(r, centers))
        regs = {g[0] for g in gclaims}
        if len(regs) == 1 or births + resid == []:
            greg, gscene = sorted(gclaims, key=lambda x: x[1] is None)[0]
            r = births[0] if births else (resid[0] if resid else None)
            return {"place": r, "region": greg, "scene": gscene, "region_source": "genre"}

    # 3/4. residence vs birthplace
    b = next((r for r in births if region_for(r)), None)
    by = it["birth_year"]
    for r in resid:
        reg = region_for(r)
        if reg not in ("West", "Midwest", "South", "East", "UK", "Canada") or (b and r["coord"] is None):
            continue
        start = it["res_start"].get(r["qid"])
        if (b is None or region_for(b) not in ("West", "Midwest", "South", "East", "UK", "Canada")
                or region_for(b) == reg or (start and by and start - by <= 18)):
            return pack(r, "P551_residence")
    if b:
        return pack(b, "P19_birthplace")

    # 5. group members' birthplaces
    if it.get("member_places"):
        cnt = Counter((region_for(r), scene_for(r, centers)) for r in it["member_places"] if region_for(r))
        if cnt:
            (reg, sc), n = cnt.most_common(1)[0]
            r = next(r for r in it["member_places"] if (region_for(r), scene_for(r, centers)) == (reg, sc))
            return pack(r, "members_birthplace")

    # 6. country only
    for q in it["P495"] + it["P27"]:
        r = res(q)
        if r and r["country"] and r["country"] != US:
            return pack(r, "country_only")
    return None


def main():
    genius = load_genius()
    log(f"genius rap artists: {len(genius)}")

    items = fetch_items(discover_hiphop_qids())
    gid_idx = pull_genius_ids()
    gid_cands = genius_id_candidates(genius, gid_idx)
    extra = {q for c in gid_cands.values() for q in c} - set(items)
    items.update(fetch_items(extra, prefix="x_"))
    log(f"+{len(extra)} non-hip-hop-tagged items reachable through Genius artist IDs")

    # group members' birthplaces
    members = {m for it in items.values() for m in it["P527"]}
    mem_birth = defaultdict(list)
    for r in sparql_batched("""SELECT ?m ?b WHERE { VALUES ?m { {values} } ?m wdt:P19 ?b . }""",
                            members, "member_births"):
        mem_birth[r["m"]].append(r["b"])

    idx = build_index(items)
    wd_match = match_artists(genius, items, idx, gid_cands, gid_idx)
    log(f"wikidata name matches: {len(wd_match)}; " +
        str(Counter(m for _, m, _ in wd_match.values())))

    # Wikipedia origins for every item (not just matched ones) so the cache does not depend on matching
    for q, origin in wikipedia_origins(set(items)).items():
        items[q]["wp_origin"] = origin

    # places for everything Wikidata knows
    seed = {q for it in items.values() for k in ("P19", "P740", "P551", "P495", "P27", "wp_origin") for q in it[k]}
    seed |= {b for bl in mem_birth.values() for b in bl}
    places = pull_places(seed)
    R = PlaceResolver(places)
    for it in items.values():
        it["member_places"] = [R.resolve(b) | {"qid": b} for m in it["P527"] for b in mem_birth.get(m, [])[:1]
                               if R.resolve(b)]
    genre_q = {g for it in items.values() for g in it["P136"]}
    genre_labels = pull_labels(genre_q, "genrelabels")

    # MusicBrainz: matched-but-placeless (>= 10 songs, via P434) and unmatched (>= MB_MIN_SONGS, via search)
    songs = dict(zip(genius.artist_id, genius.rap_songs))
    placeless = {aid: q for aid, (q, _, _) in wd_match.items()
                 if songs[aid] >= 10 and not ((x := assign(items[q], R, SCENE_CENTERS, genre_labels)) and x["region"])}
    mbids = {r["item"]: r["mb"] for r in sparql_batched("""SELECT ?item ?mb WHERE { VALUES ?item { {values} }
            ?item wdt:P434 ?mb . }""", set(items), "mbids")}
    need_area = {aid: mbids[q] for aid, q in placeless.items() if q in mbids}
    mb_match = {} if "--skip-mb" in sys.argv else musicbrainz_fallback(genius, set(wd_match), need_area)
    area_q = {v["area_qid"] for v in mb_match.values() if v["area_qid"]}
    pull_places(area_q - set(places), places, tag="mbplaces")
    R = PlaceResolver(places)
    country_labels = {q: places[q]["label"] for q in R.countries if q in places}

    def build(centers):
        rows = []
        cache = {}
        for g in genius.itertuples(index=False):
            row = {"artist_id": g.artist_id, "artist": g.artist, "rap_songs": g.rap_songs,
                   "first_year": g.first_year, "last_year": g.last_year, "wikidata_qid": None, "mb_id": None,
                   "match_method": None, "sitelinks": None, "origin_place": None, "origin_state": None,
                   "origin_country": None, "lat": None, "lon": None, "region": None, "scene": None,
                   "region_source": None, "genres": None}
            a = None
            if g.artist_id in wd_match:
                q, method, ncand = wd_match[g.artist_id]
                it = items[q]
                if q not in cache:
                    cache[q] = assign(it, R, centers, genre_labels)
                a = cache[q]
                row.update(wikidata_qid=q, match_method=method, sitelinks=it["sitelinks"],
                           genres="; ".join(genre_labels.get(x, x) for x in it["P136"]) or None)
            if g.artist_id in mb_match:
                m = mb_match[g.artist_id]
                row["mb_id"] = m["mb_id"]
                if row["match_method"] is None:
                    row["match_method"] = "musicbrainz"
                if not (a and a["region"]) and m["area_qid"] and R.resolve(m["area_qid"]):
                    r = R.resolve(m["area_qid"]) | {"qid": m["area_qid"]}
                    a = {"place": r, "region": region_for(r), "scene": scene_for(r, centers),
                         "region_source": "musicbrainz_" + ("begin_area" if m["area_is_begin"] else "area")}
            if a:
                r = a["place"]
                if r:
                    row.update(origin_place=r["label"], origin_state=r["state"],
                               origin_country=country_labels.get(r["country"], r["country"]),
                               lat=r["coord"][0] if r["coord"] else None, lon=r["coord"][1] if r["coord"] else None)
                row.update(region=a["region"], scene=a["scene"], region_source=a["region_source"])
                if row["region"] is None and row["scene"] is None:
                    row["region_source"] = (row["region_source"] or "") + ":unresolved"
            rows.append(row)
        return pd.DataFrame(rows)

    df = build(SCENE_CENTERS + EXTRA_CENTERS)
    extra_names = {c[0] for c in EXTRA_CENTERS}
    counts = df[df.scene.isin(extra_names)].groupby("scene").size()
    keep = set(counts[counts >= EXTRA_SCENE_MIN].index)
    log(f"optional scenes kept (>= {EXTRA_SCENE_MIN} artists): {sorted(keep)}; dropped: "
        f"{sorted(extra_names - keep)}")
    df = build(SCENE_CENTERS + [c for c in EXTRA_CENTERS if c[0] in keep])

    for c in ("sitelinks", "lat", "lon"):
        df[c] = pd.to_numeric(df[c])
    df["sitelinks"] = df["sitelinks"].astype("Int64")
    df = df.sort_values("rap_songs", ascending=False).reset_index(drop=True)
    df.to_parquet(ROOT / "derived" / "artist_regions.parquet", index=False)
    df.head(300).to_csv(ROOT / "derived" / "artist_regions_top300.csv", index=False)
    log(f"wrote {len(df)} rows; mapped region for {df.region.notna().sum()} artists")
    validate(df)


# Spot checks: artist -> (region, acceptable scenes; None = region only)
VALIDATION = {
    "Snoop Dogg": ("West", {"Los Angeles"}), "E-40": ("West", {"Bay Area"}), "Too $hort": ("West", {"Bay Area"}),
    "Kendrick Lamar": ("West", {"Los Angeles"}), "JAY-Z": ("East", {"New York City"}), "Nas": ("East", {"New York City"}),
    "Nicki Minaj": ("East", {"New York City"}), "Meek Mill": ("East", {"Philadelphia"}),
    "Gucci Mane": ("South", {"Atlanta"}), "Young Thug": ("South", {"Atlanta"}), "UGK": ("South", {"Houston", None}),
    "Three 6 Mafia": ("South", {"Memphis"}), "Lil Wayne": ("South", {"New Orleans"}),
    "Kodak Black": ("South", {"Miami/South Florida"}), "Chief Keef": ("Midwest", {"Chicago"}),
    "Eminem": ("Midwest", {"Detroit"}), "Drake": ("Canada", {"Toronto"}), "Wiley": ("UK", {"London"}),
    "Pusha T": ("East", {"Virginia"}), "Mac Dre": ("West", {"Bay Area"}),
}


def validate(df):
    v = df[df.artist.isin(VALIDATION)].set_index("artist")
    bad = 0
    for name, (reg, scenes) in VALIDATION.items():
        r = v.loc[name] if name in v.index else None
        got = (None, None, None) if r is None else (r.region, r.scene if pd.notna(r.scene) else None, r.region_source)
        ok = got[0] == reg and got[1] in scenes
        bad += not ok
        log(f"  {'ok ' if ok else 'BAD'} {name:15} {got[0]} / {got[1]}  [{got[2]}]")
    log(f"validation: {len(VALIDATION) - bad}/{len(VALIDATION)} as expected")


if __name__ == "__main__":
    main()
