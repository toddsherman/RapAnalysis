# The life cycle of rap slang

When does a word arrive in rap, who carries it, where does it come from, how long does it
last, and how long until pop says it? This repo tracks 243 slang terms across roughly
960,000 rap lyrics (1988–2021) and compares them with pop, R&B, rock and country.

**Live at [todd.sh/RapAnalysis](https://www.todd.sh/RapAnalysis).**

## What's here

| Path | What it is |
| --- | --- |
| `site/` | The static site: `index.html`, `app.js` (D3 from a CDN, no build step), `styles.css`, and the JSON it renders in `site/data/`. Deployed on Vercel with `site` as the root directory. |
| `lexicon/terms.py` | The curated lexicon: each term's spellings, phrase patterns, category, gloss, and whether the spelling also has an older standard meaning. |
| `lexicon/blocklist.py` | Slurs that are never shown in data-driven word lists. |
| `scripts/` | The pipeline, numbered in run order (below). |

## Data

Lyrics come from [genius-lyrics-cleaned](https://huggingface.co/datasets/theelderemo/genius-lyrics-cleaned)
(Christopher Dickinson, MIT license), an English-only, deduplicated cut of the Kaggle
[Genius Song Lyrics with Language Information](https://www.kaggle.com/datasets/carlosgdcj/genius-song-lyrics-with-language-information)
dataset. It isn't included here. Download the Parquet shards into `data/`:

```bash
huggingface-cli download theelderemo/genius-lyrics-cleaned --repo-type dataset --local-dir .
```

Artist origins and a fame proxy (Wikipedia sitelinks) come from [Wikidata](https://www.wikidata.org),
with MusicBrainz as a fallback.

No lyrics are published: the site shows only counts, artist names, song titles, and single
context words.

## Pipeline

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/01_build_term_counts.py   # tokenise every song; word x year x genre counts (~4 min)
.venv/bin/python scripts/02_discover_slang.py      # data-driven slang candidates and their life cycles
.venv/bin/python scripts/03_artist_regions.py      # artist -> region / scene / fame via Wikidata + MusicBrainz
.venv/bin/python scripts/04_term_matches.py        # match the curated lexicon against every song (~1 min)
.venv/bin/python scripts/05_term_profiles.py       # curves, life-cycle stage, crossover, early adopters -> site/data/terms.json
.venv/bin/python scripts/06_turnover.py            # is slang turning over faster? -> site/data/turnover.json
.venv/bin/python scripts/07_meaning_shift.py       # cap / drip / ice / gas senses by era -> site/data/meaning.json
.venv/bin/python scripts/08_regions.py             # regional vocabularies and diffusion -> site/data/regions.json
```

## Method notes

- **Adoption** is the share of a genre's active artists that year who used a term in at
  least one song, smoothed over three years. Counting artists instead of songs keeps one
  prolific rapper from manufacturing a trend.
- **Take-off** and **emergence** are found by walking back from a term's peak to the start
  of its final climb (25% and 10% of peak), so older meanings of the same spelling
  don't drag the dates back.
- **Genius dates** are sometimes wrong. For "who used it first", songs dated more than three
  years before the artist's 10th-percentile year are ignored, and early adopters must have
  used the term at least twice.
- **Genius skews recent** and includes many amateur uploads. The turnover analysis
  uses a fixed pool of established artists: those with a Wikipedia presence or at least 20
  rap songs.
- **Word senses** (cap, drip, ice, gas) come from transparent keyword rules applied to
  the 8 words on either side of each use. Uses with no clear signal are reported as unclear.
