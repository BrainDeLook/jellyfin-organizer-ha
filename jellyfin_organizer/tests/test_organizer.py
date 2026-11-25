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
        }
        for filename, expected in examples.items():
            with self.subTest(filename=filename):
                name = parse_name(filename)
                self.assertEqual((name.title, name.year, name.season, name.episodes), expected)

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
