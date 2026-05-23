from debrid.base_debrid import BaseDebrid
from utils.logger import setup_logger
import httpx

logger = setup_logger(__name__)


class TorBox(BaseDebrid):
    def __init__(self, config, http_client: httpx.AsyncClient):
        super().__init__(config, http_client)
        self.base_url = "https://api.torbox.app/v1/api"
        self.token = self.config["debridKey"].strip()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def unrestrict_link(self, link):
        created_download = await self._create_web_download(link)
        if not created_download:
            return None

        web_id = created_download.get("webdownload_id") or created_download.get("id")
        if web_id is None:
            self.logger.warning(f"TorBox no devolvió webdownload_id para el enlace: {link}")
            return None

        web_download = await self._get_web_download(web_id)
        selected_file = self._select_file(web_download)
        file_id = selected_file.get("id", 0) if selected_file else 0

        if not self._is_download_ready(web_download):
            return {
                "download": None,
                "filename": self._filename(web_download, selected_file, link),
                "filesize": self._filesize(web_download, selected_file),
                "pending": True,
                "web_id": web_id,
                "download_state": web_download.get("download_state"),
            }

        download_link = await self._request_download_link(web_id, file_id)
        if not download_link:
            return None

        return {
            "download": download_link,
            "filename": self._filename(web_download, selected_file, link),
            "filesize": self._filesize(web_download, selected_file),
            "pending": False,
            "web_id": web_id,
        }

    async def _create_web_download(self, link):
        url = f"{self.base_url}/webdl/createwebdownload"
        data = {"link": link}
        response = await self._request_json("post", url, data=data, headers=self.headers)
        if not self._is_success(response):
            if response and response.get("error") == "DUPLICATE_ITEM":
                existing_download = await self._find_web_download_by_link(link)
                if existing_download and existing_download.get("id") is not None:
                    return {"webdownload_id": existing_download["id"]}

            self._log_torbox_error("crear web download", response)
            return None

        return response.get("data") or {}

    async def _get_web_download(self, web_id):
        url = f"{self.base_url}/webdl/mylist"
        response = await self._request_json(
            "get",
            url,
            headers=self.headers,
            params={"id": web_id, "bypass_cache": "true"},
        )
        if not self._is_success(response):
            self._log_torbox_error("obtener web download", response)
            return {}

        data = response.get("data") or {}
        if isinstance(data, list):
            return data[0] if data else {}
        return data

    async def _find_web_download_by_link(self, link):
        url = f"{self.base_url}/webdl/mylist"
        response = await self._request_json(
            "get",
            url,
            headers=self.headers,
            params={"bypass_cache": "true", "limit": 1000},
        )
        if not self._is_success(response):
            self._log_torbox_error("buscar web download duplicado", response)
            return {}

        for web_download in response.get("data") or []:
            if web_download.get("original_url") == link:
                return web_download

        return {}

    async def _request_download_link(self, web_id, file_id=0):
        url = f"{self.base_url}/webdl/requestdl"
        response = await self._request_json(
            "get",
            url,
            params={
                "token": self.token,
                "web_id": web_id,
                "file_id": file_id or 0,
                "redirect": "false",
                "append_name": "true",
            },
        )
        if not self._is_success(response):
            self._log_torbox_error("solicitar enlace de descarga", response)
            return None

        data = response.get("data")
        return data if isinstance(data, str) else None

    async def _request_json(self, method, url, data=None, headers=None, params=None):
        try:
            if method == "get":
                response = await self.http_client.get(url, headers=headers, params=params)
            elif method == "post":
                response = await self.http_client.post(url, data=data, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"TorBox request failed with status code {e.response.status_code} and response: {e.response.text}")
            try:
                return e.response.json()
            except ValueError:
                return None
        except httpx.RequestError as e:
            self.logger.error(f"Error llamando a TorBox {e.request.url!r}: {e}")
            return None
        except ValueError:
            self.logger.error(f"TorBox devolvió una respuesta no JSON: {response.text}")
            return None

    def _is_success(self, response):
        return bool(response and response.get("success") is True)

    def _log_torbox_error(self, action, response):
        if not response:
            self.logger.warning(f"No se pudo {action} en TorBox.")
            return
        self.logger.warning(f"No se pudo {action} en TorBox: {response.get('error')} - {response.get('detail')}")

    def _select_file(self, web_download):
        files = web_download.get("files") or []
        usable_files = [file for file in files if not file.get("infected")]
        if not usable_files:
            return None
        return max(usable_files, key=lambda file: int(file.get("size") or 0))

    def _is_download_ready(self, web_download):
        return bool(
            web_download.get("download_present")
            and web_download.get("download_finished")
            and web_download.get("files")
        )

    def _filename(self, web_download, selected_file, fallback):
        if selected_file and selected_file.get("name"):
            return selected_file["name"]
        if web_download.get("name"):
            return web_download["name"]
        return fallback.rsplit("/", 1)[-1] or fallback

    def _filesize(self, web_download, selected_file):
        if selected_file:
            return int(selected_file.get("size") or 0)
        return int(web_download.get("size") or 0)
