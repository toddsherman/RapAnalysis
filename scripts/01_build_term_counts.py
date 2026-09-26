"""Build the base term-count tables that every later analysis reads from.

Outputs (in derived/):
  artists.parquet                artist_id -> artist name
  songs.parquet                  one row per kept song: song_id, tag, artist_id, year, n_tokens
  rap_artist_year_word.parquet   (artist_id, year, word) -> number of that artist's rap songs that year using the word
  genre_year_word.parquet        (tag, year, word) -> songs / artists using the word, for every genre
  genre_year_totals.parquet      (tag, year) -> total songs / artists, the denominators for rates

"Using the word" means the word appears at least once in the song (document frequency),
so one song that repeats a hook 40 times counts once.

Usage: python scripts/01_build_term_counts.py [parquet glob]   (default: data/*.parquet)
"""
import sys
import time

import duckdb

SRC = sys.argv[1] if len(sys.argv) > 1 else "data/*.parquet"

MIN_YEAR, MAX_YEAR = 1979, 2022
# Genius's own translation / meta accounts, not real artists.
ACCOUNT_RE = r"(^(Rap )?Genius\b)|(\bGenius$)"
# A token is letters/digits with optional internal apostrophes (don't, y'all); a trailing
# apostrophe (gettin') is dropped so "gettin'" and "gettin" merge.
TOKEN_RE = r"[a-z0-9]+(?:''[a-z0-9]+)*"  # '' is an escaped quote inside the SQL literal
MIN_TOTAL_SONGS = 10  # drop words that appear in fewer than this many songs overall

con = duckdb.connect(".duckdb_tmp/build.duckdb")
con.sql("SET memory_limit='10GB'")
con.sql("SET temp_directory='.duckdb_tmp'")
con.sql("SET preserve_insertion_order=false")

t0 = time.time()
def log(msg):
    print(f"[{time.time()-t0:5.0f}s] {msg}", flush=True)

con.sql(f"""
CREATE OR REPLACE TABLE raw AS
SELECT tag, artist, year::SMALLINT AS year,
       -- strip [Verse 1: X] headers, normalise curly apostrophes, lowercase
       lower(replace(replace(regexp_replace(lyrics, '\\[[^\\]]*\\]', '', 'g'), '’', ''''), '‘', '''')) AS text
FROM read_parquet('{SRC}')
WHERE year BETWEEN {MIN_YEAR} AND {MAX_YEAR}
  AND NOT regexp_matches(artist, '{ACCOUNT_RE}')
""")
con.sql("CREATE OR REPLACE TABLE artists AS SELECT row_number() OVER (ORDER BY artist)::INTEGER AS artist_id, artist FROM (SELECT DISTINCT artist FROM raw)")
con.sql(f"""
CREATE OR REPLACE TABLE songs AS
SELECT row_number() OVER ()::INTEGER AS song_id, r.tag, a.artist_id, r.year,
       list_distinct(regexp_extract_all(r.text, '{TOKEN_RE}')) AS words,
       len(regexp_extract_all(r.text, '{TOKEN_RE}')) AS n_tokens
FROM raw r JOIN artists a USING (artist)
""")
con.sql("DROP TABLE raw")
log(f"songs tokenised: {con.sql('select count(*) from songs').fetchone()[0]:,}")

# One row per (song, distinct word).
con.sql("""
CREATE OR REPLACE TABLE song_words AS
SELECT song_id, tag, artist_id, year, unnest(words) AS word FROM songs
""")
log(f"song_words: {con.sql('select count(*) from song_words').fetchone()[0]:,} rows")

con.sql(f"CREATE OR REPLACE TABLE vocab AS SELECT word FROM song_words GROUP BY word HAVING count(*) >= {MIN_TOTAL_SONGS}")
log(f"vocab: {con.sql('select count(*) from vocab').fetchone()[0]:,} words")

con.sql("COPY artists TO 'derived/artists.parquet' (FORMAT parquet)")
con.sql("COPY (SELECT song_id, tag, artist_id, year, n_tokens FROM songs) TO 'derived/songs.parquet' (FORMAT parquet)")

con.sql("""
COPY (
  SELECT artist_id, year, word, count(*)::INTEGER AS songs
  FROM song_words SEMI JOIN vocab USING (word)
  WHERE tag = 'rap'
  GROUP BY ALL
) TO 'derived/rap_artist_year_word.parquet' (FORMAT parquet, COMPRESSION zstd)
""")
log("rap_artist_year_word written")

con.sql("""
COPY (
  SELECT tag, year, word, count(*)::INTEGER AS songs, count(DISTINCT artist_id)::INTEGER AS artists
  FROM song_words SEMI JOIN vocab USING (word)
  GROUP BY ALL
) TO 'derived/genre_year_word.parquet' (FORMAT parquet, COMPRESSION zstd)
""")
log("genre_year_word written")

con.sql("""
COPY (
  SELECT tag, year, count(*) AS songs, count(DISTINCT artist_id) AS artists
  FROM songs GROUP BY ALL
) TO 'derived/genre_year_totals.parquet' (FORMAT parquet)
""")
log("done")
