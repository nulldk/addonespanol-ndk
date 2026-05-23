import unittest
from unittest.mock import AsyncMock, patch

import httpx

from debrid.get_debrid_service import get_debrid_service
from debrid.torbox import TorBox


class TorBoxProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_unrestrict_link_creates_cached_webdl_and_requests_largest_file(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/v1/api/webdl/createwebdownload":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "download started",
                    "data": {"webdownload_id": 42},
                })
            if request.url.path == "/v1/api/webdl/mylist":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "web downloads list retrieved successfully",
                    "data": {
                        "id": 42,
                        "name": "folder-name",
                        "size": 30,
                        "download_present": True,
                        "download_finished": True,
                        "files": [
                            {"id": 1, "name": "small.mkv", "size": 10, "infected": False},
                            {"id": 7, "name": "movie.1080p.mkv", "size": 200, "infected": False},
                        ],
                    },
                })
            if request.url.path == "/v1/api/webdl/requestdl":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "Web download requested successfully.",
                    "data": "https://cdn.torbox.app/download/movie.1080p.mkv",
                })
            return httpx.Response(404, json={"success": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            torbox = TorBox({"debridKey": " tb-token "}, client)
            result = await torbox.unrestrict_link("https://host.example/file")

        self.assertEqual(result, {
            "download": "https://cdn.torbox.app/download/movie.1080p.mkv",
            "filename": "movie.1080p.mkv",
            "filesize": 200,
            "pending": False,
            "web_id": 42,
        })

        create_request = requests[0]
        self.assertEqual(create_request.headers["authorization"], "Bearer tb-token")
        self.assertIn(b"link=https%3A%2F%2Fhost.example%2Ffile", create_request.content)
        self.assertNotIn(b"add_only_if_cached", create_request.content)

        download_request = requests[2]
        self.assertEqual(download_request.url.params["token"], "tb-token")
        self.assertEqual(download_request.url.params["web_id"], "42")
        self.assertEqual(download_request.url.params["file_id"], "7")
        self.assertEqual(download_request.url.params["append_name"], "true")

    async def test_unrestrict_link_returns_none_when_torbox_rejects_link(self):
        def handler(request):
            return httpx.Response(400, json={
                "success": False,
                "error": "UNSUPPORTED_SITE",
                "detail": "The site is not supported.",
                "data": None,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            torbox = TorBox({"debridKey": "tb-token"}, client)
            result = await torbox.unrestrict_link("https://unsupported.example/file")

        self.assertIsNone(result)

    async def test_unrestrict_link_returns_pending_when_download_started_but_not_ready(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/v1/api/webdl/createwebdownload":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "download started",
                    "data": {"webdownload_id": 99},
                })
            if request.url.path == "/v1/api/webdl/mylist":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "web downloads list retrieved successfully",
                    "data": {
                        "id": 99,
                        "name": "pending-file",
                        "size": 0,
                        "download_state": "downloading",
                        "download_present": False,
                        "download_finished": False,
                        "files": [],
                    },
                })
            return httpx.Response(404, json={"success": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            torbox = TorBox({"debridKey": "tb-token"}, client)
            result = await torbox.unrestrict_link("https://host.example/uncached")

        self.assertEqual(result, {
            "download": None,
            "filename": "pending-file",
            "filesize": 0,
            "pending": True,
            "web_id": 99,
            "download_state": "downloading",
        })
        self.assertEqual([request.url.path for request in requests], [
            "/v1/api/webdl/createwebdownload",
            "/v1/api/webdl/mylist",
        ])

    async def test_playback_waits_for_torbox_to_finish_after_click(self):
        import main

        class TorBox:
            config = {}

        pending = {
            "download": None,
            "filename": "pending-file",
            "filesize": 0,
            "pending": True,
        }
        ready = {
            "download": "https://cdn.torbox.app/file.mkv",
            "filename": "file.mkv",
            "filesize": 100,
        }

        with patch("main.parse_config", return_value={"addonHost": "http://addon"}), \
             patch("main.decodeb64", return_value="https://host.example/file"), \
             patch("main.get_debrid_service", return_value=TorBox()), \
             patch("main.TORBOX_PLAYBACK_INITIAL_POLL_INTERVAL", 5), \
             patch("main.TORBOX_PLAYBACK_MAX_POLL_INTERVAL", 300), \
             patch("main.TORBOX_PLAYBACK_POLL_BACKOFF", 1.5), \
             patch("main.asyncio.sleep", new=AsyncMock()) as sleep_mock, \
             patch("main._get_unrestricted_link", new=AsyncMock(side_effect=[pending, ready])) as unrestrict_mock:
            result = await main._handle_playback("cfg", "encoded", "pending-file")

        self.assertEqual(result, "https://cdn.torbox.app/file.mkv")
        self.assertEqual(unrestrict_mock.await_count, 2)
        sleep_mock.assert_awaited_once_with(5)

    async def test_playback_poll_interval_backs_off_up_to_five_minutes(self):
        import main

        class TorBox:
            config = {}

        pending = {
            "download": None,
            "filename": "pending-file",
            "filesize": 0,
            "pending": True,
        }
        ready = {
            "download": "https://cdn.torbox.app/file.mkv",
            "filename": "file.mkv",
            "filesize": 100,
        }

        with patch("main.parse_config", return_value={"addonHost": "http://addon"}), \
             patch("main.decodeb64", return_value="https://host.example/file"), \
             patch("main.get_debrid_service", return_value=TorBox()), \
             patch("main.TORBOX_PLAYBACK_INITIAL_POLL_INTERVAL", 5), \
             patch("main.TORBOX_PLAYBACK_MAX_POLL_INTERVAL", 300), \
             patch("main.TORBOX_PLAYBACK_POLL_BACKOFF", 100), \
             patch("main.asyncio.sleep", new=AsyncMock()) as sleep_mock, \
             patch("main._get_unrestricted_link", new=AsyncMock(side_effect=[pending, pending, pending, ready])):
            result = await main._handle_playback("cfg", "encoded", "pending-file")

        self.assertEqual(result, "https://cdn.torbox.app/file.mkv")
        self.assertEqual([call.args[0] for call in sleep_mock.await_args_list], [5, 300, 300])

    async def test_unrestrict_link_recovers_existing_webdl_when_torbox_reports_duplicate(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/v1/api/webdl/createwebdownload":
                return httpx.Response(409, json={
                    "success": False,
                    "error": "DUPLICATE_ITEM",
                    "detail": "This item already exists.",
                    "data": None,
                })
            if request.url.path == "/v1/api/webdl/mylist" and "id" not in request.url.params:
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "web downloads list retrieved successfully",
                    "data": [{
                        "id": 123,
                        "original_url": "https://host.example/duplicate",
                    }],
                })
            if request.url.path == "/v1/api/webdl/mylist":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "web downloads list retrieved successfully",
                    "data": {
                        "id": 123,
                        "name": "duplicate.mkv",
                        "size": 500,
                        "download_present": True,
                        "download_finished": True,
                        "files": [
                            {"id": 8, "name": "duplicate.mkv", "size": 500, "infected": False},
                        ],
                    },
                })
            if request.url.path == "/v1/api/webdl/requestdl":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "Web download requested successfully.",
                    "data": "https://cdn.torbox.app/download/duplicate.mkv",
                })
            return httpx.Response(404, json={"success": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            torbox = TorBox({"debridKey": "tb-token"}, client)
            result = await torbox.unrestrict_link("https://host.example/duplicate")

        self.assertEqual(result["download"], "https://cdn.torbox.app/download/duplicate.mkv")
        self.assertEqual(result["web_id"], 123)
        self.assertEqual([request.url.path for request in requests], [
            "/v1/api/webdl/createwebdownload",
            "/v1/api/webdl/mylist",
            "/v1/api/webdl/mylist",
            "/v1/api/webdl/requestdl",
        ])

    async def test_unrestrict_link_deletes_lowest_progress_webdl_on_active_limit(self):
        requests = []
        create_calls = 0

        def handler(request):
            nonlocal create_calls
            requests.append(request)
            if request.url.path == "/v1/api/webdl/createwebdownload":
                create_calls += 1
                if create_calls == 1:
                    return httpx.Response(409, json={
                        "success": False,
                        "error": "ACTIVE_LIMIT",
                        "detail": "Active download limit reached.",
                        "data": None,
                    })
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "download started",
                    "data": {"webdownload_id": 77},
                })
            if request.url.path == "/v1/api/webdl/mylist" and "id" not in request.url.params:
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "web downloads list retrieved successfully",
                    "data": [
                        {"id": 10, "active": True, "download_finished": False, "progress": 75},
                        {"id": 11, "active": True, "download_finished": False, "progress": 12},
                        {"id": 12, "active": False, "download_finished": True, "progress": 100},
                    ],
                })
            if request.url.path == "/v1/api/webdl/controlwebdownload":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "Web download deleted.",
                    "data": None,
                })
            if request.url.path == "/v1/api/webdl/mylist":
                return httpx.Response(200, json={
                    "success": True,
                    "error": None,
                    "detail": "web downloads list retrieved successfully",
                    "data": {
                        "id": 77,
                        "name": "new-download",
                        "download_state": "downloading",
                        "download_present": False,
                        "download_finished": False,
                        "files": [],
                    },
                })
            return httpx.Response(404, json={"success": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            torbox = TorBox({"debridKey": "tb-token"}, client)
            result = await torbox.unrestrict_link("https://host.example/new")

        self.assertEqual(result["web_id"], 77)
        self.assertTrue(result["pending"])
        control_request = next(request for request in requests if request.url.path == "/v1/api/webdl/controlwebdownload")
        self.assertIn(b'"webdl_id":11', control_request.content)
        self.assertIn(b'"operation":"delete"', control_request.content)

    def test_service_factory_returns_torbox(self):
        service = get_debrid_service({"service": "torbox", "debridKey": "tb-token"}, http_client=None)

        self.assertIsInstance(service, TorBox)


class TorBoxIntegrationShapeTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_lookup_for_torbox_does_not_start_download(self):
        import main

        class TorBox:
            config = {}

            async def unrestrict_link(self, link):
                raise AssertionError("TorBox should only start on playback click")

            async def check_cached_link(self, link):
                return None

        link, data, is_valid = await main._process_single_link(
            TorBox(),
            "https://host.example/file",
            {},
            "1080p",
            "CASTELLANO",
            "WEB-DL",
            "('1080p', 'CASTELLANO', 'WEB-DL')",
        )

        self.assertEqual(link, "https://host.example/file")
        self.assertTrue(is_valid)
        self.assertTrue(data["debrid_pending"])
        self.assertFalse(data["streamable"])
        self.assertEqual(data["quality"], "1080p")

    async def test_catalog_lookup_for_torbox_marks_cached_links_streamable(self):
        import main

        class TorBox:
            config = {}

            async def unrestrict_link(self, link):
                raise AssertionError("TorBox should only start on playback click")

            async def check_cached_link(self, link):
                return {
                    "download": None,
                    "filename": "cached.1080p.mkv",
                    "filesize": 200,
                    "pending": False,
                    "web_id": 42,
                }

        link, data, is_valid = await main._process_single_link(
            TorBox(),
            "https://host.example/cached",
            {},
            "1080p",
            "CASTELLANO",
            "WEB-DL",
            "('1080p', 'CASTELLANO', 'WEB-DL')",
        )

        self.assertEqual(link, "https://host.example/cached")
        self.assertTrue(is_valid)
        self.assertFalse(data["debrid_pending"])
        self.assertTrue(data["streamable"])
        self.assertEqual(data["nombre_fichero"], "cached.1080p.mkv")
        self.assertEqual(data["filesize"], 200)

    async def test_main_unrestricted_link_accepts_torbox_normalized_response(self):
        import main

        class TorBox:
            config = {}

            async def unrestrict_link(self, link):
                return {
                    "download": "https://cdn.torbox.app/file.mkv",
                    "filename": "file.mkv",
                    "filesize": 100,
                }

        result = await main._get_unrestricted_link(TorBox(), "https://host.example/file")

        self.assertEqual(result, {
            "download": "https://cdn.torbox.app/file.mkv",
            "filename": "file.mkv",
            "filesize": 100,
        })

    async def test_main_unrestricted_link_accepts_pending_torbox_response(self):
        import main

        class TorBox:
            config = {}

            async def unrestrict_link(self, link):
                return {
                    "download": None,
                    "filename": "pending-file",
                    "filesize": 0,
                    "pending": True,
                    "web_id": 99,
                    "download_state": "downloading",
                }

        result = await main._get_unrestricted_link(TorBox(), "https://host.example/file")

        self.assertEqual(result, {
            "download": None,
            "filename": "pending-file",
            "filesize": 0,
            "pending": True,
            "web_id": 99,
            "download_state": "downloading",
        })
