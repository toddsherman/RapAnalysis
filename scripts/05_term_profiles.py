"""Per-term profiles for the curated lexicon: adoption curves in every genre, life-cycle
stage, crossover into other genres, and who started / popularised / owned each term.

Adoption = share of a genre's active artists that year who used the term in at least one
song. Rates are smoothed with a centred 3-year mean because single years are noisy.

Output: site/data/terms.json, site/data/meta.json
"""
import json
import math
import os

import duckdb
import numpy as np
import pandas as pd

Y0, Y1 = 1988, 2021
YEARS = list(range(Y0, Y1 + 1))
GENRES = ["rap", "rb", "pop", "rock", "country"]
OUT = "site/data"
os.makedirs(OUT, exist_ok=True)

con = duckdb.connect()
lex = json.load(open("derived/lexicon.json"))

# ---------------------------------------------------------------------------------------
# Artist attributes: fame proxy (Wikipedia sitelinks) and region, when the region mapping
# exists. Artists without a Wikidata match get sitelinks 0 and fall back to catalogue size.
artists = con.sql("""
  select a.artist_id, a.artist, count(*) filter (where s.tag='rap') as rap_songs
  from 'derived/artists.parquet' a left join 'derived/song_index.parquet' s using (artist_id)
  group by all
""").df().set_index("artist_id")
if os.path.exists("derived/artist_regions.parquet"):
    reg = con.sql("select artist_id, sitelinks, wikidata_qid is not null as in_wikidata, region, scene from 'derived/artist_regions.parquet'").df().set_index("artist_id")
    artists = artists.join(reg)
else:
    artists["sitelinks"] = np.nan
    artists["in_wikidata"] = False
    artists["region"] = None
    artists["scene"] = None
artists["sitelinks"] = artists["sitelinks"].fillna(0)
# Fame score: sitelinks dominate; catalogue depth breaks ties among unmatched artists.
artists["fame"] = artists["sitelinks"] + np.log1p(artists["rap_songs"].fillna(0)) / 10

# ---------------------------------------------------------------------------------------
tot = con.sql(f"select tag, year, songs, artists from 'derived/genre_year_totals.parquet' where year between {Y0} and {Y1}").df()
tg = con.sql(f"select tag, year, term_id, songs, artists from 'derived/term_genre_year.parquet' where year between {Y0} and {Y1}").df()
tg = tg.merge(tot, on=["tag", "year"], suffixes=("", "_tot"))
tg["pct"] = tg.artists / tg.artists_tot * 100


def curve(term_id, genre):
    d = tg[(tg.term_id == term_id) & (tg.tag == genre)].set_index("year")
    raw = d.pct.reindex(YEARS, fill_value=0.0)
    n = d.artists.reindex(YEARS, fill_value=0)
    smooth = raw.rolling(3, center=True, min_periods=1).mean()
    return raw, smooth, n


def rise_year(smooth, n, frac, min_n=5):
    """Start of the run-up to the peak: walking back from the peak year, the first year of
    the unbroken stretch where the smoothed rate stays >= `frac` of the peak.

    Walking back (rather than scanning forward from 1988) ignores older senses of a
    spelling and early small-sample noise: "drip" was about water long before 2017.
    Returns None when the peak year itself has fewer than `min_n` users (too thin to call).
    """
    peak = smooth.max()
    if peak <= 0:
        return None
    py = y = int(smooth.idxmax())
    # Measure the climb above the word's pre-peak low, so an older sense that kept a
    # spelling in steady use ("drip" as water) doesn't count as part of the rise.
    base = float(smooth[:py].min())
    level = base + frac * (peak - base)
    while y - 1 >= Y0 and smooth[y - 1] >= level:
        y -= 1
    return y if n[py] >= min_n else None


def r3(x):
    return [round(float(v), 3) for v in x]


