from collections import defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import os
import re
import unicodedata

from guessit import guessit


VIDEOS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".wmv"}
AUDIO = {".mka", ".ac3", ".eac3", ".dts", ".aac", ".m4a", ".flac", ".opus", ".mp3", ".wav", ".ogg"}
SUBTITLES = {".ass", ".ssa", ".srt", ".vtt", ".sup"}
AUX_DIRS = {"audio", "dub", "dubs", "subs", "subtitles", "озвучка", "озвучки", "аудио", "субтитры"}
EXTRA_DIRS = {"nc", "extra", "extras", "bonus", "bonuses", "special features", "trailers", "featurettes", "бонусы"}
SKIP_DIRS = {"bdmv", "certificate", "video_ts", "audio_ts", "sample", "samples"}
TEAMS = ("AniDUB", "AniLibria", "AniPlay", "AOS", "StudioBand", "AniStar", "AniMedia", "AniMaunt", "LostFilm",
         "NewStudio", "HDRezka Studio", "Кураж-Бамбей", "Jaskier", "Red Head Sound", "TVShows")
LANGUAGES = {
    "ru": "rus", "rus": "rus", "russian": "rus", "русский": "rus",
    "en": "eng", "eng": "eng", "english": "eng",
    "ja": "jpn", "jpn": "jpn", "japanese": "jpn",
    "uk": "ukr", "ukr": "ukr", "ukrainian": "ukr",
    "fr": "fra", "fre": "fra", "fra": "fra", "french": "fra",
    "de": "deu", "ger": "deu", "deu": "deu", "german": "deu",
    "es": "spa", "spa": "spa", "spanish": "spa",
    "zh": "zho", "chi": "zho", "zho": "zho", "chinese": "zho",
    "ko": "kor", "kor": "kor", "korean": "kor",
    "it": "ita", "ita": "ita", "italian": "ita",
    "pt": "por", "por": "por", "portuguese": "por",
}
EPISODE = re.compile(r"(?<!\w)(?:s(\d{1,3})[ ._-]*e(\d{1,4})|(\d{1,2})x(\d{1,4}))(?!\d)", re.I)
SEASON = re.compile(r"^(?:season|сезон|s)[ ._-]*(\d{1,3})$", re.I)
SEASON_TAG = re.compile(r"(?<!\w)(?:season|сезон|s)[ ._-]*(\d{1,3})(?!\w)", re.I)
SEASON_REVERSE = re.compile(r"(?<!\w)(\d{1,3})[ ._-]+(?:season|сезон)(?!\w)", re.I)
EPISODE_TAG = re.compile(r"(?<!\w)(?:episode|ep|эпизод|серия)[ ._-]*(\d{1,4})(?!\w)", re.I)
DATED = re.compile(r"(?<!\d)(?:19|20)\d{2}[ ._-](?:0[1-9]|1[0-2])[ ._-](?:0[1-9]|[12]\d|3[01])(?!\d)")
NUMBERED = re.compile(r"(?:\[(\d{1,3})\]|(?:^| - )(\d{1,3})(?:v\d+)?(?=[ ._\[\(]|$))", re.I)
PACK = re.compile(r"(?<!\w)(?:complete|collection|pack|полный сезон|все серии)(?!\w)|\[\d{1,3}\s*-\s*\d{1,3}\]", re.I)
PART = re.compile(r"(?<!\w)(?:cd|disc|disk|диск|part|часть)[ ._-]*[1-9](?!\d)", re.I)
TECH = re.compile(r"(?<!\w)(?:[248]k|(?:480|540|576|720|1080|1440|2160|4320)[pi]|(?:uhd[ ._-]*)?blu[ ._-]*ray|"
                  r"bd[ ._-]*rip|br[ ._-]*rip|web[ ._-]*(?:dl|rip)|dvd[ ._-]*rip|"
                  r"hdtv|remux|x26[45]|h[ ._-]?26[45]|hevc|hdr10(?:\+)?|dolby[ ._-]*vision|av1)(?!\w)", re.I)
