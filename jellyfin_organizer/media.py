from contextlib import closing
from hashlib import sha256
from pathlib import Path
import ctypes
import errno
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from organizer import VIDEOS, fingerprint


FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")


def command(argv, timeout):
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    if result.returncode:
        raise ValueError((result.stderr or "Ошибка медиапроцессора").strip()[-700:])
    return result.stdout


def probe(path):
    return json.loads(command([FFPROBE, "-v", "error", "-protocol_whitelist", "file", "-show_streams", "-show_format",
                               "-show_chapters", "-of", "json", str(path)], 60))


def duration(media):
    value = media.get("format", {}).get("duration")
    return float(value) if value and value != "N/A" else None


def mux(job, output, preferred, keep_default, source):
    streams = source["streams"]
    source_duration = duration(source)
    inputs = []
    expected = []
    preferred_stream = None
    audio_count = sum(s["codec_type"] == "audio" for s in streams)
    subtitle_count = sum(s["codec_type"] == "subtitle" for s in streams)
    options = []
    warnings = []
    unused_paths = []
    for track in job.entry.tracks:
        info = probe(track.path)
        selected = [s for s in info["streams"] if s["codec_type"] == track.kind]
        if len(selected) != 1:
            unused_paths.append(str(track.path))
            warnings.append(f"{job.target.name}: {track.name} пропущена, в файле {len(selected)} подходящих дорожек.")
            continue
        stream = selected[0]
        track_duration = duration(info)
        if track.kind == "audio" and source_duration and track_duration and abs(track_duration - source_duration) > 5:
            unused_paths.append(str(track.path))
            warnings.append(f"{job.target.name}: {track.name} пропущена, длительность отличается на {abs(track_duration - source_duration):.1f} с.")
            continue
        if track.kind == "subtitle" and source_duration:
            inputs.extend(("-t", f"{source_duration:.3f}"))
        inputs.extend(("-protocol_whitelist", "file", "-i", str(track.path)))
        index = len(expected) + 1
        specifier = f"a:{audio_count}" if track.kind == "audio" else f"s:{subtitle_count}"
        language = track.language or stream.get("tags", {}).get("language", "und")
        if not re.fullmatch("[a-z]{3}", language, re.I):
            language = "und"
        flags = "forced" if track.forced or stream.get("disposition", {}).get("forced") else "0"
        if track.kind == "audio":
            if preferred and not keep_default and track.name.casefold() == preferred.casefold() and preferred_stream is None:
                preferred_stream = specifier
            audio_count += 1
        else:
            subtitle_count += 1
        codec = "subrip" if stream["codec_name"] == "mov_text" else stream["codec_name"]
        expected.append((index, stream["index"], specifier, codec, track.name, language, flags))
    for seq, stream in enumerate(s for s in streams if s["codec_type"] == "subtitle"):
        if stream["codec_name"] == "mov_text":
            options.extend((f"-c:s:{seq}", "srt"))

    maps = ["-map", "0"]
    for index, stream, specifier, codec, name, language, flags in expected:
        if specifier == preferred_stream:
            flags = "default" if flags == "0" else flags + "+default"
        maps.extend(("-map", f"{index}:{stream}"))
        options.extend((f"-metadata:s:{specifier}", f"language={language}", f"-metadata:s:{specifier}",
                        f"title={name}", f"-disposition:{specifier}", flags))
        if codec == "subrip" and specifier.startswith("s:"):
            # Copy ASS/SRT/etc.; mov_text is mapped to Matroska's equivalent subtitle format.
            options.extend((f"-c:{specifier}", "srt"))
    if preferred_stream:
        for seq, _ in enumerate(s for s in streams if s["codec_type"] == "audio"):
            options.extend((f"-disposition:a:{seq}", "-default"))

    command([FFMPEG, "-hide_banner", "-v", "error", "-xerror", "-nostdin", "-y",
             "-protocol_whitelist", "file", "-i", str(job.entry.source),
             *inputs, *maps, "-map_metadata", "0", "-map_chapters", "0", "-c", "copy", *options,
             "-f", "matroska", str(output)], 6 * 3600)
    verified = probe(output)
    result = verified["streams"]
    original_types = [(s["codec_type"], "subrip" if s["codec_name"] == "mov_text" else s["codec_name"]) for s in streams]
    output_types = [(s["codec_type"], s["codec_name"]) for s in result[:len(streams)]]
    if original_types != output_types or len(result) != len(streams) + len(expected):
        raise ValueError("Количество или кодеки дорожек в новом MKV отличаются от плана.")
    if len(source.get("chapters", [])) != len(verified.get("chapters", [])):
        raise ValueError("Количество глав в новом MKV отличается от исходника.")
    if source_duration and duration(verified) and abs(duration(verified) - source_duration) > 5:
        raise ValueError("Длительность нового MKV отличается от исходника.")
    for stream, (_, _, specifier, codec, name, language, flags) in zip(result[len(streams):], expected):
        tags = stream.get("tags", {})
        actual_language = tags.get("language", "und")
        if (stream["codec_name"] != codec or tags.get("title") != name or actual_language != language
                or bool(stream.get("disposition", {}).get("default")) != (specifier == preferred_stream)
                or bool(stream.get("disposition", {}).get("forced")) != (flags == "forced")):
            raise ValueError(f"Не подтверждены параметры дорожки {name}.")
    return warnings, unused_paths


