"""Working out a pack: one torrent or folder holding a whole franchise.

A pack like "Haikyuu!! (S01+02+03+04+Movies+OVAs+OADs)" has many seasons,
films and OVAs, but MyAnimeList keeps each of those as its own entry (and
sometimes splits one season in two: Haikyuu!! To the Top is two entries of
13 and 12 episodes, while the pack has one season 4 of 25). So a pack isn't
matched to one title: its files are grouped (each season, each film, the
OVAs, extras) and each group is matched to an entry of the franchise:

- seasons go to the franchise's TV entries in order (season 2 is the second
  one), carrying on into the next entry when a season has more episodes than
  its entry (part 2s), and episodes numbered from the start of the whole
  show are counted through the entries;
- a group whose name says which entry it is ("To the Top") goes there;
- films and OVAs go to the entries with the same name, or in order.

With an AI key, the AI looks at the same groups and entries (facts from
MyAnimeList) and its answer is used when it checks out. You see the result,
and can change any group, before anything is added.
"""

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai, library, metadata, scanner

KIND_OF_ENTRY = {"tv": "tv", "ona": "tv", "movie": "movie", "ova": "ova", "special": "ova",
                 "tv_special": "ova", "music": "other", "pv": "other", "cm": "other"}
NOT_A_SEASON = {"summary", "spin-off", "spin off", "alternative setting", "alternative version",
                "character", "other", "full story", "side story"}


def describe(files):
    """files: [{"key", "rel", "size"}] -> each with season, episodes, kind, guess."""
    out = []
    for f in files:
        season, eps = scanner.parse_episode(Path(f["rel"]).parts)
        v = library.Library.describe({"rel": f["rel"], "name": Path(f["rel"]).name, "size": f.get("size") or 0,
                                      "season": season, "episodes": list(eps)})
        if v.get("folder_season") and v["season"] is None:
            v["absolute"] = True
        out.append(dict(f, **{k: v.get(k) for k in ("season", "episodes", "kind", "guess", "full", "name_season",
                                                       "folder_season", "absolute", "name")}))
    return out


def _base(guess):
    """A title without its season words: "haikyuu second season" -> "haikyuu"."""
    text = re.sub(r"\b(?:" + "|".join(scanner._SEASON_WORDS) + r")\s+season\b", " ", guess or "")
    text, _ = library.split_season(" ".join(text.split()))
    return text.strip()


def groups_of(files, common=None):
    """The pack's files in groups: [{"id", "kind", "season", "label", "title",
    "distinct", "files": [keys], "episodes": {key: n}, "sample"}]. "distinct":
    the group has a name of its own ("To the Top"), not just the show's.
    common: the series' own title words from MyAnimeList ("haikyuu"); without
    them, the name most files share is taken as the show's."""
    files = describe(files)
    videos = [f for f in files if Path(f["rel"]).suffix.lower() in scanner.VIDEO_EXTS]
    main_titles = [_base(f["guess"]) for f in videos if f["kind"] == "episode" and f["guess"]]
    main = max(set(main_titles), key=main_titles.count) if main_titles else ""
    groups = {}

    def own_name(guess):
        """What a name says beyond the show's title ("to the top"), without numbers."""
        if common:
            # (A torrent of "To the Top" alone: every file says it, and it's still its own name.)
            return " ".join(sorted(_words(_base(guess), common)))
        rest = _base(guess)
        if main and (rest == main or rest.startswith(main + " ")):
            rest = rest[len(main):]
        return " ".join(w for w in rest.split() if not w.isdigit())

    def add(gid, kind, label, f, season=None, title="", distinct=False):
        g = groups.setdefault(gid, {"id": gid, "kind": kind, "season": season, "label": label, "title": title,
                                    "distinct": distinct, "files": [], "episodes": {}, "sample": []})
        g["files"].append(f["key"])
        if f["episodes"]:
            g["episodes"][f["key"]] = f["episodes"][0]
        if len(g["sample"]) < 3:
            g["sample"].append(Path(f["rel"]).name)

    for f in sorted(videos, key=lambda f: scanner.natural_key(f["rel"])):
        kind, guess = f["kind"], f["guess"] or ""
        extra_name = own_name(guess)
        if kind == "extra":
            add("extras", "extra", "Extras (openings, endings, trailers…)", f)
        elif kind == "movie":
            add(f"movie:{f['rel']}", "movie", f"Film: {Path(f['rel']).stem}", f, title=guess, distinct=True)
        elif kind in ("ova", "special") or f["season"] == 0:
            folder = str(Path(f["rel"]).parent)
            label = f"OVAs / specials ({Path(folder).name})" + (f": {extra_name}" if extra_name else "")
            add(f"ova:{folder}:{extra_name}", "ova", label, f, title=guess, distinct=bool(extra_name))
        else:
            season = f["season"] if f["season"] is not None else f.get("name_season")
            if season:
                gid, label = f"tv:{season}", f"Season {season}"
            elif extra_name:
                gid, label = f"tv:name:{extra_name}", f"Episodes: {guess}"
            else:
                gid, label = "tv:none", "Episodes"
            add(gid, "tv", label, f, season=season, title=guess, distinct=bool(extra_name))
            if extra_name:
                groups[gid]["distinct"] = True
                groups[gid]["title"] = guess
    order = {"tv": 0, "movie": 1, "ova": 2, "extra": 3}
    return sorted(groups.values(), key=lambda g: (order[g["kind"]], g["season"] or 99,
                                                  scanner.natural_key(g["sample"][0] if g["sample"] else "")))


