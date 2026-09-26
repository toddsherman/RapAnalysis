"""How the meaning of a few polysemous rap words shifted over time.

For each target (cap, drip, ice, gas, plus a few collocate-only words) we pull every
occurrence in rap lyrics with a +/-8-word window, then:

  * senses    - classify each occurrence with transparent keyword rules on its window
                (first matching rule wins; anything unmatched is "unclear")
  * collocates - the context words most distinctive of each era versus the other eras,
                scored with the log-odds ratio with an informative Dirichlet prior
                (Monroe, Colaresi & Quinn 2008)

Only aggregate counts and single context words are exported, never lyric lines.

Reads .duckdb_tmp/terms.duckdb (song_text) built by 04_term_matches.py.
Output: site/data/meaning.json
"""
import json
import math
import re
import sys
from collections import Counter, defaultdict

import duckdb

sys.path.insert(0, "lexicon")
from blocklist import blocked  # noqa: E402

Y0, Y1 = 1988, 2021
ERAS = [("1988–1999", 1988, 1999), ("2000–2009", 2000, 2009), ("2010–2015", 2010, 2015), ("2016–2021", 2016, 2021)]
WIN = 8
WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")

# Sense rules are regexes applied to the window text (target included). Order matters.
TARGETS = {
    "cap": dict(
        forms=r"cap|caps|cappin|capping|capped|capper|cappers",
        senses=[
            ("lie", r"\bno caps?\b|\bcapp(?:in|ing|er|ers)\b|\b(?:that'?s|thats|stop|stop the|quit|talkin|talking|all|straight) cap\b"),
            ("shoot", r"\b(?:pop|popped|poppin|popping|bust|busted|bustin|buss|bussin|peel|peeled|put|pump|pumped|leave|left|push|pushed|twist|twisted|blow|blew)\b(?: \w+){0,2} caps?\b|\bcaps? (?:in|up in|into|peeled)\b|\bcapped\b"),
            ("hat", r"\b(?:fitted|fitteds|snapback|snapbacks|hat|hats|baseball|visor|new era|beanie|brim|bill)\b|\bcaps? (?:backwards?|to the back|turned|tilted|low|pulled)\b|\b(?:backwards?|tilted|turned) (?:cap|caps)\b"),
            ("other", r"\b(?:bottle|gown|salary|market|kneecap|ice cap|polar|mushroom|capital|captain)\b"),
        ]),
    "drip": dict(
        forms=r"drip|drips|drippin|dripping|drippy|dripped",
        senses=[
            ("style", r"\b(?:designer|fit|fits|outfit|clothes|fashion|swag|sauce|saucy|icy|jewelry|gucci|prada|louis|vuitton|versace|fendi|dior|balenciaga|amiri|margiela|givenchy|chanel|off white|rick|owens|vlone|bape|supreme|shoes|kicks|jeans|shirt|pants|jacket|coat|wearing|wear|dressed|fresh|flooded|splash|swagger|style|model|runway|brand|brands|label)\b|\b(?:my|his|her|your|ya|the|this|that|all this|all that|nothing but) drip\b|\bdrip (?:too hard|so hard|check|on|different|crazy|hard|too much)\b|\bdripp(?:ed|in|ing|y) (?:out|in|on|down)\b|\bdrippy\b"),
            ("liquid", r"\b(?:sweat|sweatin|sweating|blood|bleed|bleeding|water|wet|rain|faucet|sink|drops?|tears|leak|leaking|pour|pouring|juice|honey|syrup|paint|candy|lean|drank|cup|melt|melting|oil|gravy|butter|wax|sauce drip|ice cream|pussy|faucet|drip drop|iv)\b"),
        ]),
    "ice": dict(
        forms=r"ice|iced|icy|icey",
        senses=[
            ("name", r"\bice (?:cube|t|spice|mike|jj)\b|\bvanilla ice\b|\bice-t\b|\bthin ice\b"),
            ("jewelry", r"\b(?:chain|chains|wrist|neck|piece|pendant|rocks|diamond|diamonds|carat|carats|karat|karats|vvs|bling|shine|shinin|shining|glisten|rollie|rolex|watch|cartier|patek|grill|grills|teeth|ring|rings|earring|earrings|bracelet|flooded|jewel|jewels|jewelry|charm|cuban|bust down|busdown|drip|dripping|drippin|frozen|froze|flawless|clarity|bezel|jeweler)\b|\biced (?:out|up)\b|\bicy\b"),
            ("cold", r"\b(?:cold|freeze|freezing|snow|cube|cubes|water|drink|cup|glass|cooler|chill|chilly|winter|skate|skating|hockey|rink|ice cream|melt|melting|sheet|pick|box|tea|lemonade|henny|liquor|on ice|in my veins|veins|blood|heart)\b"),
        ]),
    "gas": dict(
        forms=r"gas|gassed|gassin|gassing|gasoline",
        senses=[
            ("poison", r"\b(?:chamber|chambers|mask|masks|tear gas|mustard|nerve|stove|oven|fumes|toxic|poison|lethal|holocaust)\b"),
            ("weed", r"\b(?:smoke|smokin|smoking|smoked|roll|rollin|rolled|pack|packs|pound|pounds|zip|zips|ounce|ounces|strain|weed|loud|za|zaza|kush|exotic|blunt|blunts|joint|joints|wood|woods|backwood|backwoods|smell|reek|stink|stinky|runtz|cookies|jar|dispensary|thc|pressure|dope|high|lungs|inhale|exhale|cloud|clouds|tree|trees|bud|buds|grams?|qp|qps|papers?|wraps?|light it|fire it)\b"),
            ("hype", r"\bgas(?:sed|sin|sing)?(?: \w+)? up\b|\bgassed\b|\b(?:that'?s|thats|straight|pure|all) gas\b|\bgas(?:sin|sing)\b"),
            ("fuel", r"\b(?:station|tank|pump|pedal|foot|hit the gas|on the gas|step on|mash|mileage|gallon|gallons|premium|unleaded|fill|filled|car|cars|whip|drive|drivin|driving|engine|brake|brakes|mph|speed|gasoline|fuel|pump|bill|bills|rent|light bill|money|prices?|charged|miles)\b"),
        ]),
    # collocate-only words (senses too diffuse for keyword rules)
    "sauce": dict(forms=r"sauce|saucy|saucin|saucing|sauced", senses=[]),
    "lit": dict(forms=r"lit|litty", senses=[]),
    "slime": dict(forms=r"slime|slimes|slimey|slimy", senses=[]),
    "stick": dict(forms=r"stick|sticks", senses=[]),
    "bag": dict(forms=r"bag|bags", senses=[]),
    "woke": dict(forms=r"woke", senses=[]),
}