YEAR = re.compile(r"(?<!\d)(18[89]\d|19\d{2}|20\d{2})(?!\d)")
EXTRA = re.compile(r"(?<!\w)(ncop|nced|creditless[ ._-]*(?:opening|ending)|trailer|sample|трейлер)(?!\w)", re.I)
BROKEN = re.compile(r"(?:\.!qB|\.part|\.crdownload|\.aria2|\.tmp)$", re.I)
LEADING_GROUP = re.compile(r"^\[[^\]]+\]\s*(?=\S)")


@dataclass(frozen=True)
class Name:
    title: str
    year: int | None
    season: int | None
    episodes: tuple[int, ...]
    explicit: bool


@dataclass(frozen=True)
class Track:
    path: Path
    kind: str
    name: str
    language: str | None
    forced: bool


@dataclass(frozen=True)
class Entry:
    source: Path
    name: Name
    tracks: tuple[Track, ...]
    extra: str | None = None


@dataclass(frozen=True)
class Release:
    key: str
    folder: Path
    title: str
    year: int | None
    kind: str
    entries: tuple[Entry, ...]
    warnings: tuple[str, ...]
    hard_block: str = ""


@dataclass(frozen=True)
class Job:
    entry: Entry
    target: Path


def title_key(title):
    return "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())


def clean_title(value):
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", str(value))
    value = re.sub(r"\s+", " ", value).strip(" ._-[]()")
    if re.fullmatch(r"(?i)CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value):
        value = "_" + value
    while len(value.encode("utf-8")) > 140:
        value = value[:-1]
    return value.rstrip(" .")


@lru_cache(maxsize=4096)
def parse_name(value, is_file=True):
    stem = Path(value).stem if is_file else value
    stripped = LEADING_GROUP.sub("", stem).replace("_", " ")
    match = EPISODE.search(stripped)
    tech = TECH.search(stripped)
    guess = guessit(value, {"excludes": ["country"], "single_value": False})
    guessed_title = guess.get("title", "")
    if isinstance(guessed_title, list):
        guessed_title = guessed_title[0]
    year = guess.get("year")
    if not isinstance(year, int):
        year = None
    stop = match.start() if match else (tech.start() if tech else len(stripped))
    year_match = next((m for m in YEAR.finditer(stripped[:stop]) if int(m[0]) == year and m.start()), None)
    if year_match:
        stop = year_match.start()
    season_guess = guess.get("season")
    season_tag = None if match else SEASON_TAG.search(stripped) or SEASON_REVERSE.search(stripped)
    episode_tag = EPISODE_TAG.search(stripped) if is_file and not match else None
    date = DATED.search(stripped) if is_file and not match else None
    standalone_season = SEASON.fullmatch(stem)
    episodes = guess.get("episode", ())
    if isinstance(episodes, int):
        episodes = (episodes,)
    if not isinstance(episodes, (list, tuple)) or any(type(n) is not int or not 0 <= n <= 9999 for n in episodes):
        episodes = ()
    if match:
        season = int(match[1] or match[3])
        first = int(match[2] or match[4])
        episodes = tuple(episodes) if episodes and episodes[0] == first else (first,)
    else:
        season = int(season_tag[1]) if season_tag else season_guess if isinstance(season_guess, int) else None
    numbered = NUMBERED.search(stripped[:stop].rstrip())
    if not episodes and numbered and is_file:
        episodes = (int(numbered[1] or numbered[2]),)
    if episode_tag:
        episodes = (int(episode_tag[1]),)
        stop = min(stop, episode_tag.start())
    if date:
        stop = min(stop, date.start())
    if numbered and episodes and numbered.start() < stop and not match:
        stop = numbered.start()
    if season_tag and season_tag.start():
        stop = min(stop, season_tag.start())
    if not is_file and PACK.search(stripped):
        stop = min(stop, PACK.search(stripped).start())
    prefix = stripped[:stop].strip(" ._-[]()")
    if standalone_season or (is_file and episodes and (not prefix or re.fullmatch(r"\d{1,3}(?:v\d+)?", stem, re.I))):
        title = ""
    elif prefix and (stop < len(stripped) or match):
        title = clean_title(re.split(r"\s+[|=]\s+", prefix, maxsplit=1)[0].replace(".", " "))
    elif guessed_title:
        title = clean_title(guessed_title)
    else:
        title = ""
    if not title and not episodes and not standalone_season:
        title = clean_title(prefix.replace(".", " ").replace("_", " "))
    if title.isdecimal() and year == int(title):
        year = None
    return Name(title, year, season, tuple(episodes), bool(match or season_tag))


def scan_files(root):
    video_files, sidecars, unfinished = [], [], []
    def fail(error):
        raise error

    for directory, dirs, files in os.walk(root, onerror=fail):
        here = Path(directory)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.casefold() not in SKIP_DIRS
                   and not (here / d).is_symlink()]
        for name in files:
            path = here / name
            suffix = path.suffix.lower()
            partial = BROKEN.search(name)
            if name.startswith(".") or (not partial and suffix not in VIDEOS | AUDIO | SUBTITLES):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if partial or path.stat().st_size == 0:
                unfinished.append(path)
            elif suffix in VIDEOS:
                video_files.append(path)
            elif suffix in AUDIO or suffix in SUBTITLES:
                sidecars.append(path)
    return video_files, sidecars, unfinished