def quick_own(files, item):
    """Straight away, before MyAnimeList's facts are in (no internet needed):
    which of a pack's video files are the title's own, going by their names.
    {key: episode or None} for those; files of the series' other seasons and
    titles ("Second Season", "To the Top", the films) are left out until the
    pack is worked out. None when the torrent looks like one title, or when
    the names can't tell."""
    groups = [g for g in groups_of(files) if g["kind"] != "extra"]
    if len(groups) < 2:
        return None
    names = [n for n in [item.get("title"), item.get("original_title"), *(item.get("aliases") or [])] if n]
    main = Counter(_base(g["title"]) for g in groups if g["kind"] == "tv" and g["title"]).most_common(1)
    main_words = set(library.norm(main[0][0]).split()) if main and main[0][0] else set()
    own = set()
    for n in names:
        own |= _words(_base(library.norm(n)), main_words)
    season = next((x for x in (entry_season({"names": [n]}) for n in names) if x), None) or 1
    kind = library.item_kind(item)
    me = {"names": names}
    chosen = []
    for g in groups:
        if g["kind"] == "tv" and kind == "tv":
            if g["distinct"]:
                if own and _name_score(g["title"], me, main_words) >= 60:
                    chosen.append(g)
            elif (g["season"] or 1) == season and (not own or season > 1):
                chosen.append(g)
        elif g["kind"] in ("movie", "ova") and kind in ("movie", "ova", "special"):
            if _name_score(g["title"], me, main_words) >= 60:
                chosen.append(g)
    if not chosen:
        return None
    out = {}
    for g in chosen:
        for k in g["files"]:
            out[k] = g["episodes"].get(k)
    return out


# ---- the franchise's entries

def entries(settings, mal_id):
    """The anime and everything connected to it on MyAnimeList, with each one's
    kind, episodes and start date: [{"source_id", "title", "names", "kind",
    "episodes", "date", "year", "relation"}], oldest first."""
    mal = metadata.MAL(settings["mal_client_id"])
    rows = [dict(r) for r in mal.franchise(mal_id)]
    try:
        anchor = mal._summary(mal._get(f"/anime/{int(mal_id)}", fields=metadata.MAL_SEARCH_FIELDS))
        anchor["relation"] = "This title"
        rows.append(anchor)
    except metadata.MetadataError:
        pass
    # Entries MyAnimeList only linked to (not looked up yet): fetch what they are.
    missing = [r for r in rows if not r.get("media_kind") or not r.get("date")]

    def fill(r):
        try:
            full = mal._summary(mal._get(f"/anime/{int(r['source_id'])}", fields=metadata.MAL_SEARCH_FIELDS))
            return dict(full, relation=r.get("relation"), via=r.get("via"))
        except metadata.MetadataError:
            return r

    if missing:
        with ThreadPoolExecutor(4) as pool:
            filled = dict(zip((r["source_id"] for r in missing), pool.map(fill, missing[:30])))
        rows = [filled.get(r["source_id"], r) for r in rows]
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (r.get("date") or "9999", r["title"])):
        if r["source_id"] in seen:
            continue
        seen.add(r["source_id"])
        out.append({"source_id": str(r["source_id"]), "title": r["title"],
                    "names": [n for n in [r["title"], r.get("original_title"), *(r.get("aliases") or [])] if n],
                    "kind": KIND_OF_ENTRY.get((r.get("media_kind") or "").lower(), "other"),
                    "media_type": r.get("media_type") or "", "episodes": r.get("episodes_total"),
                    "date": r.get("date") or "", "year": r.get("year"), "relation": r.get("relation") or "",
                    "poster": r.get("poster") or ""})
    return out