# ---------------------------------------------------------------------------------------
# Song-level rap usage, for first users / popularisers / signature artists.
uses = con.sql("""
  select t.term_id, s.artist_id, s.year, s.title
  from 'derived/song_terms.parquet' t join 'derived/song_index.parquet' s using (song_key)
  where s.tag = 'rap'
""").df()
uses = uses.join(artists[["artist", "fame", "sitelinks", "in_wikidata", "rap_songs", "region", "scene"]], on="artist_id")
# Misdated-song guard: Genius sometimes carries a wrong year (a 2012 mixtape track tagged
# 1986). A song dated more than 3 years before the artist's 10th-percentile year is not
# trusted for "who used it first".
p10 = con.sql("select artist_id, quantile_cont(year, 0.1) as p10 from 'derived/song_index.parquet' where tag='rap' group by 1").df().set_index("artist_id")
uses = uses.join(p10, on="artist_id")
uses = uses[uses.year >= uses.p10 - 3]
# "Notable" = has a Wikipedia presence, or a Wikidata entry plus a real catalogue (some acts,
# like Lil Jon & The East Side Boyz, sit on a Wikidata item with no Wikipedia links of its own).
# Battle-rap leagues post hundreds of battles under one account; they aren't artists.
uses["notable"] = (((uses.sitelinks > 0) | (uses.in_wikidata.fillna(False).astype(bool) & (uses.rap_songs >= 20)))
                   & ~uses.artist.str.contains(r"Don't Flop|Rap Battles|Rap League|URLtv|King of the Dot", regex=True))

region_totals = None
if artists.region.notna().any():
    # active mapped artists per region per year, the denominator for regional curves
    ay = con.sql("select distinct artist_id, year from 'derived/song_index.parquet' where tag='rap'").df()
    ay = ay.join(artists[["region"]], on="artist_id").dropna(subset=["region"])
    region_totals = ay.groupby(["region", "year"]).size()
    region_artist_totals = ay.groupby("region").artist_id.nunique()


def people(term_id, emerged, takeoff, poly):
    """Early adopters, popularisers and signature artists for one term.

    early        notable artists whose first use falls in the run-up window
                 [emerged - 3, takeoff], earliest first
    popularizers the most famous artists using it just before and at take-off
    signature    artists with a Wikipedia presence who used it in the most songs
    """
    u = uses[uses.term_id == term_id]
    if u.empty:
        return {}
    by_year = u.groupby("year").artist_id.nunique()
    out = dict(first_seen=int(by_year[by_year >= 2].index.min()) if (by_year >= 2).any() else int(u.year.min()))
    if takeoff:
        # Early adopters used it in the run-up AND came back to it (2+ songs by take-off + 2),
        # which drops one-off uses of an older meaning.
        # Spellings with an older meaning get a tighter window and need more repeat use, so
        # a stray old-sense mention ("drip" as water) doesn't read as early adoption.
        lo = (emerged or takeoff) - (1 if poly else 3)
        min_uses = 3 if poly else 2
        run = u[u.notable & (u.year >= lo) & (u.year <= takeoff + 2)]
        per = run.groupby("artist_id").agg(n=("title", "size"), year=("year", "min"))
        keep = per[(per.n >= min_uses) & (per.year <= takeoff)]
        firsts = (run[run.artist_id.isin(keep.index)].join(keep.n, on="artist_id")
                  .sort_values(["year", "n"], ascending=[True, False]).drop_duplicates("artist_id"))
        out["early"] = [dict(artist=r.artist, year=int(r.year), title=r.title) for r in firsts.head(6).itertuples()]
        # Popularisers must be rappers (a real rap catalogue), not pop stars with one feature.
        w = u[(u.year >= takeoff - 2) & (u.year <= takeoff + 1) & u.notable & (u.rap_songs >= 25)]
        g = w.groupby("artist_id").agg(n=("title", "size"), year=("year", "min"), title=("title", "first"))
        g = g.join(artists[["artist", "sitelinks"]])
        # Reach (log Wikipedia sitelinks) times how often they used it around take-off: a
        # superstar's single passing mention shouldn't outrank the artists who drove it.
        g["score"] = np.log1p(g.sitelinks.clip(lower=1)) * g.n
        g = g.sort_values("score", ascending=False).head(5)
        out["popularizers"] = [dict(artist=r.artist, year=int(r.year), songs=int(r.n), title=r.title) for r in g.itertuples()]
    s = u[u.notable].groupby("artist_id").agg(n=("title", "size"), y0=("year", "min"), y1=("year", "max"))
    s = s.join(artists[["artist", "rap_songs"]])
    s = s[s.n >= 5]
    s["share"] = s.n / s.rap_songs
    s = s.sort_values("n", ascending=False).head(5)
    out["signature"] = [dict(artist=r.artist, songs=int(r.n), share=round(float(r.share), 3), years=[int(r.y0), int(r.y1)]) for r in s.itertuples()]
    return out


