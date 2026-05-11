import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from debrid.realdebrid import FICHIER_PREPARED_INFLIGHT, RealDebrid
from metadata.tmdb import TMDB
from models.movie import Movie
from utils import bd
from utils.cache import cache
from utils.detection import (
    detect_languages,
    detect_quality,
    detect_quality_spec,
    post_process_results,
)
from utils.parse_config import parse_config
from utils.string_encoding import encodeb64


class FakeRealDebrid(RealDebrid):
    def __init__(self):
        super().__init__(
            {"debridKey": " rd-token ", "fichierApiKey": " fichier-token "},
            http_client=None,
            warp_client=None,
        )
        self.calls = []

    async def _post_1fichier(self, endpoint, payload, api_key):
        self.calls.append((endpoint, payload, api_key))
        if endpoint == "cp.cgi":
            to_url = "https://1fichier.com/?renamed" if "rename" in payload else "https://1fichier.com/?copied"
            return {
                "status": "OK",
                "urls": [{"from_url": payload["urls"][0], "to_url": to_url}],
            }
        if endpoint == "chattr.cgi":
            return {
                "status": "OK",
                "updated": 1,
                "urls": ["https://1fichier.com/?renamed"],
            }
        return {"status": "KO"}


class FakeFolderResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeFolderClient:
    def __init__(self, html):
        self.html = html
        self.get_calls = 0

    async def get(self, url, follow_redirects=False):
        self.get_calls += 1
        return FakeFolderResponse(self.html)


class FakeTMDBResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeTMDBClient:
    def __init__(self):
        self.get_calls = 0

    async def get(self, url):
        self.get_calls += 1
        return FakeTMDBResponse({
            "movie_results": [{
                "id": 123,
                "title": "Cached Movie",
                "release_date": "2026-01-01",
            }]
        })


class FakeDebridService:
    config = {}


class PerformanceOptimizationsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache.clear()
        FICHIER_PREPARED_INFLIGHT.clear()

    async def test_1fichier_preparation_is_cached_per_link_and_key(self):
        debrid = FakeRealDebrid()

        first = await debrid._prepare_1fichier_link("https://1fichier.com/?abc")
        second = await debrid._prepare_1fichier_link("https://1fichier.com/?abc")

        self.assertEqual(first, "https://1fichier.com/?renamed")
        self.assertEqual(second, "https://1fichier.com/?renamed")
        self.assertEqual([call[0] for call in debrid.calls], ["cp.cgi"])
        self.assertIn("rename", debrid.calls[0][1])
        self.assertEqual(debrid.calls[0][2], "fichier-token")
        self.assertEqual(debrid.headers["Authorization"], "Bearer rd-token")

    async def test_non_1fichier_link_is_not_prepared(self):
        debrid = FakeRealDebrid()

        result = await debrid._prepare_1fichier_link("https://example.com/file")

        self.assertEqual(result, "https://example.com/file")
        self.assertEqual(debrid.calls, [])

    async def test_random_1fichier_filename_is_letters_only_without_extension(self):
        debrid = FakeRealDebrid()

        filename = debrid._random_filename()

        self.assertEqual(len(filename), 16)
        self.assertTrue(filename.isalpha())

    async def test_concurrent_1fichier_preparation_is_coalesced(self):
        debrid = FakeRealDebrid()

        results = await asyncio.gather(*[
            debrid._prepare_1fichier_link("https://1fichier.com/?same")
            for _ in range(10)
        ])

        self.assertEqual(results, ["https://1fichier.com/?renamed"] * 10)
        self.assertEqual([call[0] for call in debrid.calls], ["cp.cgi"])

    async def test_1fichier_copy_rename_falls_back_to_chattr(self):
        class FallbackRealDebrid(FakeRealDebrid):
            async def _post_1fichier(self, endpoint, payload, api_key):
                if endpoint == "cp.cgi" and "rename" in payload:
                    self.calls.append((endpoint, payload, api_key))
                    return {"status": "KO"}
                return await super()._post_1fichier(endpoint, payload, api_key)

        debrid = FallbackRealDebrid()

        result = await debrid._prepare_1fichier_link("https://1fichier.com/?fallback")

        self.assertEqual(result, "https://1fichier.com/?renamed")
        self.assertEqual([call[0] for call in debrid.calls], ["cp.cgi", "cp.cgi", "chattr.cgi"])
        self.assertIn("rename", debrid.calls[0][1])
        self.assertNotIn("rename", debrid.calls[1][1])

    async def test_real_debrid_folder_listing_is_cached_per_service_instance(self):
        html = """
        <a href="../">Parent Directory</a>
        <a href="Movie%29abc">Movie)abc</a>
        <a href="Episode%29def">Episode)def</a>
        """
        client = FakeFolderClient(html)
        debrid = RealDebrid({"debridKey": "rd-token"}, client)

        first = await debrid.find_link_in_folder("https://my.real-debrid.com/user", "anything)abc")
        second = await debrid.find_link_in_folder("https://my.real-debrid.com/user", "anything)def")

        self.assertEqual(first, "https://my.real-debrid.com/user/links/Movie%29abc")
        self.assertEqual(second, "https://my.real-debrid.com/user/links/Episode%29def")
        self.assertEqual(client.get_calls, 1)

    async def test_tmdb_metadata_is_cached_in_memory(self):
        client = FakeTMDBClient()
        provider = TMDB({"tmdbApi": "tmdb-token"}, client)

        first = await provider.get_metadata("tt1234567", "movie")
        second = await provider.get_metadata("tt1234567", "movie")

        self.assertIs(first, second)
        self.assertEqual(first.titles, ["Cached Movie"])
        self.assertEqual(client.get_calls, 1)

    async def test_movie_search_results_are_cached_by_db_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "bd.tmp")
            connection = sqlite3.connect(db_path)
            cursor = connection.cursor()
            cursor.execute("CREATE TABLE enlaces_pelis (link TEXT, calidad TEXT, audio TEXT, info TEXT, tmdb TEXT)")
            cursor.execute(
                "INSERT INTO enlaces_pelis VALUES (?, ?, ?, ?, ?)",
                ("https://1fichier.com/?abc", "1080p", "SPANISH", "WEB-DL", "123"),
            )
            connection.commit()
            connection.close()

            with patch("utils.bd.DB_DECRYPTED_PATH", db_path):
                first = await bd.search_movies("123")

                with patch("utils.bd.get_cursor", side_effect=AssertionError("search cache miss")):
                    second = await bd.search_movies("123")

                connection = sqlite3.connect(db_path)
                cursor = connection.cursor()
                cursor.execute(
                    "INSERT INTO enlaces_pelis VALUES (?, ?, ?, ?, ?)",
                    ("https://1fichier.com/?def", "720p", "ENGLISH", "WEBRip", "123"),
                )
                connection.commit()
                connection.close()
                future = time.time() + 10
                os.utime(db_path, (future, future))

                refreshed = await bd.search_movies("123")

        self.assertEqual(first, [("https://1fichier.com/?abc", "1080p", "SPANISH", "WEB-DL")])
        self.assertEqual(second, first)
        self.assertEqual(len(refreshed), 2)

    async def test_duplicate_links_share_one_unrestrict_task_per_request(self):
        import config
        config.IS_DEV = True
        import main

        calls = 0

        async def fake_get_unrestricted_link(debrid_service, link):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {
                "download": "https://download",
                "filename": "Movie 1080p SPANISH WEB-DL",
                "filesize": 1024 ** 3,
            }

        request_cache = {}
        with patch("main._get_unrestricted_link", side_effect=fake_get_unrestricted_link):
            results = await asyncio.gather(*[
                main._process_single_link(
                    FakeDebridService(),
                    "https://host/file",
                    {},
                    "1080p",
                    "SPANISH",
                    "WEB-DL",
                    "('1080p', 'SPANISH', 'WEB-DL')",
                    request_cache,
                )
                for _ in range(5)
            ])

        self.assertEqual(calls, 1)
        self.assertEqual(len(results), 5)
        self.assertTrue(all(result[2] == "https://download" for result in results))

    async def test_stream_response_cache_key_tracks_db_version_and_fichier_status(self):
        import config
        config.IS_DEV = True
        import main

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "bd.tmp")
            with open(db_path, "w", encoding="utf-8") as db_file:
                db_file.write("one")

            with patch("main.DB_DECRYPTED_PATH", db_path):
                first = main._stream_response_cache_key("cfg", "movie", "tt1", "up")
                status_changed = main._stream_response_cache_key("cfg", "movie", "tt1", "down")

                future = time.time() + 10
                os.utime(db_path, (future, future))
                db_changed = main._stream_response_cache_key("cfg", "movie", "tt1", "up")

        self.assertNotEqual(first, status_changed)
        self.assertNotEqual(first, db_changed)

    async def test_stream_response_cache_hit_skips_metadata_and_debrid_work(self):
        import config
        config.IS_DEV = True
        import main

        encoded_config = encodeb64(
            '{"addonHost": "http://addon", "service": "realdebrid", "debridKey": "rd", '
            '"tmdbApi": "tmdb", "maxSize": "100", "selectedQualityExclusion": []}'
        )
        expected = {"streams": [{"name": "cached"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "bd.tmp")
            with open(db_path, "w", encoding="utf-8") as db_file:
                db_file.write("db")

            with patch("main.DB_DECRYPTED_PATH", db_path), patch("main.IS_DB_READY", True):
                cache_key = main._stream_response_cache_key(encoded_config, "movie", "tt1", "up")
                cache.set(cache_key, expected, ttl=60)

                with patch("main.TMDB", side_effect=AssertionError("stream cache miss")):
                    response = await main.get_results(encoded_config, "movie", "tt1.json")

        self.assertEqual(response, expected)

    def test_parse_config_is_cached(self):
        encoded = encodeb64('{"service": "realdebrid", "debridKey": "token"}')

        first = parse_config(encoded)
        with patch("utils.parse_config.decodeb64", side_effect=AssertionError("config cache miss")):
            second = parse_config(encoded)

        self.assertIs(first, second)
        self.assertEqual(first["service"], "realdebrid")


class MetadataReuseTest(unittest.TestCase):
    def test_post_process_reuses_loaded_metadata_without_db_query(self):
        media = Movie("1", ["Title"], "2026", [])
        metadata = "('1080p', 'SPANISH DDP5.1', 'WEB-DL')"
        result = {
            "filesize": 10,
            "quality": "1080p",
            "quality_spec": ["DDP", "WEBDL"],
            "languages": ["es"],
            "metadata_filename": metadata,
        }

        with patch("utils.detection.getMetadata") as get_metadata:
            processed = post_process_results("https://1fichier.com/?abc", media, "RealDebrid", "http://play", result)

        get_metadata.assert_not_called()
        self.assertEqual(processed["filename"], metadata)
        self.assertEqual(processed["languages"], ["es"])
        self.assertEqual(processed["quality_spec"], ["DDP", "WEBDL"])

    def test_post_process_keeps_db_fallback_when_metadata_was_not_preloaded(self):
        media = Movie("1", ["Title"], "2026", [])

        with patch("utils.detection.getMetadata", return_value="SPANISH 720p WEBRip") as get_metadata:
            processed = post_process_results("https://1fichier.com/?abc", media, "RealDebrid", "http://play", {})

        get_metadata.assert_called_once_with("https://1fichier.com/?abc", "movie")
        self.assertEqual(processed["filename"], "SPANISH 720p WEBRip")
        self.assertEqual(processed["languages"], ["es"])
        self.assertEqual(processed["quality_spec"], ["WEBRIP"])


class DetectionEquivalenceTest(unittest.TestCase):
    def test_detection_outputs_are_preserved_with_precompiled_regexes(self):
        sample = "Movie 2160p HDR DDP WEB-DL SPANISH MULTI"

        self.assertEqual(detect_quality(sample), "4k")
        self.assertEqual(detect_quality_spec(sample), ["HDR", "DDP", "WEBDL"])
        self.assertEqual(detect_languages(sample), ["es", "multi", "multi"])


if __name__ == "__main__":
    unittest.main()