def _tv_chain(ents, anchor_id):
    """The franchise's seasons in order: TV entries that continue the story. ONAs
    count only when the franchise has no TV series of its own (a web series), or
    when they're the title itself."""
    story = [e for e in ents if e["kind"] == "tv" and
             (e["source_id"] == anchor_id or (e["relation"] or "").lower() not in NOT_A_SEASON)]
    tv = [e for e in story if (e.get("media_type") or "tv").lower() == "tv" or e["source_id"] == anchor_id]
    return tv or story


def entry_season(entry):
    """The season an entry's names say ("Second Season", "Haikyu!! 3rd Season"), or None."""
    for name in entry.get("names") or [entry.get("title") or ""]:
        n = library.norm(name)
        if re.search(r"\bpart\s*\d|\bcour\b", n):
            continue                     # "To the Top 2nd Season Part 2": says the part, not the season
        found = scanner.season_of(n)
        if found:
            return found
    return None


def _season_start(chain, season):
    """Where season N of a pack starts in the chain: the Nth TV entry, unless that
    entry's names say it's another season and another entry's say it's N."""
    i = min(season - 1, len(chain) - 1)
    says = entry_season(chain[i])
    if says and says != season:
        named = next((j for j, e in enumerate(chain) if entry_season(e) == season), None)
        if named is not None:
            return named, True
    return i, season - 1 < len(chain)


_NOT_A_NAME = library._STOP | {"movie", "movies", "film", "gekijouban", "ova", "ovas", "oad", "oads", "special",
                              "specials", "season", "part", "cour", "tv", "the", "vs"}


def _words(text, common=()):
    """The words that make a name its own: no show title, numbers or filler."""
    out = set()
    for w in library.norm(text).split():
        if w.isdigit() or w in _NOT_A_NAME or any(c.startswith(w) or w.startswith(c) for c in common):
            continue
        out.add(w)
    return out


def _common_words(ents):
    """The franchise's own title words ("haikyuu"): in most entries' names."""
    counts = {}
    for e in ents:
        for w in {w for n in e["names"] for w in library.norm(n).split()}:
            counts[w] = counts.get(w, 0) + 1
    return {w for w, n in counts.items() if n > len(ents) / 2 and not w.isdigit()}


def _name_score(group_title, entry, common=()):
    """0-100: how well what a group is called ("to the top") fits an entry's names."""
    mine = _words(group_title, common)
    if not mine:
        return 0
    best = 0
    for name in entry["names"]:
        theirs = _words(name, common)
        if theirs:
            best = max(best, round(200 * len(mine & theirs) / (len(mine) + len(theirs))))
    return best


def _spread(eps, chain, start, absolute=False):
    """{key: (entry index, episode)}: a season's episodes from chain[start] on,
    carrying on into the next entries when one runs out."""
    out = {}
    if not eps:
        return out
    first = min(eps.values())
    offset = 0
    if absolute or (chain[start].get("episodes") and first > chain[start]["episodes"]):
        # Numbered from the start of the whole show ("Season 2/26"): count through
        # the entries before this one. Otherwise ("Season 4/14" of 13 + 12) the
        # season carries on into the next entry, below.
        before = sum(e.get("episodes") or 0 for e in chain[:start])
        if before and first > before:
            offset = before
    for key, n in eps.items():
        i, left = start, n - offset
        while i < len(chain) - 1 and chain[i].get("episodes") and left > chain[i]["episodes"]:
            left -= chain[i]["episodes"]
            i += 1
        out[key] = (i, max(1, left))
    return out


