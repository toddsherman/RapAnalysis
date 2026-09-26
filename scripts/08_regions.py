"""Regional slang: which words each region's artists use far more than everyone else,
and how regional terms spread.

Only artists matched to a place (derived/artist_regions.parquet, from 03) take part.
Usage is artist-level: an artist "uses" a word in an era if any of their rap songs from
that era contains it.

Distinctiveness is the log-odds ratio with an informative Dirichlet prior (Monroe et al.
2008), region vs all other mapped regions, on artist counts.

Output: site/data/regions.json
"""
import json
import math
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "lexicon")
from blocklist import blocked  # noqa: E402

REGIONS = ["West", "South", "East", "Midwest", "UK", "Canada"]
ERAS = [("1988–2007", 1988, 2007), ("2008–2021", 2008, 2021)]
Y0, Y1 = 1988, 2021
YEARS = list(range(Y0, Y1 + 1))

con = duckdb.connect()
con.sql("SET memory_limit='8GB'")
con.sql(f"""
create temp table ar as
select artist_id, region from 'derived/artist_regions.parquet'
where region in ({",".join(repr(r) for r in REGIONS)})
""")

# Words that are names of rap artists with real catalogues are shout-outs, not slang.
names = set(con.sql("""
  select lower(a.artist) from 'derived/artists.parquet' a join 'derived/song_index.parquet' s using (artist_id)
  where s.tag='rap' group by 1 having count(*) >= 30
""").df().iloc[:, 0])
dictionary = set(w.strip().lower() for w in open("/usr/share/dict/words"))
# Non-dictionary pieces of established artists' names (keak, selektah, mistah...) are
# shout-outs too. Nicknames that aren't in any artist name slip through; that's accepted.
name_tokens = {tok for n in names for tok in n.replace(".", " ").replace("$", "s").split()
               if len(tok) > 2 and tok not in dictionary}
# Place names (hometown shout-outs) are regional but they aren't slang; they get their own
# short list. Built from the artists' own origin places plus common neighbourhood names.
places = set()
for p in con.sql("select distinct origin_place from 'derived/artist_regions.parquet' where origin_place is not null").df().iloc[:, 0]:
    for tok in str(p).lower().replace(",", " ").replace("-", " ").split():
        if len(tok) > 3:
            places.add(tok)
places |= set("""
brooklyn bronx bx bk queens harlem manhattan staten uptown stuy bedstuy brownsville flatbush southside northside eastside westside
oakland vallejo crenshaw inglewood compton cpt lbc pch fairfax berkeley frisco richmond sactown sacramento hayward
decatur bankhead dade magnolia marta zone6 ecw westcoast eastcoast midwest dirty south houston htown atl atlanta memphis
nola orleans chiraq chi detroit detroit's york's california's cali calif toronto scarborough trudeau cn philly southwest
tottenham peckham brixton hackney croydon ends manor london's
""".split())

out = {"eras": [e[0] for e in ERAS], "regions": [], "distinctive": {}}
places_out = {}
active = {}
for name, a, b in ERAS:
    act = con.sql(f"""
      select ar.region, count(distinct s.artist_id) n from 'derived/song_index.parquet' s join ar using (artist_id)
      where s.tag='rap' and s.year between {a} and {b} group by 1
    """).df().set_index("region").n
    active[name] = act
    wc = con.sql(f"""
      select ar.region, w.word, count(distinct w.artist_id) as artists
      from 'derived/rap_artist_year_word.parquet' w join ar using (artist_id)
      where w.year between {a} and {b}
      group by 1, 2
    """).df()
    piv = wc.pivot(index="word", columns="region", values="artists").fillna(0)
    piv = piv[[r for r in REGIONS if r in piv.columns]]
    total = piv.sum(axis=1)
    prior_total = total.sum()
    per_region = {}
    for r in piv.columns:
        n_r = act.get(r, 0)
        n_o = act.drop(r, errors="ignore").sum()
        y1 = piv[r]
        y2 = total - y1
        alpha = total / prior_total * 2000
        a0 = alpha.sum()
        # counts of "used" vs "didn't use" are per-artist, so frame it as rates of usage
        d = np.log((y1 + alpha) / (n_r + a0 - y1 - alpha)) - np.log((y2 + alpha) / (n_o + a0 - y2 - alpha))
        z = d / np.sqrt(1 / (y1 + alpha) + 1 / (y2 + alpha))
        share = y1 / n_r * 100
        rest = y2 / n_o * 100
        df = pd.DataFrame({"z": z, "share": share, "rest": rest, "n": y1})
        ok = (df.n >= max(6, 0.015 * n_r)) & (df.share >= 2 * df.rest) & (df.z > 3)
        df = df[ok]
        drop = np.array([w.isdigit() or blocked(w) or (w in names and w not in dictionary) or w in name_tokens
                         for w in df.index], dtype=bool)
        df = df[~drop].sort_values("z", ascending=False)
        is_place = np.array([w in places for w in df.index], dtype=bool)
        fmt = lambda d: [dict(w=w, share=round(float(x.share), 2), rest=round(float(x.rest), 2), z=round(float(x.z), 1))
                         for w, x in d.iterrows()]
        per_region[r] = fmt(df[~is_place].head(30))
        places_out.setdefault(name, {})[r] = fmt(df[is_place].head(8))
    out["distinctive"][name] = per_region
    out["places"] = places_out
    print(name, {r: [d["w"] for d in v[:12]] for r, v in per_region.items()}, flush=True)

