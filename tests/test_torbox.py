import unittest
from unittest.mock import patch

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

    async def test_playback_redirects_pending_torbox_to_spanish_placeholder_video(self):
        import main

        class TorBox:
            config = {}

        with patch("main.parse_config", return_value={"addonHost": "http://addon"}), \
             patch("main.decodeb64", return_value="https://host.example/file"), \
             patch("main.get_debrid_service", return_value=TorBox()), \
             patch("main._get_unrestricted_link", return_value={
                 "download": None,
                 "filename": "pending-file",
                 "filesize": 0,
                 "pending": True,
             }):
            result = await main._handle_playback("cfg", "encoded", "pending-file")

        self.assertEqual(result, "http://addon/static/torbox-descargando.mp4")

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

    def test_service_factory_returns_torbox(self):
        service = get_debrid_service({"service": "torbox", "debridKey": "tb-token"}, http_client=None)

        self.assertIsInstance(service, TorBox)


class TorBoxIntegrationShapeTest(unittest.IsolatedAsyncioTestCase):
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