STOP = set("""
a about above after again against all am an and any are aren't as at be because been before being below between both
but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further had
hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd
i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my myself no nor not of off on once only
or other ought our ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some such than
that that's the their theirs them themselves then there there's these they they'd they'll they're they've this those
through to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where
where's which while who who's whom why why's with won't would wouldn't you you'd you'll you're you've your yours
yourself yourselves shit bitch bitches fuck fuckin fucking fucked motherfucker motherfuckin motherfuckers damn ass hoe hoes pussy dick cunt penis semen
new big good look aye ey yah ye
im ima imma ain't aint got get gettin getting go gon gonna wanna gotta just like yeah yea uh oh ooh
ayy ay hey huh ya yo y'all em 'em cause 'cause cuz know man now one two back still even make made cause yuh la da na
bout 'bout tryna wit wanna lil real need say said take see come never ever every really thing things way whole
""".split())


def main():
    con = duckdb.connect(".duckdb_tmp/terms.duckdb", read_only=True)
    out = {"eras": [e[0] for e in ERAS], "years": list(range(Y0, Y1 + 1)), "targets": {}}
    for target, spec in TARGETS.items():
        form_re = re.compile(rf"^(?:{spec['forms']})$")
        senses = [(name, re.compile(rx)) for name, rx in spec["senses"]]
        rows = con.execute(
            f"select year, text from song_text where tag='rap' and year between {Y0} and {Y1} "
            f"and regexp_matches(text, $re)", {"re": rf"\b(?:{spec['forms']})\b"}).fetchall()
        sense_year = defaultdict(Counter)          # year -> sense -> occurrences
        ctx = {e[0]: Counter() for e in ERAS}      # era -> context word counts
        occ_era = Counter()
        for year, text in rows:
            era = next(e[0] for e in ERAS if e[1] <= year <= e[2])
            toks = WORD.findall(text)
            for i, w in enumerate(toks):
                if not form_re.match(w):
                    continue
                window = toks[max(0, i - WIN): i + WIN + 1]
                occ_era[era] += 1
                if senses:
                    wtxt = " ".join(window)
                    sense = next((name for name, rx in senses if rx.search(wtxt)), "unclear")
                    sense_year[year][sense] += 1
                for c in set(window):
                    if c != w and not form_re.match(c) and c not in STOP and not blocked(c) and not c.isdigit() and len(c) > 1:
                        ctx[era][c] += 1

        # Log-odds with informative Dirichlet prior: each era vs all other eras.
        total = Counter()
        for c in ctx.values():
            total.update(c)
        a0 = sum(total.values())
        colloc = {}
        for era, cnt in ctx.items():
            rest = total - cnt
            n1, n2 = sum(cnt.values()), sum(rest.values())
            scored = []
            for w, y1 in cnt.items():
                if y1 < 15:
                    continue
                aw = total[w] / a0 * 500  # prior strength
                y2 = rest[w]
                d = math.log((y1 + aw) / (n1 + 500 - y1 - aw)) - math.log((y2 + aw) / (n2 + 500 - y2 - aw))
                var = 1 / (y1 + aw) + 1 / (y2 + aw)
                scored.append((d / math.sqrt(var), w, y1))
            scored.sort(reverse=True)
            colloc[era] = [dict(w=w, n=n, z=round(z, 1)) for z, w, n in scored[:14]]

        entry = dict(occurrences={e: occ_era[e] for e in out["eras"]}, collocates=colloc)
        if senses:
            names = [n for n, _ in spec["senses"]] + ["unclear"]
            entry["senses"] = names
            entry["by_year"] = {n: [sense_year[y][n] for y in out["years"]] for n in names}
            entry["by_era"] = {e[0]: {n: sum(sense_year[y][n] for y in range(e[1], e[2] + 1)) for n in names} for e in ERAS}
        out["targets"][target] = entry
        print(target, dict(occ_era), {e: entry.get("by_era", {}).get(e) for e in out["eras"]} if senses else "", flush=True)

    json.dump(out, open("site/data/meaning.json", "w"), separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    main()
