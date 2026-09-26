"""Match the curated lexicon (lexicon/terms.py) against every song in every genre.

Single-token spellings are matched against each song's distinct word set; multi-word
phrases are matched with one combined regex pass over the text. A song counts once per
term no matter how many times it uses it.

Outputs (in derived/):
  song_index.parquet        song_key, tag, artist_id, year, title  (every kept song)
  song_terms.parquet        song_key, term_id                      (one row per song x term)
  term_genre_year.parquet   tag, year, term_id -> songs, artists
  term_artist_year.parquet  artist_id, year, term_id -> songs      (rap only)
  lexicon.json              the lexicon, for the site

Also leaves `.duckdb_tmp/terms.duckdb` with a `song_text` table (normalised lyrics) that
the meaning-shift script reads.
"""
import json
import re
import sys
import time

import duckdb
import pandas as pd

sys.path.insert(0, "lexicon")
from terms import T  # noqa: E402

TOKEN_RE = r"[a-z0-9]+(?:''[a-z0-9]+)*"  # '' is an escaped quote inside the SQL literal


def wrap(p):
    """Word-bound a phrase pattern and let its spaces match any whitespace run."""
    p = p.replace(" ", r"\s+")
    tail = "" if p.endswith(("!", "$")) else r"\b"
    return rf"\b(?:{p}){tail}"


t0 = time.time()
def log(msg):
    print(f"[{time.time()-t0:5.0f}s] {msg}", flush=True)

con = duckdb.connect(".duckdb_tmp/terms.duckdb")
con.sql("SET memory_limit='10GB'")
con.sql("SET temp_directory='.duckdb_tmp'")
con.sql("SET preserve_insertion_order=false")

# song_key is stable across rebuilds: shard number * 1e6 + row within the shard.
# artists.parquet only holds real artists (Genius accounts were dropped in step 01).
con.sql(r"""
CREATE OR REPLACE TABLE song_text AS
SELECT (regexp_extract(p.filename, 'train-(\d+)-', 1)::BIGINT * 1000000 + p.file_row_number) AS song_key,
       p.tag, a.artist_id, p.year::SMALLINT AS year, p.title,
       lower(replace(replace(regexp_replace(p.lyrics, '\[[^\]]*\]', '', 'g'), '’', ''''), '‘', '''')) AS text
FROM read_parquet('data/*.parquet', filename=true, file_row_number=true) p
JOIN read_parquet('derived/artists.parquet') a USING (artist)
WHERE p.year BETWEEN 1979 AND 2022
""")
log(f"song_text: {con.sql('select count(*) from song_text').fetchone()[0]:,} songs")
con.sql("COPY (SELECT song_key, tag, artist_id, year, title FROM song_text) TO 'derived/song_index.parquet' (FORMAT parquet)")

# --- single tokens ---------------------------------------------------------------------
variant_map = pd.DataFrame([(w, t["id"]) for t in T for w in t["tokens"]], columns=["word", "term_id"]).drop_duplicates()
con.register("variant_map_df", variant_map)
con.sql(f"""
CREATE OR REPLACE TABLE token_hits AS
SELECT DISTINCT s.song_key, v.term_id
FROM (SELECT song_key, unnest(list_distinct(regexp_extract_all(text, '{TOKEN_RE}'))) AS word FROM song_text) s
JOIN variant_map_df v USING (word)
""")
log(f"token hits: {con.sql('select count(*) from token_hits').fetchone()[0]:,}")

# --- phrases ---------------------------------------------------------------------------
phrase_terms = [(t["id"], re.compile(wrap(p))) for t in T for p in t["phrases"]]
big = "|".join(f"(?:{wrap(p)})" for t in T for p in t["phrases"])
con.execute("""
CREATE OR REPLACE TABLE phrase_raw AS
SELECT song_key, unnest(list_distinct(regexp_extract_all(text, $re))) AS m
FROM song_text WHERE regexp_matches(text, $re)
""", {"re": big})
distinct_m = [r[0] for r in con.sql("SELECT DISTINCT m FROM phrase_raw").fetchall()]
m_map = pd.DataFrame(
    [(m, tid) for m in distinct_m for tid, rx in phrase_terms if rx.search(m)],
    columns=["m", "term_id"],
).drop_duplicates()
unmapped = set(distinct_m) - set(m_map.m)
if unmapped:
    log(f"WARNING {len(unmapped)} matched strings map to no term, e.g. {list(unmapped)[:5]}")
con.register("m_map_df", m_map)
con.sql("""
CREATE OR REPLACE TABLE song_terms AS
SELECT song_key, term_id FROM token_hits
UNION
SELECT DISTINCT r.song_key, m.term_id FROM phrase_raw r JOIN m_map_df m USING (m)
""")
log(f"song_terms: {con.sql('select count(*) from song_terms').fetchone()[0]:,} rows")

con.sql("COPY song_terms TO 'derived/song_terms.parquet' (FORMAT parquet)")
con.sql("""
COPY (
  SELECT s.tag, s.year, t.term_id, count(*)::INTEGER AS songs, count(DISTINCT s.artist_id)::INTEGER AS artists
  FROM song_terms t JOIN song_text s USING (song_key) GROUP BY ALL
) TO 'derived/term_genre_year.parquet' (FORMAT parquet)
""")
con.sql("""
COPY (
  SELECT s.artist_id, s.year, t.term_id, count(*)::INTEGER AS songs
  FROM song_terms t JOIN song_text s USING (song_key) WHERE s.tag = 'rap' GROUP BY ALL
) TO 'derived/term_artist_year.parquet' (FORMAT parquet)
""")
json.dump(T, open("derived/lexicon.json", "w"), indent=1)
log("done")
