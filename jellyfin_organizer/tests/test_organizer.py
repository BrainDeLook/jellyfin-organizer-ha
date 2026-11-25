from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from organizer import jobs_for, parse_name, releases_in


class NameTests(unittest.TestCase):
    def test_main_titles_and_numbering(self):
        examples = {
            "The.Last.of.Us.S02E03.1080p.WEB-DL.mkv": ("The Last of Us", None, 2, (3,)),
            "Severance.1x02.1080p.mkv": ("Severance", None, 1, (2,)),
            "The_Show_S02E03_1080p.mkv": ("The Show", None, 2, (3,)),
            "Человек-паук.Нет.пути.домой.2021.BDRip.1080p.mkv":
                ("Человек-паук Нет пути домой", 2021, None, ()),
            "1917.2019.2160p.BluRay.mkv": ("1917", 2019, None, ()),
            "Chernobyl.S01E01-E02.1080p.mkv": ("Chernobyl", None, 1, (1, 2)),
            "3 Body Problem (2024) S01E01.mkv": ("3 Body Problem", 2024, 1, (1,)),
            "[Group] Frieren - 06 [1080p].mkv": ("Frieren", None, None, (6,)),
            "[Release] Мой сериал [Сезон 2] [03].mkv": ("Мой сериал", None, 2, (3,)),
            "Мой сериал.2 сезон.серия 03.1080p.mkv": ("Мой сериал", None, 2, (3,)),
            "Tate no Yuusha no Nariagari TV-4 01.mkv": ("Tate no Yuusha no Nariagari", None, 4, (1,)),
            "Anime.4th.Season.02.1080p.mkv": ("Anime", None, 4, (2,)),
            "Anime [TV-4] [03].mkv": ("Anime", None, 4, (3,)),
            "Anime TV-4 01–02 [1080p].mkv": ("Anime", None, 4, (1, 2)),
            "Anime Season 4 Episode 03.mkv": ("Anime", None, 4, (3,)),
            "Anime Episode 03 Season 4.mkv": ("Anime", None, 4, (3,)),
            "Anime 4-й сезон 13-я серия.mkv": ("Anime", None, 4, (13,)),
            "Anime 13-я серия 4-й сезон.mkv": ("Anime", None, 4, (13,)),
            "Anime Season 4 Episodes 03-04.mkv": ("Anime", None, 4, (3, 4)),
            "Halo 2 Season 1 Episode 3.mkv": ("Halo 2", None, 1, (3,)),
            "3 Body Problem (2024) Season 04 03.mkv": ("3 Body Problem", 2024, 4, (3,)),
            "Movie TV-4 (2020).mkv": ("Movie TV-4", 2020, None, ()),
            "Star Trek 4 2018.mkv": ("Star Trek 4", 2018, None, ()),
            "Anime TV-2024 01.mkv": ("Anime TV", 2024, None, (1,)),
        }
        for filename, expected in examples.items():
            with self.subTest(filename=filename):
                name = parse_name(filename)
                self.assertEqual((name.title, name.year, name.season, name.episodes), expected)

    def test_anime_tv_season_imports_without_review(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            release_name = "Tate no Yuusha no Nariagari TV-4"
            title = "Tate no Yuusha no Nariagari"
            folder = root / f"{release_name} [anidb2-18439]"
            folder.mkdir()
            for number in range(1, 13):
                (folder / f"{release_name} {number:02d}.mkv").write_bytes(b"dummy")
            release, = releases_in(root)
            self.assertEqual(release.title, title)
            self.assertEqual(release.warnings, ())
            self.assertEqual(release.hard_block, "")
            jobs = jobs_for(release, root / "Movies", root / "Shows", True)
            for number, job in enumerate(jobs, 1):
                self.assertEqual(job.target, root / "Shows" / title / "Season 04" / f"{title} - S04E{number:02d}.mkv")

    def test_season_conflicts_stay_in_review(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "Anime Season 04"
            folder.mkdir()
            (folder / "Anime TV-3 S02E01.mkv").write_bytes(b"dummy")
            release, = releases_in(root)
            self.assertIn("В одном имени указаны разные сезоны", release.warnings)
            self.assertIn("Номер сезона в имени отличается от папки", release.warnings)
            self.assertFalse(release.entries[0].name.season_inferred)
            self.assertTrue(parse_name("Anime S02E01 + S03E01.mkv").season_conflict)

    def test_unmarked_numbers_stay_in_review(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "Anime"
            folder.mkdir()
            (folder / "[Group] Anime - 101 [1080p].mkv").write_bytes(b"dummy")
            release, = releases_in(root)
            self.assertIn("Сезон не указан. В плане предложен сезон 01", release.warnings)
            self.assertTrue(release.entries[0].name.season_inferred)

    def test_pack_and_sidecars(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = (
                "My Show/Season 01/01.mkv",
                "My Show/Season 01/02.mkv",
                "My Show/Season 02/01.mkv",
                "My Show/Season 02/01.en.srt",
                "My Show/Озвучка/[AniDUB]/02.mka",
                "My Show/Extras/Creditless Opening.mkv",
                "1917.2019.2160p.BluRay.mkv",
                "Film.2020/Film.2020.1080p.mkv",
                "Film.2020/sample.mkv",
                "Film.2020/another-video.mkv.!qB",
                "Trailer.Park.Boys.The.Movie.2006.1080p.mkv",
                "BluRayRelease/BDMV/STREAM/00001.m2ts",
            )
            for name in files:
                path = root / name
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_bytes(b"dummy")
            releases = {release.title: release for release in releases_in(root)}
            self.assertEqual(set(releases), {"My Show", "1917", "Film", "Trailer Park Boys The Movie"})
            show = releases["My Show"]
            self.assertEqual(len(show.entries), 4)
            jobs = jobs_for(show, root / "Movies", root / "Shows", True)
            self.assertEqual([job.target.name for job in jobs],
                             ["My Show - S01E01.mkv", "My Show - S01E02.mkv",
                              "My Show - S02E01.mkv", "Creditless Opening.mkv"])
            self.assertEqual(jobs[1].entry.tracks[0].name, "AniDUB")
            self.assertEqual(jobs[2].entry.tracks[0].language, "eng")
            self.assertTrue(releases["Film"].hard_block)


if __name__ == "__main__":
    unittest.main()
