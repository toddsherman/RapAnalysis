"""Is rap slang turning over faster?

Measured on a stable pool of established artists (a Wikipedia presence, or >= 20 rap
songs on Genius) so the 2018-2021 flood of one-off uploads does not masquerade as churn.

Two views:
  lifecycles  For every bursty, rap-distinctive word: how many years it took to climb from
              a quarter of its peak to the peak, and how much of its peak usage it kept
              3 years later. Grouped by the era in which the word peaked.
  churn       Of the 200 most-used rap-distinctive words in year Y, the share still in the
              top 200 five years later.

Output: site/data/turnover.json
"""
import json
import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "lexicon")
from blocklist import blocked  # noqa: E402

Y0, Y1 = 1988, 2021
YEARS = list(range(Y0, Y1 + 1))
COHORTS = [("1992–1999", 1992, 1999), ("2000–2006", 2000, 2006), ("2007–2012", 2007, 2012), ("2013–2018", 2013, 2018)]
TOPK, GAP = 200, 5

con = duckdb.connect()
con.sql("SET memory_limit='8GB'")
have_regions = os.path.exists("derived/artist_regions.parquet")
fame = ("left join 'derived/artist_regions.parquet' r using (artist_id)" if have_regions else "")
fame_cond = "or coalesce(r.sitelinks, 0) > 0" if have_regions else ""
con.sql(f"""
create temp table pool as
select artist_id from (
  select s.artist_id, count(*) n from 'derived/song_index.parquet' s where tag='rap' group by 1
) c {fame}
where c.n >= 20 {fame_cond}
""")
print("pool artists:", con.sql("select count(*) from pool").fetchone()[0])

tot = con.sql(f"""
  select year, count(distinct artist_id) as n from 'derived/song_index.parquet'
  where tag='rap' and artist_id in (select artist_id from pool) and year between {Y0} and {Y1} group by 1
""").df().set_index("year").n.reindex(YEARS)

# Candidate vocabulary: rap-distinctive words (see 02_discover_slang.py) that are not
# numbers, slurs, or the name of a rap artist with a real catalogue.
spec = pd.read_csv("derived/term_summary.csv", keep_default_na=False)[["word", "rap_log_ratio", "in_dictionary"]]  # "nan"/"null" are words here
names = set(con.sql("""
  select lower(a.artist) from 'derived/artists.parquet' a join 'derived/song_index.parquet' s using (artist_id)
  where s.tag='rap' group by 1 having count(*) >= 30
""").df().iloc[:, 0])
spec = spec[(spec.rap_log_ratio >= 1.0) & ~spec.word.str.isdigit() & ~spec.word.map(blocked)
            & ~(spec.word.isin(names) & ~spec.in_dictionary)]
con.register("cand", spec[["word"]])

wy = con.sql(f"""
  select word, year, count(distinct artist_id) as artists
  from 'derived/rap_artist_year_word.parquet'
  where artist_id in (select artist_id from pool) and year between {Y0} and {Y1}
    and word in (select word from cand)
  group by 1, 2
""").df()
rates = wy.pivot(index="word", columns="year", values="artists").reindex(columns=YEARS).fillna(0)
counts = rates.copy()
rates = rates.div(tot, axis=1) * 100
smooth = rates.T.rolling(3, center=True, min_periods=1).mean().T

# --- lifecycles --------------------------------------------------------------------------
rows = []
for w in smooth.index:
    s = smooth.loc[w]
    peak, py = s.max(), int(s.idxmax())
    if peak < 0.5 or counts.loc[w, py] < 10:
        continue
    before = s[max(Y0, py - 8)]
    if before > peak / 3:  # not a burst: it was already common
        continue
    y = py
    while y - 1 >= Y0 and s[y - 1] >= 0.25 * peak:
        y -= 1
    if y == Y0:
        continue
    rise = py - y + 1
    keep3 = float(s[py + 3] / peak) if py + 3 <= Y1 else None
    rows.append(dict(word=w, peak_year=py, peak=round(float(peak), 2), rise=rise,
                     keep3=round(keep3, 3) if keep3 is not None else None))
life = pd.DataFrame(rows)

OFFSETS = list(range(-8, 9))
cohorts = []
for name, a, b in COHORTS:
    d = life[(life.peak_year >= a) & (life.peak_year <= b)]
    k = d.keep3.dropna()
    # Median usage relative to peak, with every word aligned on its own peak year.
    aligned = pd.DataFrame(
        [[(smooth.loc[w, py + o] / pk) if Y0 <= py + o <= Y1 else np.nan for o in OFFSETS]
         for w, py, pk in zip(d.word, d.peak_year, d.peak.map(float))],
        columns=OFFSETS)
    aligned = aligned.clip(upper=1.0)
    med = [round(float(aligned[o].median()), 3) if aligned[o].notna().sum() >= 15 else None for o in OFFSETS]
    cohorts.append(dict(
        curve=med,
        cohort=name, n=int(len(d)),
        rise_median=float(d.rise.median()) if len(d) else None,
        rise_q=[float(d.rise.quantile(.25)), float(d.rise.quantile(.75))] if len(d) else None,
        keep3_median=round(float(k.median()), 3) if len(k) else None,
        keep3_q=[round(float(k.quantile(.25)), 3), round(float(k.quantile(.75)), 3)] if len(k) else None,
        examples=d.sort_values("peak", ascending=False).word.head(12).tolist(),
    ))
    print(cohorts[-1])

# --- churn -------------------------------------------------------------------------------
churn = []
for y in range(1992, Y1 - GAP + 1):
    a = set(rates[y].nlargest(TOPK).index)
    b = set(rates[y + GAP].nlargest(TOPK).index)
    churn.append(dict(year=y, kept=round(len(a & b) / TOPK, 3)))
print([c["kept"] for c in churn])

# A few example words for each lifecycle scatter point (for the site's hover).
life_out = [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
            for r in life.sort_values("peak", ascending=False).head(600).to_dict("records")]
json.dump(dict(pool_artists=int(con.sql("select count(*) from pool").fetchone()[0]), offsets=OFFSETS,
               cohorts=cohorts, churn=churn, gap=GAP, topk=TOPK, words=life_out),
          open("site/data/turnover.json", "w"), separators=(",", ":"), allow_nan=False)
