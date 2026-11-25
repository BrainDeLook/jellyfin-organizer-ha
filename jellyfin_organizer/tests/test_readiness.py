from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from media import cleanup_pending
from organizer import InputChanged, release_activity_stamp, releases_in
from web import Config, Organizer


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.inbox = root / "Cache"
        self.release = self.inbox / "Example Series"
        self.release.mkdir(parents=True)
        movies, shows = root / "Movies", root / "Shows"
        movies.mkdir()
        shows.mkdir()
        self.video = self.release / "Example Series - S01E01.mkv"
        self.video.write_bytes(b"video")
        config = Config((self.inbox,), movies, shows, 60, 90, True, False, "", False, "", "",
                        cleanup_inputs=(self.inbox,), cleanup_whole_release=True)
        self.app = Organizer(config, root)

    def ready_card(self):
        with patch("web.time.monotonic", side_effect=[10, 100]):
            self.app.scan()
            self.app.scan()
        card = next(iter(self.app.cards.values()))
        self.assertTrue(card.ready)
        return card

    def test_hidden_partial_prevents_import(self):
        (self.release / ".Example Series - S01E02.mkv.!qB").write_bytes(b"partial")
        release = next(releases_in(self.inbox))
        self.assertIn("незавершённые файлы", release.hard_block)

    def test_shared_inbox_stamp_ignores_another_release_but_tracks_season_children(self):
        original = release_activity_stamp(self.inbox, shared=True)
        (self.release / "Example Series - S01E02.mkv").write_bytes(b"new episode")
        self.assertEqual(release_activity_stamp(self.inbox, shared=True), original)
        season = self.inbox / "Season 01"
        season.mkdir()
        (season / "Loose Series - S01E02.mkv").write_bytes(b"new loose episode")
        self.assertNotEqual(release_activity_stamp(self.inbox, shared=True), original)

    def test_partial_in_separate_subfolder_does_not_block_loose_media(self):
        (self.inbox / "Loose Series - S01E01.mkv").write_bytes(b"completed episode")
        unrelated = self.inbox / "Other Release"
        unrelated.mkdir()
        (unrelated / "Other Release - S01E01.mkv.!qB").write_bytes(b"partial")
        loose = next(release for release in releases_in(self.inbox) if release.folder == self.inbox)
        self.assertFalse(loose.hard_block)
        (self.inbox / "Loose Series - S01E02.mkv.!qB").write_bytes(b"partial")
        loose = next(release for release in releases_in(self.inbox) if release.folder == self.inbox)
        self.assertTrue(loose.hard_block)

    def test_sidecar_arriving_after_scan_restarts_stability_window(self):
        card = self.ready_card()
        subfolder = self.release / "Subs"
        subfolder.mkdir()
        (subfolder / "01.ru.srt").write_bytes(b"subtitle")
        with self.assertRaises(InputChanged):
            self.app.apply(card)
        with patch("web.time.monotonic", return_value=110):
            self.app.scan()
        self.assertFalse(self.app.cards[card.release.key].ready)
        self.assertFalse(card.jobs[0].target.exists())

    def test_episode_arriving_during_import_prevents_cleanup(self):
        card = self.ready_card()
        partial = self.release / "Example Series - S01E02.mkv.!qB"

        def probe(_):
            partial.write_bytes(b"partial new episode")
            return {"streams": [{"codec_type": "video"}]}

        with patch("media.probe", side_effect=probe), self.assertRaises(InputChanged):
            self.app.apply(card)
        self.assertTrue(card.jobs[0].target.is_file())
        self.assertTrue(self.video.is_file())
        self.assertTrue(partial.is_file())
        self.assertEqual(cleanup_pending(self.app.database, (self.inbox,), whole_release=True), (0, []))


if __name__ == "__main__":
    unittest.main()
