"""Canned TMDB / MyAnimeList / Jikan replies, shaped like the real APIs."""

import re
import urllib.parse

TMDB_SEARCH_TV = {"results": [
    {"id": 1396, "name": "Breaking Bad", "original_name": "Breaking Bad", "first_air_date": "2008-01-20",
     "overview": "A chemistry teacher turned meth maker.", "poster_path": "/bb.jpg", "vote_average": 8.9,
     "vote_count": 15000, "genre_ids": [18, 80], "original_language": "en"},
]}

TMDB_TV = {
    "id": 1396, "name": "Breaking Bad", "original_name": "Breaking Bad", "first_air_date": "2008-01-20",
    "overview": "Walter White, a chemistry teacher, turns to crime.", "tagline": "Remember my name",
    "poster_path": "/bb.jpg", "backdrop_path": "/bb-back.jpg", "genres": [{"id": 18, "name": "Drama"}, {"id": 80, "name": "Crime"}],
    "vote_average": 8.91, "vote_count": 15000, "popularity": 400.5, "status": "Ended",
    "episode_run_time": [47], "number_of_episodes": 62, "networks": [{"name": "AMC"}],
    "seasons": [{"season_number": 0, "episode_count": 9}, {"season_number": 1, "episode_count": 7},
                {"season_number": 2, "episode_count": 13}],
    "credits": {"cast": [{"name": "Bryan Cranston"}, {"name": "Aaron Paul"}]},
    "videos": {"results": [
        {"site": "YouTube", "type": "Featurette", "key": "feat1234567", "official": True, "iso_639_1": "en"},
        {"site": "YouTube", "type": "Trailer", "key": "fan12345678", "official": False, "iso_639_1": "en"},
        {"site": "YouTube", "type": "Trailer", "key": "HhesaQXLuRY", "official": True, "iso_639_1": "en"},
    ]},
    "external_ids": {"imdb_id": "tt0903747"},
}

TMDB_SEASON = {"episodes": [
    {"episode_number": 1, "name": "Pilot", "overview": "Walt is diagnosed.", "air_date": "2008-01-20", "runtime": 58, "still_path": "/s1.jpg"},
    {"episode_number": 2, "name": "Cat's in the Bag...", "overview": "", "air_date": "2008-01-27", "runtime": 48, "still_path": None},
]}

TMDB_SEARCH_MOVIE = {"results": [
    {"id": 27205, "title": "Inception", "original_title": "Inception", "release_date": "2010-07-15",
     "overview": "A thief who steals secrets through dreams.", "poster_path": "/inc.jpg", "vote_average": 8.4, "vote_count": 30000},
]}

TMDB_MOVIE = {
    "id": 27205, "title": "Inception", "original_title": "Inception", "release_date": "2010-07-15",
    "overview": "Cobb steals secrets from dreams.", "tagline": "Your mind is the scene of the crime.",
    "poster_path": "/inc.jpg", "backdrop_path": "/inc-back.jpg", "genres": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}],
    "vote_average": 8.37, "vote_count": 30000, "popularity": 90.1, "runtime": 148, "revenue": 839030630,
    "status": "Released", "imdb_id": "tt1375666", "production_companies": [{"name": "Legendary Pictures"}],
    "credits": {"cast": [{"name": "Leonardo DiCaprio"}]},
    "videos": {"results": [{"site": "YouTube", "type": "Trailer", "key": "YoHD9XEInc0", "official": True, "iso_639_1": "en"}]},
}

TMDB_SEARCH_ANIME = {"results": [
    {"id": 209867, "name": "Frieren: Beyond Journey's End", "first_air_date": "2023-09-29", "genre_ids": [16, 10759],
     "original_language": "ja", "poster_path": "/fr.jpg", "vote_average": 8.8, "vote_count": 400},
]}

TMDB_ANIME_TV = dict(TMDB_TV, id=209867, name="Frieren: Beyond Journey's End", original_name="葬送のフリーレン",
                     backdrop_path="/frieren-back.jpg", genres=[{"id": 16, "name": "Animation"}],
                     credits={"cast": [{"name": "Atsumi Tanezaki"}]}, videos={"results": []})

PROVIDERS = {
    1396: {"results": {"PK": {"link": "https://www.themoviedb.org/tv/1396/watch?locale=PK",
                              "flatrate": [{"provider_id": 8, "provider_name": "Netflix", "logo_path": "/n.jpg"}]}}},
    27205: {"results": {"US": {"flatrate": [{"provider_id": 1899, "provider_name": "Max"}]},
                        "PK": {"rent": [{"provider_id": 2, "provider_name": "Apple TV"}]}}},
    209867: {"results": {"PK": {"flatrate": [{"provider_id": 119, "provider_name": "Amazon Prime Video"}]}}},
}

MAL_SEARCH = {"data": [{"node": {
    "id": 52991, "title": "Sousou no Frieren", "main_picture": {"medium": "https://cdn.myanimelist.net/m.jpg", "large": "https://cdn.myanimelist.net/l.jpg"},
    "alternative_titles": {"synonyms": ["Frieren at the Funeral"], "en": "Frieren: Beyond Journey's End", "ja": "葬送のフリーレン"},
    "start_date": "2023-09-29", "synopsis": "An elf mage outlives her party.\n\n[Written by MAL Rewrite]", "mean": 9.3,
    "media_type": "tv", "num_episodes": 28, "num_list_users": 1100000}}]}

MAL_DETAIL = dict(MAL_SEARCH["data"][0]["node"], rank=1, popularity=150, num_scoring_users=600000, status="finished_airing",
                  genres=[{"id": 2, "name": "Adventure"}, {"id": 8, "name": "Drama"}, {"id": 10, "name": "Fantasy"}],
                  average_episode_duration=1440, studios=[{"id": 11, "name": "Madhouse"}])

JIKAN = {"data": {"mal_id": 52991, "trailer": {"youtube_id": "qgQQQ7ZRrm4", "embed_url": "https://www.youtube.com/embed/qgQQQ7ZRrm4"}}}


def fake_get_json(url, headers=None, service="the server"):
    parts = urllib.parse.urlsplit(url)
    path = parts.path
    query = dict(urllib.parse.parse_qsl(parts.query))
    if parts.netloc == "api.themoviedb.org":
        if path == "/3/configuration":
            return {"images": {}}
        if path == "/3/search/tv":
            return TMDB_SEARCH_ANIME if "frieren" in query.get("query", "").lower() else TMDB_SEARCH_TV
        if path == "/3/search/movie":
            return TMDB_SEARCH_MOVIE
        if path == "/3/tv/1396":
            return TMDB_TV
        if path == "/3/tv/209867":
            return TMDB_ANIME_TV
        if path == "/3/movie/27205":
            return TMDB_MOVIE
        if re.fullmatch(r"/3/tv/1396/season/\d+", path):
            return TMDB_SEASON
        m = re.fullmatch(r"/3/(?:tv|movie)/(\d+)/watch/providers", path)
        if m:
            return PROVIDERS.get(int(m.group(1)), {"results": {}})
    if parts.netloc == "api.myanimelist.net":
        if path == "/v2/anime":
            return MAL_SEARCH
        if path == "/v2/anime/52991":
            return MAL_DETAIL
    if parts.netloc == "api.jikan.moe":
        return JIKAN
    raise AssertionError(f"unexpected request {url}")
