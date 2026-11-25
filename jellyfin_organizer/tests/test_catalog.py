from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from catalog import resolve_catalog
from web import Config, Organizer, render


class CatalogTests(TestCase):
    @patch("catalog.tmdb_request")
    def test_only_one_exact_movie_match_supplies_year(self, request):
        request.return_value = {"total_pages": 1, "results": [
            {"id": 1, "title": "Moon", "release_date": "2009-06-12"},
            {"id": 2, "title": "Moon 2", "release_date": "2020-01-02"},
        ]}
        query = ("movie", "Moon", None, ())
        self.assertEqual(resolve_catalog("token", query), 2009)
        request.return_value["results"][1]["title"] = "Moon"
        self.assertIsNone(resolve_catalog("token", query))

    @patch("catalog.tmdb_request")
    def test_single_season_requires_exact_episode_count(self, request):
        match = {"total_pages": 1, "results": [{"id": 1, "name": "Series"}]}
        season = {"season_number": 1, "episode_count": 3}
        query = ("show", "Series", None, (1, 2, 3))
        request.side_effect = [match, {"seasons": [season]}, match, {"seasons": [
            season, {"season_number": 2, "episode_count": 3},
        ]}]
        self.assertEqual(resolve_catalog("token", query), 1)
        self.assertIsNone(resolve_catalog("token", query))

    def test_ambiguous_release_uses_local_mapping_before_auto_import(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            inbox, movies, shows = (root / name for name in ("Cache", "Movies", "Shows"))
            release = inbox / "Shield [anidb2-18439]"
            release.mkdir(parents=True)
            movies.mkdir()
            shows.mkdir()
            for number in (1, 2, 3):
                (release / f"Shield - {number:02d}.mkv").write_bytes(b"video")
            mapping = root / "mapping.json"
            mapping.write_text('{"anidb:18439:R":{"tmdb_show:83095:s4":{"1-3":"3-5"}}}', encoding="utf-8")
            config = Config((inbox,), movies, shows, 60, 0, True, True, "", False, "", "",
                            anibridge_mappings=mapping)
            app = Organizer(config, root)
            with patch("media.probe", return_value={"streams": [{"codec_type": "video"}]}):
                app.scan(automatic=True)
            card, = app.cards.values()
            self.assertTrue(card.imported)
            self.assertIn("AniBridge", render(app))
            self.assertEqual(sorted(path.name for path in (shows / "Shield" / "Season 04").iterdir()),
                             ["Shield - S04E03.mkv", "Shield - S04E04.mkv", "Shield - S04E05.mkv"])

    def test_transient_import_error_is_visible_and_retried(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            inbox, movies, shows = (root / name for name in ("Cache", "Movies", "Shows"))
            for directory in inbox, movies, shows:
                directory.mkdir()
            (inbox / "Moon.2009.mkv").write_bytes(b"video")
            app = Organizer(Config((inbox,), movies, shows, 60, 0, True, True, "", False, "", ""), root)
            with patch("web.time.monotonic", return_value=100), patch("web.import_jobs", side_effect=OSError("Disk busy")):
                app.scan(automatic=True)
            self.assertIn("Disk busy", render(app))
            self.assertIn("Повторить импорт", render(app))
            with patch("web.time.monotonic", return_value=159), patch("web.import_jobs") as attempt:
                app.scan(automatic=True)
                attempt.assert_not_called()
            with patch("web.time.monotonic", return_value=160), patch("web.import_jobs", return_value=[]) as attempt:
                app.scan(automatic=True)
                attempt.assert_called_once()
            self.assertFalse(app.auto_failed)
