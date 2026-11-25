from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from media import cleanup_pending, import_jobs, open_state
from organizer import Entry, Job, Name, Track
from web import Config


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.inbox = root / "Cache"
        self.shows = root / "Shows"
        self.inbox.mkdir()
        self.shows.mkdir()
        self.database = root / "imports.sqlite"
        self.release = self.inbox / "Series"
        self.release.mkdir()
        self.config = Config((self.inbox,), root / "Movies", self.shows, 60, 90,
                             True, True, "AniDUB", False, "", "", cleanup_inputs=(self.inbox,))
        patched = patch("media.probe", return_value={"streams": [{"codec_type": "video"}]})
        patched.start()
        self.addCleanup(patched.stop)

    def job(self, episode, tracks=()):
        name = f"Series - S01E{episode:02d}.mkv"
        original = self.release / name
        original.write_bytes(b"fake video")
        return Job(Entry(original, Name("Series", None, 1, (episode,), True), tracks),
                   self.shows / "Series" / "Season 01" / name)

    def run_import(self, jobs):
        return import_jobs(jobs, self.config, self.database, lambda _: None)

    def test_cleanup_only_verified_media_and_empty_children(self):
        job = self.job(1)
        unrelated = self.release / "cover.jpg"
        unrelated.write_bytes(b"poster")
        second_job = self.job(2)
        self.assertEqual(self.run_import([job, second_job]), [])
        self.assertTrue(job.entry.source.exists())
        self.assertEqual(cleanup_pending(self.database, ()), (0, []))
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (2, []))
        self.assertFalse(job.entry.source.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(self.inbox.exists())
        self.assertEqual(job.target.read_bytes(), b"fake video")
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (0, []))

    def test_conflict_delays_cleanup_for_all_episodes(self):
        first, second = self.job(1), self.job(2)
        second.target.parent.mkdir(parents=True)
        second.target.write_bytes(b"conflicting file")
        with self.assertRaisesRegex(ValueError, "Уже есть файл"):
            self.run_import([first, second])
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (0, []))
        self.assertTrue(first.entry.source.exists())
        second.target.unlink()
        self.run_import([first, second])
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (2, []))
        self.assertFalse(self.release.exists())

    def test_replaced_output_blocks_whole_batch(self):
        first, second = self.job(1), self.job(2)
        self.run_import([first, second])
        second.target.unlink()
        second.target.write_bytes(b"replacement")
        removed, notes = cleanup_pending(self.database, (self.inbox,))
        self.assertEqual(removed, 0)
        self.assertIn("Итоговый файл изменился", notes[0])
        self.assertTrue(first.entry.source.exists())
        self.assertTrue(second.entry.source.exists())

    def test_skipped_track_stays_in_inbox(self):
        audio = self.release / "track.mka"
        audio.write_bytes(b"long alternate audio")
        job = self.job(1, (Track(audio, "audio", "Alternative", "rus", False),))

        def mux(_, destination, *_args):
            destination.write_bytes(b"new video")
            return ["Skipped"], [str(audio)]

        with patch("media.mux", side_effect=mux):
            self.assertEqual(self.run_import([job]), ["Skipped"])
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (1, []))
        self.assertTrue(audio.exists())
        self.assertTrue(job.target.exists())

    def test_retries_only_files_it_could_not_remove(self):
        first, second = self.job(1), self.job(2)
        self.run_import([first, second])
        unlink = Path.unlink

        def blocked(path, *args, **kwargs):
            if path == second.entry.source:
                raise PermissionError("busy")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", blocked):
            removed, notes = cleanup_pending(self.database, (self.inbox,))
        self.assertEqual(removed, 1)
        self.assertIn("Очистка отложена", notes[0])
        self.assertTrue(second.entry.source.exists())
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (1, []))
        self.assertFalse(self.release.exists())

    def test_existing_import_without_cleanup_receipt_is_preserved(self):
        job = self.job(1)
        self.run_import([job])
        with closing(open_state(self.database)) as connection, connection:
            connection.execute("DELETE FROM cleanup_receipts")
            connection.execute("DELETE FROM cleanup_queue")
        notes = self.run_import([job])
        self.assertIn("прежней версией", notes[0])
        self.assertEqual(cleanup_pending(self.database, (self.inbox,)), (0, []))
        self.assertTrue(job.entry.source.exists())


if __name__ == "__main__":
    unittest.main()