def publish_no_replace(source, target):
    if sys.platform == "win32":
        source.rename(target)
        return
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        args = (ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(source)),
                ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(target)), ctypes.c_uint(1))
        # musl may provide the syscall without the libc wrapper.
        number = {"aarch64": 276, "x86_64": 316}.get(os.uname().machine)
        if rename or number is not None:
            result = rename(*args) if rename else libc.syscall(ctypes.c_long(number), *args)
            if result == 0:
                return
            error = ctypes.get_errno()
            if error not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise OSError(error, os.strerror(error), str(target))
    os.link(source, target)
    source.unlink()


def open_state(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS imports (target TEXT PRIMARY KEY, receipt TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS import_warnings (target TEXT PRIMARY KEY, warnings TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS cleanup_receipts (target TEXT PRIMARY KEY, details TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS cleanup_queue (id TEXT PRIMARY KEY, details TEXT NOT NULL)")
    return connection


def job_inputs(job):
    return {str(path): fingerprint(path) for path in (
        job.entry.source, *(track.path for track in job.entry.tracks)
    )}


def queue_cleanup(connection, jobs, roots, warnings):
    if not roots:
        return
    folder = next((root for root in roots if all(job.entry.source.is_relative_to(root) for job in jobs)), None)
    if folder is None:
        return
    inputs, outputs, skipped = {}, {}, set()
    for job in jobs:
        row = connection.execute("SELECT details FROM cleanup_receipts WHERE target=?", (str(job.target),)).fetchone()
        if row is None:
            warnings.append("Исходники сохранены: часть файлов импортирована прежней версией без отметок для очистки.")
            return
        record = json.loads(row[0])
        inputs.update(record["inputs"])
        outputs.update(record["outputs"])
        skipped.update(record["skipped"])
    for path in skipped:
        inputs.pop(path, None)
    if not inputs:
        return
    payload = json.dumps({"root": str(folder), "inputs": inputs, "outputs": outputs}, ensure_ascii=False, sort_keys=True)
    with connection:
        connection.execute("INSERT OR IGNORE INTO cleanup_queue VALUES (?, ?)",
                           (sha256(payload.encode()).hexdigest(), payload))


def cleanup_pending(database, roots):
    removed, warnings = 0, []
    allowed = {str(root) for root in roots}
    with closing(open_state(database)) as connection:
        rows = connection.execute("SELECT id, details FROM cleanup_queue").fetchall()
        for key, details in rows:
            batch = json.loads(details)
            if batch["root"] not in allowed:
                continue
            root = Path(batch["root"])
            if not root.is_dir() or root.resolve() != root:
                continue
            try:
                for name, expected in batch["outputs"].items():
                    path = Path(name)
                    if path.resolve() != path or fingerprint(path) != tuple(expected):
                        raise ValueError(f"Итоговый файл изменился: {path.name}")
                    with path.open("rb+" if os.name == "nt" else "rb") as output:
                        os.fsync(output.fileno())
                if os.name == "posix":
                    for folder in {Path(name).parent for name in batch["outputs"]}:
                        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
            except ValueError as exc:
                warnings.append(f"Очистка отменена. {exc}.")
                with connection:
                    connection.execute("DELETE FROM cleanup_queue WHERE id=?", (key,))
                continue
            except OSError as exc:
                warnings.append(f"Очистка отложена. Не удалось проверить или синхронизировать итоговые файлы: {exc}.")
                continue

            pending, parents = {}, set()
            for name, expected in batch["inputs"].items():
                path = Path(name)
                try:
                    if not path.is_relative_to(root) or path == root or path.resolve() != path:
                        warnings.append(f"Исходник сохранён: путь выходит за границы папки очистки ({path.name}).")
                        continue
                    if fingerprint(path) != tuple(expected):
                        warnings.append(f"Исходник сохранён: файл изменился после импорта ({path.name}).")
                        continue
                    path.unlink()
                    removed += 1
                    parents.update(parent for parent in path.parents if parent != root and parent.is_relative_to(root))
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    pending[name] = expected
                    warnings.append(f"Очистка отложена для {path.name}: {exc}.")
            for folder in sorted(parents, key=lambda path: len(path.parts), reverse=True):
                try:
                    if folder.resolve() == folder:
                        folder.rmdir()
                except OSError as exc:
                    if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                        warnings.append(f"Не удалось убрать пустую папку {folder.name}: {exc}.")
            with connection:
                if pending:
                    batch["inputs"] = pending
                    connection.execute("UPDATE cleanup_queue SET details=? WHERE id=?",
                                       (json.dumps(batch, ensure_ascii=False), key))
                else:
                    connection.execute("DELETE FROM cleanup_queue WHERE id=?", (key,))
    return removed, warnings


def import_jobs(jobs, config, database, report):
    warnings = []
    with closing(open_state(database)) as connection:
        for job in jobs:
            paths = [job.target]
            if not config.embed:
                for track in job.entry.tracks:
                    label = re.sub(r"[^-\w ]+", " ", track.name).strip()[:42]
                    language = f".{track.language}" if track.language else ""
                    forced = ".forced" if track.forced and track.kind == "subtitle" else ""
                    label = f".{label}" if label and label.casefold() not in {"default", "forced", "foreign", "sdh", "hi", "cc"} else ""
                    paths.append(job.target.with_name(f"{job.target.stem}{label}{language}{forced}{track.path.suffix.lower()}"))
            if len({str(path).casefold() for path in paths}) != len(paths):
                raise ValueError(f"Имена внешних дорожек совпадают: {job.target.name}.")
            if any(path.resolve() != path for path in paths):
                raise ValueError("Путь назначения проходит через символическую ссылку.")
            before = job_inputs(job)
            receipt = json.dumps({
                "inputs": before, "paths": [str(p) for p in paths],
                "embed": config.embed, "preferred": config.preferred, "keep_default": config.keep_default,
            }, ensure_ascii=False, sort_keys=True)
            previous = connection.execute("SELECT receipt FROM imports WHERE target=?", (str(job.target),)).fetchone()
            if previous and previous[0] == receipt and all(p.is_file() for p in paths):
                note = connection.execute("SELECT warnings FROM import_warnings WHERE target=?", (str(job.target),)).fetchone()
                if note:
                    warnings.extend(json.loads(note[0]))
                continue
            exists = [path for path in paths if os.path.lexists(path)]
            if job.target.parent.is_dir():
                exists.extend(p for p in job.target.parent.iterdir() if p.suffix.lower() in VIDEOS
                              and p.stem.casefold() == job.target.stem.casefold() and p != job.target)
            if exists:
                raise ValueError(f"Уже есть файл назначения: {exists[0].name}. Требуется проверка.")
            job.target.parent.mkdir(parents=True, exist_ok=True)
            report(f"Обработка {job.target.name}")
            source_info = probe(job.entry.source)
            if not any(s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
                       for s in source_info.get("streams", ())):
                raise ValueError(f"В исходном контейнере нет видео: {job.entry.source.name}.")
            with tempfile.TemporaryDirectory(prefix=".organizer-", dir=job.target.parent) as temporary:
                staged = [Path(temporary) / path.name for path in paths]
                job_warnings = []
                unused = []
                if config.embed and job.entry.tracks:
                    job_warnings, unused = mux(job, staged[0], config.preferred, config.keep_default, source_info)
                else:
                    for source, target in zip([job.entry.source, *(t.path for t in job.entry.tracks)], staged):
                        try:
                            os.link(source, target)
                        except OSError as error:
                            if error.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EOPNOTSUPP):
                                raise
                            shutil.copy2(source, target)
                if before != job_inputs(job) or not all(p.stat().st_size > 0 for p in staged):
                    raise ValueError("Входные файлы изменились во время импорта или результат пуст.")
                published = []
                try:
                    # Publish the video last, so Jellyfin sees any external tracks on its first scan.
                    for temp, final in zip(reversed(staged), reversed(paths)):
                        publish_no_replace(temp, final)
                        published.append(final)
                    with connection:
                        connection.execute("INSERT OR REPLACE INTO imports VALUES (?, ?)", (str(job.target), receipt))
                        connection.execute("INSERT OR REPLACE INTO import_warnings VALUES (?, ?)",
                                           (str(job.target), json.dumps(job_warnings, ensure_ascii=False)))
                        details = {"inputs": before, "outputs": {str(path): fingerprint(path) for path in paths},
                                   "skipped": unused}
                        connection.execute("INSERT OR REPLACE INTO cleanup_receipts VALUES (?, ?)",
                                           (str(job.target), json.dumps(details, ensure_ascii=False)))
                    warnings.extend(job_warnings)
                except (OSError, sqlite3.Error):
                    for final in published:
                        final.unlink()
                    raise
        if jobs:
            queue_cleanup(connection, jobs, config.cleanup_inputs, warnings)
    return warnings