def rule_plan(groups, ents, anchor_id):
    """Each group matched by the rules: {gid: {"parts": {key: (source_id, episode)},
    "why", "sure"}}."""
    chain = _tv_chain(ents, anchor_id)
    by_kind = {k: [e for e in ents if e["kind"] == k] for k in ("movie", "ova")}
    anchor_index = next((i for i, e in enumerate(chain) if e["source_id"] == anchor_id), 0)
    tv_groups = [g for g in groups if g["kind"] == "tv"]
    common = _common_words(ents)
    plan = {}
    for g in groups:
        parts, why, sure = {}, "", False
        if g["kind"] == "tv" and chain:
            named = max(((_name_score(g["title"], e, common), -i) for i, e in enumerate(chain)), default=(0, 0))
            named = (named[0], -named[1])    # the earliest entry of equally good ones: part 2s follow on
            if not g["distinct"]:
                named = (0, 0)               # just the show's name: says nothing about which season
            if g["season"]:
                if named[0] >= 60:
                    start, why, sure = named[1], f'its name matches {chain[named[1]]["title"]}', True
                else:
                    start, sure = _season_start(chain, g["season"])
                    why = (f"season {g['season']} is the franchise's TV entry number {g['season']}"
                           if start == g["season"] - 1 else f'{chain[start]["title"]} is season {g["season"]}')
            elif named[0] >= 60:
                start, why, sure = named[1], f'its name matches {chain[named[1]]["title"]}', True
            else:
                start = anchor_index if len(tv_groups) == 1 else 0
                why = "the episodes of the title you added it to"
                sure = len(tv_groups) == 1
            spread = _spread(g["episodes"], chain, start, absolute=False)
            parts = {k: (chain[i]["source_id"], n) for k, (i, n) in spread.items()}
            for k in g["files"]:
                parts.setdefault(k, (chain[start]["source_id"], None))
        elif g["kind"] in ("movie", "ova"):
            pool = by_kind[g["kind"]] + (by_kind["ova"] if g["kind"] == "movie" else by_kind["movie"])
            best = max(((_name_score(g["title"], e, common), e) for e in pool), default=(0, None), key=lambda x: x[0])
            if best[1] is not None and best[0] >= 50:
                parts = {k: (best[1]["source_id"], g["episodes"].get(k)) for k in g["files"]}
                why, sure = f'its name matches {best[1]["title"]}', best[0] >= 80
            elif g["kind"] == "movie":
                m = re.search(r"\b(?:movie|film|gekijouban)[ ._-]*(\d)\b", g["sample"][0] if g["sample"] else "", re.I)
                films = by_kind["movie"]
                if m and int(m.group(1)) <= len(films):
                    e = films[int(m.group(1)) - 1]
                    parts = {k: (e["source_id"], None) for k in g["files"]}
                    why = f"film {m.group(1)} in release order is {e['title']}"
            else:
                ovas = by_kind["ova"]
                if len(ovas) == 1:
                    parts = {k: (ovas[0]["source_id"], g["episodes"].get(k)) for k in g["files"]}
                    why = "the franchise's only OVA entry"
        plan[g["id"]] = {"parts": parts, "why": why or "no match - choose it yourself", "sure": sure}
    return plan