def regions(term_id):
    if region_totals is None:
        return None
    u = uses[(uses.term_id == term_id) & uses.region.notna()]
    ry = u.drop_duplicates(["artist_id", "year"]).groupby(["region", "year"]).size()
    out = {}
    for region in region_artist_totals.index:
        num = ry.get(region, pd.Series(dtype=float)).reindex(YEARS, fill_value=0)
        den = region_totals.get(region, pd.Series(dtype=float)).reindex(YEARS, fill_value=0)
        pct = (num / den.replace(0, np.nan) * 100).fillna(0)
        # share of the region's mapped artists who ever used the term
        ever = u[u.region == region].artist_id.nunique() / region_artist_totals[region] * 100
        out[region] = dict(curve=r3(pct.rolling(3, center=True, min_periods=1).mean()), ever=round(float(ever), 2),
                           artists=int(den.sum()))
    return out


# ---------------------------------------------------------------------------------------
profiles = []
for t in lex:
    tid = t["id"]
    raw, smooth, n = curve(tid, "rap")
    peak = float(smooth.max())
    if peak == 0:
        continue
    peak_year = int(smooth.idxmax())
    now = float(raw[[2020, 2021]].mean())
    emerged = rise_year(smooth, n, 0.10)
    takeoff = rise_year(smooth, n, 0.25)
    early_level = float(smooth[Y0:Y0 + 4].mean())
    if early_level >= 0.4 * peak:
        stage = "staple" if now >= 0.5 * peak else "faded"
    elif now >= 0.85 * peak and peak_year >= 2018:
        stage = "rising"
    elif now <= 0.35 * peak:
        stage = "faded"
    else:
        stage = "cooling"

    genres = {}
    cross = {}
    for g in GENRES[1:]:
        graw, gsmooth, gn = curve(tid, g)
        genres[g] = r3(gsmooth)
        gpeak = float(gsmooth.max())
        tg_year = rise_year(gsmooth, gn, 0.25) if gpeak >= 0.1 else None
        cross[g] = dict(
            peak=round(gpeak, 3),
            peak_year=int(gsmooth.idxmax()) if gpeak > 0 else None,
            takeoff=tg_year,
            lag=(tg_year - takeoff) if (tg_year and takeoff) else None,
            reach=round(gpeak / peak, 3),
        )

    profiles.append(dict(
        id=tid, label=t["label"], cat=t["cat"], gloss=t["gloss"], poly=t["poly"], region_hint=t["region"],
        rap=r3(smooth), rap_raw=r3(raw), rap_n=[int(v) for v in n],
        genres=genres,
        life=dict(peak_year=peak_year, peak=round(peak, 3), now=round(now, 3), emerged=emerged,
                  takeoff=takeoff, stage=stage, total_artists=int(uses[uses.term_id == tid].artist_id.nunique())),
        cross=cross,
        people=people(tid, emerged, takeoff, t["poly"]),
        regions=regions(tid),
    ))

json.dump(dict(years=YEARS, terms=profiles), open(f"{OUT}/terms.json", "w"), separators=(",", ":"), allow_nan=False)

meta = dict(
    years=YEARS,
    totals={g: dict(songs=[int(v) for v in tot[tot.tag == g].set_index("year").songs.reindex(YEARS, fill_value=0)],
                    artists=[int(v) for v in tot[tot.tag == g].set_index("year").artists.reindex(YEARS, fill_value=0)])
            for g in GENRES},
    rap_songs=int(con.sql("select count(*) from 'derived/song_index.parquet' where tag='rap'").fetchone()[0]),
    rap_artists=int(con.sql("select count(distinct artist_id) from 'derived/song_index.parquet' where tag='rap'").fetchone()[0]),
    all_songs=int(con.sql("select count(*) from 'derived/song_index.parquet'").fetchone()[0]),
    n_terms=len(profiles),
)
json.dump(meta, open(f"{OUT}/meta.json", "w"), separators=(",", ":"), allow_nan=False)
print(f"{len(profiles)} term profiles, terms.json {os.path.getsize(f'{OUT}/terms.json')/1e6:.2f} MB")
