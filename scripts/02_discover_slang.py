"""Find rap slang candidates from the data and describe each one's life cycle.

For every word we compute, per year, the share of active rap artists who used it at least
once (adoption rate). Artist-level adoption is used instead of song counts so one prolific
artist (Lil B has 1,286 songs) cannot manufacture a trend on their own.

Outputs (in derived/):
  rap_word_year.parquet   word x year: artists, songs, pct_artists, pct_songs, smoothed pct_artists
  term_summary.csv        one row per candidate word with life-cycle metrics and a category
"""
import duckdb
import numpy as np
import pandas as pd

Y0, Y1 = 1988, 2021          # 2022 is a partial scrape year; pre-1988 is too sparse
MIN_ARTIST_YEARS = 40         # word must be used by >= this many artist-years in total
SMOOTH = 3                    # centred rolling window (years)

con = duckdb.connect()
dictionary = set(w.strip().lower() for w in open("/usr/share/dict/words"))

tot = con.sql(f"""
  select year, artists as tot_artists, songs as tot_songs
  from 'derived/genre_year_totals.parquet' where tag='rap' and year between {Y0} and {Y1}
""").df().set_index("year").sort_index()

wy = con.sql(f"""
  select word, year, artists, songs
  from 'derived/genre_year_word.parquet'
  where tag='rap' and year between {Y0} and {Y1}
    and word in (select word from 'derived/genre_year_word.parquet' where tag='rap'
                 group by word having sum(artists) >= {MIN_ARTIST_YEARS})
""").df()
wy = wy.join(tot, on="year")
wy["pct_artists"] = wy.artists / wy.tot_artists * 100
wy["pct_songs"] = wy.songs / wy.tot_songs * 100

# Dense word x year grid so missing years count as zero before smoothing.
rates = wy.pivot(index="word", columns="year", values="pct_artists").reindex(columns=tot.index).fillna(0)
counts = wy.pivot(index="word", columns="year", values="artists").reindex(columns=tot.index).fillna(0)
smooth = rates.T.rolling(SMOOTH, center=True, min_periods=1).mean().T

long = smooth.stack().rename("pct_artists_smooth").reset_index()
wy = wy.merge(long, on=["word", "year"], how="right").fillna({"artists": 0, "songs": 0, "pct_artists": 0, "pct_songs": 0})
wy[["word", "year", "artists", "songs", "pct_artists", "pct_songs", "pct_artists_smooth"]].to_parquet("derived/rap_word_year.parquet", index=False)

# Rap specificity: how much more often a word appears in rap songs than in pop/rock/country
# songs from the same era (2000+, where every genre has solid coverage).
spec = con.sql("""
  with g as (
    select word, sum(songs) filter (where tag='rap') rap, sum(songs) filter (where tag in ('pop','rock','country')) other
    from 'derived/genre_year_word.parquet' where year >= 2000 group by word),
  t as (
    select sum(songs) filter (where tag='rap') rap_t, sum(songs) filter (where tag in ('pop','rock','country')) other_t
    from 'derived/genre_year_totals.parquet' where year >= 2000)
  select word, ln((coalesce(rap,0)+5)/rap_t) - ln((coalesce(other,0)+5)/other_t) as rap_log_ratio
  from g, t
""").df().set_index("word")

years = np.array(tot.index)
peak_year = smooth.idxmax(axis=1)
peak = smooth.max(axis=1)
now = rates[[2020, 2021]].mean(axis=1)
# Emergence: first year the smoothed rate reaches 10% of its peak with >= 5 real adopters.
reached = smooth.ge(0.10 * peak, axis=0) & counts.ge(5)
emerged = reached.idxmax(axis=1).where(reached.any(axis=1))
# How much it grew into its peak: peak vs the rate 8 years earlier.
before = pd.Series([smooth.loc[w, max(Y0, py - 8)] for w, py in peak_year.items()], index=smooth.index)

summary = pd.DataFrame({
    "emerged": emerged,
    "peak_year": peak_year,
    "peak_pct": peak.round(3),
    "pct_2020_21": now.round(3),
    "now_vs_peak": (now / peak).round(3),
    "rise_x": (peak / (before + 0.02)).round(1),
    "total_artist_years": counts.sum(axis=1).astype(int),
}).join(spec)
summary["in_dictionary"] = summary.index.isin(dictionary)

def category(r):
    if r.rap_log_ratio < 0.7 or r.peak_pct < 0.25 or r.name.isdigit():
        return None
    if r.peak_year <= 2016 and r.now_vs_peak <= 0.35 and r.rise_x >= 3:
        return "faded"
    if r.emerged >= 2008 and r.now_vs_peak >= 0.75 and r.rise_x >= 3:
        return "rising"
    if r.emerged <= 1998 and r.now_vs_peak >= 0.6:
        return "staple"
    return "other"

summary["category"] = summary.apply(category, axis=1)
summary = summary[summary.category.notna()].sort_values("peak_pct", ascending=False)
summary.index.name = "word"
summary.to_csv("derived/term_summary.csv")
print(summary.category.value_counts().to_string())