def ai_plan(settings, groups, ents):
    """The AI's matching for the same groups, checked: {gid: {"parts", "why"}}."""
    rows = [{"n": i, "title": e["title"], "kind": e["media_type"] or e["kind"], "episodes": e["episodes"],
             "year": e["year"], "relation": e["relation"]} for i, e in enumerate(ents)]
    gs = [{"id": g["id"], "kind": g["kind"], "season": g["season"], "label": g["label"],
           "episode_numbers": sorted(set(g["episodes"].values()))[:60], "files": len(g["files"]),
           "examples": g["sample"]} for g in groups if g["kind"] != "extra"]
    import json
    prompt = (
        "A downloaded pack of anime files has to be matched to MyAnimeList entries. MyAnimeList lists every "
        "season, part, film and OVA as its own entry (a season can be split into two entries, e.g. 13 + 12 "
        "episodes). These are the entries of the franchise, oldest first (facts from MyAnimeList):\n"
        f"{json.dumps(rows, ensure_ascii=False)}\n\nThese are the groups of files in the pack, with the episode "
        f"numbers in their file names:\n{json.dumps(gs, ensure_ascii=False)}\n\n"
        "For each group say which entry (by n) its files are, and how its episode numbers map onto that entry's "
        "episodes. When a group runs over more than one entry (a season split into parts, or episodes numbered "
        "from the start of the whole show), give one part per entry. A film or a single OVA has no episode "
        "numbers: give its entry with from_ep and to_ep null. If you can't tell, leave the group out.\n"
        'Reply as JSON: {"groups": [{"id": "...", "parts": [{"entry": n, "from_ep": a, "to_ep": b, '
        '"entry_first_episode": k}], "why": "short reason"}]}')
    reply, provider = ai.ask(settings, prompt, timeout=60, budget=120)
    if not isinstance(reply, dict):
        raise ai.AIError("the answer wasn't readable")
    by_id = {g["id"]: g for g in groups}
    out = {}
    for row in reply.get("groups") or []:
        g = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if not g:
            continue
        parts, ok = {}, True
        for p in row.get("parts") or []:
            n = p.get("entry") if isinstance(p, dict) else None
            if not isinstance(n, int) or not 0 <= n < len(ents):
                ok = False
                break
            e = ents[n]
            a, b, k = p.get("from_ep"), p.get("to_ep"), p.get("entry_first_episode")
            if a is None or not g["episodes"]:
                for key in g["files"]:
                    parts.setdefault(key, (e["source_id"], g["episodes"].get(key)))
                continue
            if not all(isinstance(x, int) for x in (a, b, k)) or a > b or k < 1:
                ok = False
                break
            for key, num in g["episodes"].items():
                if a <= num <= b:
                    ep = k + (num - a)
                    if e["episodes"] and ep > e["episodes"]:
                        ok = False
                    parts[key] = (e["source_id"], ep)
        if ok and parts and set(parts) >= set(g["episodes"]):
            for key in g["files"]:
                parts.setdefault(key, (next(iter(parts.values()))[0], None))
            out[g["id"]] = {"parts": parts, "why": str(row.get("why") or "")[:160]}
    return out, ai.NAMES[provider]


def plan(settings, mal_id, files, use_ai=True, ents=None):
    """Everything about a pack: {"groups": [...each with "parts": {key: [source_id,
    episode]}, "why", "sure", "by"], "entries": [...], "by": who matched it}.
    ents: the franchise's entries, when they're known already."""
    if ents is None:
        ents = entries(settings, mal_id) if settings.get("mal_client_id") else []
    groups = groups_of(files, _common_words(ents) if len(ents) > 1 else None)
    rules = rule_plan(groups, ents, str(mal_id))
    smart, who = {}, "the app's rules"
    if use_ai and ents and ai.available(settings) and any(g["kind"] != "extra" for g in groups):
        try:
            smart, name = ai_plan(settings, groups, ents)
            if smart:
                who = f"{name} (checked against MyAnimeList)"
        except ai.AIError:
            smart = {}
    for g in groups:
        chosen = smart.get(g["id"]) or rules[g["id"]]
        g["parts"] = {k: list(v) for k, v in chosen["parts"].items()}
        g["why"] = chosen["why"]
        g["by"] = "ai" if g["id"] in smart else "rules"
        g["sure"] = g["id"] in smart or rules[g["id"]]["sure"]
        targets = sorted({v[0] for v in g["parts"].values()}, key=lambda sid: next(
            (i for i, e in enumerate(ents) if e["source_id"] == sid), 0))
        g["targets"] = targets
    return {"groups": groups, "entries": ents, "by": who}


def regroup(group, ents, source_id, anchor_id):
    """A group you pointed at another entry: its files spread from there."""
    if source_id is None:
        return {k: [None, None] for k in group["files"]}
    chain = _tv_chain(ents, anchor_id)
    idx = next((i for i, e in enumerate(chain) if e["source_id"] == source_id), None)
    if group["kind"] == "tv" and idx is not None and group["episodes"]:
        spread = _spread(group["episodes"], chain, idx)
        parts = {k: [chain[i]["source_id"], n] for k, (i, n) in spread.items()}
    else:
        parts = {k: [source_id, group["episodes"].get(k)] for k in group["files"] if k in group["episodes"]}
    for k in group["files"]:
        parts.setdefault(k, [source_id, None])
    return parts
