from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import re


ANIDB_TAG = re.compile(r"[\[{(]anidb(?:2|id)?[ ._:-]+([1-9]\d{0,8})[\]})]", re.I)
ANIDB_SOURCE = re.compile(r"anidb:([1-9]\d{0,8}):R")
TMDB_DESTINATION = re.compile(r"tmdb_show:([1-9]\d{0,8}):s(\d{1,3})")
EPISODE_RANGE = re.compile(r"([1-9]\d{0,3})(?:-(\d{0,4}))?")


class AnimeMappingConflict(ValueError):
    pass


@dataclass(frozen=True)
class EpisodeMapping:
    tmdb_id: int
    season: int
    source_first: int
    source_last: int
    offset: int | None


@dataclass(frozen=True)
class AnimePlacement:
    anidb_id: int
    tmdb_id: int
    season: int
    episodes: dict[int, int]


AnimeMappings = dict[int, tuple[EpisodeMapping, ...]]


def load_anime_mappings(path: Path) -> AnimeMappings:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("Каталог AniBridge v3 должен содержать объект JSON.")
    mappings = {}
    for descriptor, targets in catalog.items():
        source = ANIDB_SOURCE.fullmatch(descriptor)
        if source is None or not isinstance(targets, dict):
            continue
        rows = []
        for destination, ranges in targets.items():
            target = TMDB_DESTINATION.fullmatch(destination)
            if target is None or not isinstance(ranges, dict):
                continue
            tmdb_id, season = map(int, target.groups())
            for source_range, target_range in ranges.items():
                source_bounds = EPISODE_RANGE.fullmatch(source_range)
                if source_bounds is None:
                    continue
                first = int(source_bounds[1])
                last = first if source_bounds[2] is None else int(source_bounds[2] or "9999")
                if last < first:
                    continue
                offset = None
                target_bounds = EPISODE_RANGE.fullmatch(target_range) if isinstance(target_range, str) else None
                if target_bounds:
                    target_first = int(target_bounds[1])
                    target_last = target_first if target_bounds[2] is None else int(target_bounds[2] or "9999")
                    if ((source_bounds[2] == "" and target_bounds[2] == "")
                            or (source_bounds[2] != "" and target_bounds[2] != ""
                                and target_last - target_first == last - first)):
                        offset = target_first - first
                rows.append(EpisodeMapping(tmdb_id, season, first, last, offset))
        if rows:
            mappings[int(source[1])] = tuple(rows)
    if not mappings:
        raise ValueError("В каталоге AniBridge v3 нет сопоставлений обычных эпизодов с TMDB.")
    return mappings


def lookup_anime_episodes(mappings: AnimeMappings, folder: str, filenames: Iterable[str],
                          episodes: Iterable[int]) -> AnimePlacement | None:
    folder_ids = ANIDB_TAG.findall(folder)
    file_ids = [ANIDB_TAG.findall(name) for name in filenames]
    ids = {int(value) for tags in (folder_ids, *file_ids) for value in tags}
    if len(ids) > 1:
        raise AnimeMappingConflict("В папке и именах файлов указаны разные идентификаторы AniDB.")
    if not ids or (not folder_ids and any(not tags for tags in file_ids)):
        return None

    anidb_id = ids.pop()
    rows = mappings.get(anidb_id, ())
    numbers = set(episodes)
    if not rows or not numbers:
        return None
    targets = set()
    placed = {}
    for episode in numbers:
        choices = {
            (row.tmdb_id, row.season, episode + row.offset if row.offset is not None else None)
            for row in rows if row.source_first <= episode <= row.source_last
        }
        if len(choices) > 1:
            raise AnimeMappingConflict("В каталоге AniBridge есть несколько вариантов нумерации одной серии.")
        if not choices:
            continue
        tmdb_id, season, target_episode = choices.pop()
        targets.add((tmdb_id, season))
        if target_episode is not None and 1 <= target_episode <= 9999:
            placed[episode] = target_episode
    if len(targets) > 1:
        raise AnimeMappingConflict("AniBridge сопоставляет серии релиза с разными сериалами или сезонами.")
    if numbers != placed.keys():
        return None
    if len(set(placed.values())) != len(placed):
        raise AnimeMappingConflict("По данным AniBridge разные файлы получили одинаковый номер серии.")
    tmdb_id, season = targets.pop()
    return AnimePlacement(anidb_id, tmdb_id, season, placed)