for r in REGIONS:
    out["regions"].append(dict(id=r, artists_by_era={e: int(active[e].get(r, 0)) for e in active}))

# --- diffusion of curated terms ------------------------------------------------------------
lex = {t["id"]: t for t in json.load(open("derived/lexicon.json"))}
ay = con.sql("""
  select distinct s.artist_id, s.year, ar.region from 'derived/song_index.parquet' s join ar using (artist_id) where s.tag='rap'
""").df()
den = ay.groupby(["region", "year"]).size()
use = con.sql("""
  select distinct t.term_id, s.artist_id, s.year, ar.region
  from 'derived/song_terms.parquet' t join 'derived/song_index.parquet' s using (song_key) join ar using (artist_id)
  where s.tag='rap'
""").df()
num = use.groupby(["term_id", "region", "year"]).size()
num_map = {k: g.droplevel([0, 1]) for k, g in num.groupby(level=[0, 1])}


def takeoff(s, n, valid):
    """Start of a region's climb to its peak (25% of peak), walking back from the peak.

    Returns (year, established): `established` is True when the climb runs back into the
    first years with enough artists to measure (the region was already using the word
    when the data becomes reliable), in which case the year is an upper bound.
    """
    peak = s.max()
    if peak < 1.0:
        return None, False
    py = y = int(s.idxmax())
    if n.get(py, 0) < 5:  # too few users even at the peak to trust the timing
        return None, False
    while y - 1 >= Y0 and valid[y - 1] and s[y - 1] >= 0.25 * peak:
        y -= 1
    established = y - 1 < Y0 or not valid[y - 1]
    return y, established


diff = []
for tid, t in lex.items():
    if t["poly"]:
        continue
    tk, est, lvl = {}, {}, {}
    for r in REGIONS[:4]:  # US regions carry enough artists per year for timing
        d = den.get(r, pd.Series(dtype=float)).reindex(YEARS, fill_value=0)
        valid = d >= 15
        nn = num_map.get((tid, r), pd.Series(dtype=float)).reindex(YEARS, fill_value=0)
        pct = (nn / d.where(valid) * 100).fillna(0).rolling(3, center=True, min_periods=1).mean()
        y, e = takeoff(pct, nn, valid)
        if y:
            tk[r], est[r], lvl[r] = y, e, float(pct[y])
    if len(tk) < 2:
        continue
    # Earliest take-off wins; a tie goes to the region using it more heavily that year.
    origin = min(tk, key=lambda r: (tk[r], -lvl[r]))
    others = sorted(v for k, v in tk.items() if k != origin)
    lead = float(np.median(others)) - tk[origin]
    when = f"by {tk[origin]}" if est[origin] else f"{tk[origin]}"
    diff.append(dict(id=tid, origin=origin, takeoffs=tk, established=est[origin], lead=lead,
                     note=f"{origin} {when}, elsewhere {int(np.median(others))}"))
diff.sort(key=lambda d: -d["lead"])
featured = ["hella", "hyphy", "shawty", "crunk", "trill", "opps", "thot", "slatt", "jit", "drip", "izzle", "wordup", "fosho", "bling", "swag"]
# Outside the featured list, only clean cases: a real take-off (not "already established
# when the data starts") from 1993 on, ahead of the other regions by 3+ years.
chosen = [d for d in diff if d["id"] in featured and d["lead"] >= 1]
chosen += [d for d in diff if d["id"] not in featured and not d["established"] and d["takeoffs"][d["origin"]] >= 1993 and d["lead"] >= 3][:8]
out["diffusion"] = chosen
print("diffusion:", [(d["id"], d["note"]) for d in chosen])

json.dump(out, open("site/data/regions.json", "w"), separators=(",", ":"), allow_nan=False)
