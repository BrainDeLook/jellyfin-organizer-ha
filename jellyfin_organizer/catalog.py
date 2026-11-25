from dataclasses import replace
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json

from organizer import title_key


MISSING_SEASON = "Сезон не указан. В плане предложен сезон 01"
MISSING_YEAR = "Год фильма не определился"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tmdb_request(token, endpoint, params):
    request = Request("https://api.themoviedb.org/3/" + endpoint + "?" + urlencode(params),
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with build_opener(NoRedirect()).open(request, timeout=5) as response:
        payload = json.loads(response.read(1_000_000))
    if not isinstance(payload, dict):
        raise ValueError("TMDB вернул ответ неизвестного формата.")
    return payload


def unplaced_episodes(release):
    if release.kind == "show" and release.warnings == (MISSING_SEASON,):
        episodes = [entry.name for entry in release.entries if not entry.extra]
        if all(name.season_inferred for name in episodes):
            return tuple(sorted(e for name in episodes for e in name.episodes))
    return ()


def catalog_query(release, episodes):
    if release.warnings == (MISSING_YEAR,) and release.kind == "movie":
        return release.kind, release.title, release.year, ()
    if episodes and episodes == tuple(range(1, len(episodes) + 1)):
        return release.kind, release.title, release.year, episodes
    return None


def resolve_catalog(token, query):
    kind, title, year, episodes = query
    movie = kind == "movie"
    catalog_kind = "movie" if movie else "tv"
    name, date = ("title", "release_date") if movie else ("name", "first_air_date")
    language = "ru-RU" if any("а" <= letter <= "я" or letter == "ё" for letter in title.casefold()) else "en-US"
    matches = tmdb_request(token, f"search/{catalog_kind}", {"query": title, "language": language})
    if matches.get("total_pages") != 1:
        return None
    items = matches.get("results")
    if not isinstance(items, list):
        raise ValueError("TMDB вернул результаты неизвестного формата.")
    candidates = {}
    for item in items:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
            continue
        names = [item.get(name), item.get(f"original_{name}")]
        if any(isinstance(value, str) and title_key(value) == title_key(title) for value in names):
            released = item.get(date)
            found_year = int(released[:4]) if isinstance(released, str) and released[:4].isascii() and released[:4].isdecimal() else None
            if year is None or found_year == year:
                candidates[item["id"]] = found_year
    if len(candidates) != 1:
        return None
    identifier, found_year = next(iter(candidates.items()))
    if movie:
        return found_year if found_year and 1880 <= found_year <= 2099 else None
    details = tmdb_request(token, f"tv/{identifier}", {"language": language})
    seasons = details.get("seasons")
    if not isinstance(seasons, list):
        raise ValueError("TMDB вернул сезоны неизвестного формата.")
    regular = [s for s in seasons if isinstance(s, dict) and s.get("season_number") != 0]
    if len(regular) == 1 and regular[0].get("season_number") == 1 and regular[0].get("episode_count") == len(episodes):
        return 1
    return None


def apply_catalog(release, value, episodes=None):
    if release.kind == "movie":
        return replace(release, year=value, warnings=())
    entries = tuple(entry if entry.extra else replace(
        entry, name=replace(entry.name, season=value,
                            episodes=tuple(episodes[e] for e in entry.name.episodes) if episodes else entry.name.episodes))
                    for entry in release.entries)
    return replace(release, entries=entries, warnings=())
