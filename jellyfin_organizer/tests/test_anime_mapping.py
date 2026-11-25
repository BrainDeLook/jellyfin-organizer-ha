import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from anime_mapping import AnimeMappingConflict, load_anime_mappings, lookup_anime_episodes


class AnimeMappingTests(unittest.TestCase):
    def load(self, content):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "mappings.json"
            file.write_text(json.dumps(content), encoding="utf-8")
            return load_anime_mappings(file)

    def test_places_complete_pack_using_anidb_id(self):
        catalog = self.load({"anidb:18439:R": {
            "anilist:173780": {"1-12": "1-12"},
            "tmdb_show:83095:s4": {"1-12": "1-12"},
            "tvdb_show:353712:s4": {"1-12": "1-12"},
        }})
        placement = lookup_anime_episodes(catalog, "Series TV-4 [anidb2-18439]", ["Series 01.mkv"], range(1, 13))
        self.assertEqual((placement.anidb_id, placement.tmdb_id, placement.season), (18439, 83095, 4))
        self.assertEqual(placement.episodes, {number: number for number in range(1, 13)})

    def test_applies_offset_to_chosen_episode_subset(self):
        catalog = self.load({"anidb:345:R": {"tmdb_show:123:s2": {"1-": "13-"}}})
        placement = lookup_anime_episodes(catalog, "Series", ["Episode 02 [AniDB-345].mkv"], [2, 4, 9])
        self.assertEqual(placement.episodes, {2: 14, 4: 16, 9: 21})
        self.assertIsNone(lookup_anime_episodes(catalog, "Series", ["[AniDB-345] 02.mkv", "03.mkv"], [2, 3]))

    def test_reports_conflicting_ids_and_destinations(self):
        catalog = self.load({"anidb:345:R": {
            "tmdb_show:123:s2": {"1-12": "13-24"},
            "tmdb_show:123:s3": {"12-24": "1-13"},
        }})
        with self.assertRaises(AnimeMappingConflict):
            lookup_anime_episodes(catalog, "Series [anidb-345]", ["Series [anidb-346].mkv"], [1])
        with self.assertRaises(AnimeMappingConflict):
            lookup_anime_episodes(catalog, "Series [anidb-345]", [], [12])
        with self.assertRaises(AnimeMappingConflict):
            lookup_anime_episodes(catalog, "Series [anidb-345]", [], [11, 13])

    def test_refuses_split_ratio_and_partial_coverage(self):
        catalog = self.load({"anidb:345:R": {
            "tmdb_show:123:s2": {"1-2": "1-2,5-6|2", "3-5": "7-9"},
        }})
        for numbers in ([1], [2, 3], [3, 6]):
            with self.subTest(numbers=numbers):
                self.assertIsNone(lookup_anime_episodes(catalog, "Series [anidb-345]", [], numbers))
        self.assertEqual(lookup_anime_episodes(catalog, "Series [anidb-345]", [], [3]).episodes, {3: 7})

    def test_validates_catalog_structure(self):
        for invalid in ([], {}, {"anidb:123:R": {"tvdb_show:123:s1": {"1-12": "1-12"}}}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.load(invalid)