def track_info(path, source, folder):
    suffix = path.stem[len(source.stem):].strip(" ._-[]()") if path.stem.casefold().startswith(source.stem.casefold()) else ""
    parents = [part.strip("[]()") for part in path.parent.relative_to(folder).parts]
    labels = [part for part in parents + [suffix] if part and part.casefold() not in AUX_DIRS and not SEASON.fullmatch(part)]
    metadata = " ".join(labels)
    tokens = re.findall(r"[\w]+", metadata.casefold())
    team = next((t for t in TEAMS if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", metadata, re.I)), "")
    language = next((LANGUAGES[t] for t in reversed(tokens) if t in LANGUAGES), None)
    if team and not language:
        language = "rus"
    last_label = labels[-1] if labels else ""
    label = " ".join(v for v in re.split(r"[ ._]+", last_label) if v.casefold() not in LANGUAGES and v.casefold() not in {"forced", "foreign", "default"})
    forced = any(t in tokens for t in ("forced", "foreign"))
    kind = "audio" if path.suffix.lower() in AUDIO else "subtitle"
    return Track(path, kind, team or label or path.suffix[1:].upper(), language, forced)


def releases_in(root):
    if root.name.casefold() in SKIP_DIRS:
        return
    videos, aux, unfinished = scan_files(root)
    by_parent = {}
    extras = {}
    for video in sorted(videos):
        parents = video.parent.relative_to(root).parts
        extra_index = next((i for i, part in enumerate(parents) if part.casefold() in EXTRA_DIRS), None)
        detected_extra = EXTRA.search(video.stem)
        if re.search(r"(?:^|[._-])sample$", video.stem, re.I) and not EPISODE.search(video.stem):
            continue
        if extra_index is not None or (detected_extra and not EPISODE.search(video.stem)
                                        and (detected_extra[0].casefold() in {"ncop", "nced"} or
                                             re.search(r"(?:^|[._-])(?:trailer|трейлер)$", video.stem, re.I))):
            owner = root.joinpath(*parents[:extra_index]) if extra_index is not None else video.parent
            extras.setdefault(owner, []).append(video)
            continue
        if any(p.strip("[]").casefold() in AUX_DIRS for p in parents):
            continue
        parent = video.parent
        if parent != root and SEASON.fullmatch(parent.name):
            parent = parent.parent
        by_parent.setdefault(parent, []).append(video)

    def by_release(paths):
        index = defaultdict(list)
        for path in paths:
            for ancestor in path.parents:
                if ancestor in by_parent:
                    index[ancestor].append(path)
                    break
                if ancestor == root:
                    break
        return index

    auxiliary_by_release = by_release(aux)
    unfinished_by_release = by_release(unfinished)
    # Index name boundaries, then use episode-only sidecars within a release's track folders.
    for folder, source_files in by_parent.items():
        parsed = [(p, parse_name(p.name)) for p in source_files]
        if folder != root or len(by_parent) == 1:
            folder_name = parse_name(folder.name, False)
        else:
            folder_name = Name("", None, None, (), False)
        show_names = {title_key(n.title) for _, n in parsed if n.episodes and n.title}
        if len(show_names) <= 1 and any(n.episodes for _, n in parsed) and (
            folder != root or folder_name.title and any(title_key(n.title) == title_key(folder_name.title) for _, n in parsed)
        ):
            groups = [parsed]
        else:
            groups = []
            series = {}
            for pair in parsed:
                _, name = pair
                if name.episodes and name.title:
                    series.setdefault(title_key(name.title), []).append(pair)
                else:
                    groups.append([pair])
            groups.extend(series.values())

        video_parents = {v.parent for v in source_files}
        related = [p for p in auxiliary_by_release[folder] if (
            p.parent == folder or p.parent in video_parents
            or any(part.strip("[]").casefold() in AUX_DIRS for part in p.parent.relative_to(folder).parts)
            or folder != root
        )]
        indexed = {}
        for source in source_files:
            indexed.setdefault(source.stem.casefold().rstrip(), []).append(source)
        matches = {v: [] for v in source_files}
        unmatched = []
        for track in related:
            stamp = track.stem.casefold()
            candidates = indexed[stamp] if stamp in indexed else [
                candidate for m in re.finditer(r"[ ._-]", stamp) for candidate in indexed.get(stamp[:m.start()], ())
            ]
            if not candidates and track.parent != folder:
                track_name = parse_name(track.name)
                if track_name.episodes:
                    candidates = [v for v, n in parsed if n.episodes == track_name.episodes
                                  and (not track_name.title or title_key(track_name.title) == title_key(n.title))
                                  and (track_name.season is None or track_name.season == n.season)]
            if candidates:
                longest = max(len(p.stem) for p in candidates)
                closest = [p for p in candidates if len(p.stem) == longest and p.parent == track.parent]
                if not closest:
                    closest = [p for p in candidates if len(p.stem) == longest]
                if len(closest) == 1:
                    matches[closest[0]].append(track_info(track, closest[0], folder))
                    continue
            if any(part.strip("[]").casefold() in AUX_DIRS for part in track.parent.relative_to(folder).parts):
                unmatched.append(track.name)

        for group in groups:
            warnings = []
            entries = []
            kind = "show" if any(n.episodes for _, n in group) else "movie"
            group_titles = {title_key(n.title): n.title for _, n in group if n.title}
            single_title = next(iter(group_titles.values()), "")
            title = single_title or folder_name.title
            if len(groups) == 1 and folder_name.title:
                if not single_title or title_key(single_title) == title_key(folder_name.title):
                    title = folder_name.title
                elif title_key(single_title) in title_key(folder_name.title):
                    title = single_title
                elif kind == "show" or folder_name.year:
                    warnings.append("Имя видео отличается от названия папки")
            if len(group_titles) > 1:
                warnings.append("В одной папке обнаружены разные названия")
            years = {n.year for _, n in group if n.year}
            year = next(iter(years), None) or (folder_name.year if len(groups) == 1 else None)
            if len(years) > 1 or (years and len(groups) == 1 and folder_name.year and folder_name.year not in years):
                warnings.append("В именах указаны разные годы")
            if kind == "movie" and not year:
                warnings.append("Год фильма не определился")
            if not title:
                warnings.append("Основное название не определилось")
            elif title_key(title) in {"video", "movie", "фильм", "видео", "file", "unknown", "untitled"}:
                warnings.append("Название похоже на имя файла-заглушки")
            if kind == "movie" and any(PART.search(p.stem) for p, _ in group):
                warnings.append("Возможен фильм из нескольких частей")
            if kind == "movie" and any(DATED.search(p.stem) for p, _ in group):
                warnings.append("Дата в имени может обозначать выпуск сериала")
            if kind == "movie" and PACK.search(folder.name):
                warnings.append("Папка похожа на сборник")
            if len(groups) == 1 and unmatched:
                warnings.append(f"Не сопоставлены дорожки ({len(unmatched)}): " + ", ".join(unmatched[:3]))
            for path, name in group:
                parent_season = SEASON.fullmatch(path.parent.name)
                season = name.season
                if name.episodes:
                    from_folder = int(parent_season[1]) if parent_season else folder_name.season
                    if season is not None and from_folder is not None and season != from_folder:
                        warnings.append("Номер сезона в имени отличается от папки")
                    if season is not None and from_folder is None and not name.explicit:
                        warnings.append("Сезон выведен из неявного номера серии")
                    if season is None:
                        season = from_folder
                    if season is None:
                        warnings.append("Сезон не указан. В плане предложен сезон 01")
                        season = 1
                elif kind == "show":
                    warnings.append("Есть видео без номера серии")
                if BROKEN.search(path.name):
                    continue
                tracks = tuple(sorted(matches[path], key=lambda t: str(t.path).casefold()))
                if len({(t.kind, t.name.casefold(), t.language, t.forced, t.path.suffix.lower()) for t in tracks}) != len(tracks):
                    warnings.append("Найдены дорожки с одинаковыми названиями")
                entries.append(Entry(path, replace(name, season=season), tracks))
            entries.sort(key=lambda item: (item.name.season or 0, item.name.episodes or (99999,), item.source.name.casefold()))
            if len(groups) == 1:
                for path in extras.get(folder, []):
                    tags = EXTRA.search(path.stem)
                    labels = {"ncop": "Creditless Opening", "nced": "Creditless Ending",
                              "trailer": "Trailer", "трейлер": "Trailer"}
                    label = labels.get(tags[0].casefold(), path.stem) if tags else path.stem
                    entries.append(Entry(path, parse_name(path.name), (), clean_title(label)))
            hard_block = ""
            if unfinished_by_release[folder]:
                hard_block = "В папке присутствуют незавершённые файлы"
            key_source = group[0][0] if len(groups) > 1 or (len(group) == 1 and kind == "movie") else folder
            key = sha256(os.fsencode(key_source)).hexdigest()[:24]
            yield Release(key, folder, title, year, kind, tuple(entries), tuple(dict.fromkeys(warnings)), hard_block)


def jobs_for(release, movies, shows, embed, override=None):
    override = override or {}
    kind = override.get("kind", release.kind)
    title = clean_title(override.get("title", release.title))
    year = override.get("year", release.year)
    if not title:
        raise ValueError("Укажи название.")
    album = f"{title} ({year})" if year else title
    root = (shows if kind == "show" else movies) / album
    jobs = []
    episode_index = 0
    for entry in release.entries:
        if entry.extra:
            jobs.append(Job(entry, root / "extras" / f"{entry.extra}{entry.source.suffix.lower()}"))
            continue
        extension = ".mkv" if embed and entry.tracks else entry.source.suffix.lower()
        if kind == "movie":
            target = root / f"{album}{extension}"
        else:
            season = override.get("season", entry.name.season)
            episodes = (override["first"] + episode_index,) if "first" in override else entry.name.episodes
            if season is None or not episodes:
                raise ValueError("Укажи сезон и номер первой серии.")
            if episodes[-1] > 9999:
                raise ValueError("Номер серии превышает 9999.")
            if len(episodes) > 1 and tuple(range(episodes[0], episodes[-1] + 1)) != episodes:
                raise ValueError("Серии в одном файле должны идти подряд.")
            episode = f"S{season:02d}E{episodes[0]:02d}"
            if len(episodes) > 1:
                episode += f"-E{episodes[-1]:02d}"
            target = root / f"Season {season:02d}" / f"{album} - {episode}{extension}"
            episode_index += 1
        jobs.append(Job(entry, target))
    targets = [str(j.target).casefold() for j in jobs]
    if len(set(targets)) != len(targets):
        raise ValueError("Несколько файлов получают одинаковое имя. Укажи отдельные номера или разложи версии по разным входным папкам.")
    return jobs


def fingerprint(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def release_stamp(release):
    files = sorted({e.source for e in release.entries} | {t.path for e in release.entries for t in e.tracks})
    return tuple((str(path), fingerprint(path)) for path in files)
