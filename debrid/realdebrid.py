from debrid.base_debrid import BaseDebrid
from utils.cache import cache
from utils.logger import setup_logger
import asyncio
import hashlib
import httpx
import os
import random
import re
import signal
import string
from urllib.parse import unquote, urljoin

logger = setup_logger(__name__)
FICHIER_PREPARED_LINK_TTL = 15 * 24 * 60 * 60
FICHIER_PREPARED_INFLIGHT = {}
FOLDER_HREF_REGEX = re.compile(r'<a href="([^"]+)">[^<]*<\/a>')
WARP_RESTART_LOCK = asyncio.Lock()

class RealDebrid(BaseDebrid):
    def __init__(self, config, http_client: httpx.AsyncClient, warp_client: httpx.AsyncClient = None):
        super().__init__(config, http_client)
        self.warp_client = warp_client
        self.base_url = "https://api.real-debrid.com"
        self.headers = {"Authorization": f"Bearer {self.config['debridKey'].strip()}"}
        self._folder_hrefs_cache = {}
        self._folder_hrefs_inflight = {}

    async def unrestrict_link(self, link):
        url = f"{self.base_url}/rest/1.0/unrestrict/link"
        link_to_unrestrict = await self._prepare_1fichier_link(link)
        data = {"link": link_to_unrestrict}
        return await self.get_json_response(url, method='post', headers=self.headers, data=data, client=self.warp_client)

    async def _prepare_1fichier_link(self, link):
        if "1fichier.com" not in link.lower():
            return link

        fichier_api_key = self._select_fichier_api_key(link)
        if not fichier_api_key:
            self.logger.warning("Enlace 1fichier detectado, pero no hay FICHIER_API_KEY configurada. Se enviará el enlace original a Real-Debrid.")
            return link

        cache_key = self._fichier_cache_key(link)
        cached_link = cache.get(cache_key)
        if cached_link:
            return cached_link

        inflight_task = FICHIER_PREPARED_INFLIGHT.get(cache_key)
        if inflight_task:
            return await inflight_task

        task = asyncio.create_task(self._create_prepared_1fichier_link(link, fichier_api_key, cache_key))
        FICHIER_PREPARED_INFLIGHT[cache_key] = task
        try:
            return await task
        finally:
            if FICHIER_PREPARED_INFLIGHT.get(cache_key) is task:
                del FICHIER_PREPARED_INFLIGHT[cache_key]

    async def _create_prepared_1fichier_link(self, link, fichier_api_key, cache_key):
        random_filename = self._random_filename()
        copied_url = await self._copy_1fichier_link(link, fichier_api_key, rename=random_filename)
        if copied_url:
            cache.set(cache_key, copied_url, ttl=FICHIER_PREPARED_LINK_TTL)
            return copied_url

        copied_url = await self._copy_1fichier_link(link, fichier_api_key)
        if not copied_url:
            self.logger.warning("No se pudo copiar el enlace de 1fichier. Se enviará el enlace original a Real-Debrid.")
            return link

        updated_url = await self._rename_1fichier_link(copied_url, random_filename, fichier_api_key)
        if not updated_url:
            self.logger.warning("No se pudo renombrar la copia de 1fichier. Se enviará la copia sin renombrar a Real-Debrid.")
            return copied_url

        cache.set(cache_key, updated_url, ttl=FICHIER_PREPARED_LINK_TTL)
        return updated_url

    async def _copy_1fichier_link(self, link, api_key, rename=None):
        payload = {"urls": [link]}
        if rename:
            payload["rename"] = rename

        response = await self._post_1fichier("cp.cgi", payload, api_key)
        if not response or response.get("status") != "OK":
            self.logger.warning(f"Respuesta inválida al copiar en 1fichier: {response}")
            return None

        copied_urls = response.get("urls") or []
        if not copied_urls:
            return None

        return copied_urls[0].get("to_url")

    async def _rename_1fichier_link(self, link, filename, api_key):
        payload = {
            "urls": [link],
            "filename": filename
        }
        response = await self._post_1fichier("chattr.cgi", payload, api_key)
        if not response or response.get("status") != "OK":
            self.logger.warning(f"Respuesta inválida al cambiar atributos en 1fichier: {response}")
            return None

        updated_urls = response.get("urls") or []
        if updated_urls:
            return updated_urls[0]

        return link if response.get("updated", 0) > 0 else None

    async def _post_1fichier(self, endpoint, payload, api_key):
        return await self._post_1fichier_once(endpoint, payload, api_key, retry_on_ip_lock=True)

    async def _post_1fichier_once(self, endpoint, payload, api_key, retry_on_ip_lock=False):
        url = f"https://api.1fichier.com/v1/file/{endpoint}"
        headers = {"Authorization": f"Bearer {api_key}"}
        client = self.warp_client or self.http_client
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"1fichier {endpoint} falló con status {e.response.status_code}: {e.response.text}")
            if retry_on_ip_lock and self.warp_client and self._is_1fichier_ip_locked(e.response):
                restarted = await self._restart_warp_proxy()
                if restarted:
                    self.logger.warning(f"WARP reiniciado tras bloqueo de IP en 1fichier {endpoint}. Reintentando una vez.")
                    return await self._post_1fichier_once(endpoint, payload, api_key, retry_on_ip_lock=False)
            return None
        except httpx.RequestError as e:
            self.logger.error(f"Error llamando a 1fichier {endpoint}: {e}")
            return None
        except ValueError:
            self.logger.error(f"1fichier {endpoint} devolvió una respuesta no JSON: {response.text}")
            return None

    def _is_1fichier_ip_locked(self, response):
        return response.status_code == 403 and "IP Locked" in response.text

    async def _restart_warp_proxy(self):
        if os.getenv("WARP_AUTO_RESTART", "true").lower() in ("0", "false", "no"):
            self.logger.warning("Bloqueo de IP detectado, pero WARP_AUTO_RESTART está desactivado.")
            return False

        async with WARP_RESTART_LOCK:
            wireproxy_path = os.getenv("WIREPROXY_PATH", "./wireproxy")
            config_path = os.getenv("WIREPROXY_CONFIG_PATH", "wireproxy.conf")
            proxy_host = os.getenv("WARP_PROXY_HOST", "127.0.0.1")
            proxy_port = int(os.getenv("WARP_PROXY_PORT", "40000"))

            if not os.path.exists(wireproxy_path) or not os.path.exists(config_path):
                self.logger.error(f"No se puede reiniciar WARP: falta {wireproxy_path} o {config_path}.")
                return False

            self.logger.warning("Reiniciando wireproxy por bloqueo de IP en 1fichier.")
            await self._terminate_wireproxy_processes(wireproxy_path)
            await asyncio.sleep(1)

            try:
                await asyncio.create_subprocess_exec(
                    wireproxy_path,
                    "-c",
                    config_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as e:
                self.logger.error(f"No se pudo iniciar wireproxy: {e}")
                return False

            return await self._wait_for_warp_proxy(proxy_host, proxy_port)

    async def _terminate_wireproxy_processes(self, wireproxy_path):
        pids = self._wireproxy_pids(wireproxy_path)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        await asyncio.sleep(1)

        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue

            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _wireproxy_pids(self, wireproxy_path):
        wireproxy_name = os.path.basename(wireproxy_path)
        current_pid = os.getpid()
        pids = []

        try:
            proc_entries = os.listdir("/proc")
        except OSError:
            return pids

        for entry in proc_entries:
            if not entry.isdigit():
                continue

            pid = int(entry)
            if pid == current_pid:
                continue

            try:
                with open(f"/proc/{entry}/cmdline", "rb") as cmdline_file:
                    cmdline = cmdline_file.read().decode("utf-8", errors="ignore")
            except OSError:
                continue

            args = [arg for arg in cmdline.split("\0") if arg]
            if not args:
                continue

            executable = args[0]
            if executable == wireproxy_path or os.path.basename(executable) == wireproxy_name:
                pids.append(pid)

        return pids

    async def _wait_for_warp_proxy(self, host, port, attempts=10, delay=0.5):
        for _ in range(attempts):
            try:
                reader, writer = await asyncio.open_connection(host, port)
                writer.close()
                await writer.wait_closed()
                return True
            except OSError:
                await asyncio.sleep(delay)

        self.logger.error(f"wireproxy no empezó a escuchar en {host}:{port}.")
        return False

    def _random_filename(self, length=16):
        return ''.join(random.choices(string.ascii_letters, k=length))

    def _select_fichier_api_key(self, link):
        api_keys = self._fichier_api_keys()
        if not api_keys:
            return None

        key_index = int(hashlib.sha256(link.encode("utf-8")).hexdigest(), 16) % len(api_keys)
        return api_keys[key_index]

    def _fichier_api_keys(self):
        return [
            api_key.strip()
            for api_key in os.getenv("FICHIER_API_KEY", "").split(",")
            if api_key.strip()
        ]

    def _fichier_cache_key(self, link):
        key_hash = hashlib.sha256(link.encode("utf-8")).hexdigest()
        return f"realdebrid:1fichier:prepared:{key_hash}"

    async def find_link_in_folder(self, http_folder_url, filename):
        """
        Busca en la carpeta HTTP de Real-Debrid para encontrar el enlace de descarga directa.
        La comparación se realiza usando el ID único al final del nombre del archivo.
        """
        if not http_folder_url or not http_folder_url.startswith("https://my.real-debrid.com/"):
            self.logger.warning("La URL de la carpeta HTTP de Real-Debrid no es válida o no está configurada.")
            return None

        links_folder_url = f"{http_folder_url.rstrip('/')}/links/"
        self.logger.info(f"Iniciando búsqueda por ID único para: '{filename}'")

        # 1. Extraer el ID del nombre de archivo de la API
        try:
            api_id_start_index = filename.rfind(')') + 1
            api_id = filename[api_id_start_index:] if api_id_start_index > 0 else None
        except:
            api_id = None

        if not api_id:
            self.logger.warning(f"No se pudo extraer el ID del filename de la API: '{filename}'. Cancelando búsqueda.")
            return None
            

        try:
            had_cached_folder = links_folder_url in self._folder_hrefs_cache
            hrefs = await self._get_folder_hrefs(links_folder_url)
            full_link_url = self._find_folder_link_by_id(links_folder_url, hrefs, api_id)
            if full_link_url:
                self.logger.info(f"✅ Coincidencia por ID único encontrada! URL: {full_link_url}")
                return full_link_url

            if had_cached_folder:
                hrefs = await self._get_folder_hrefs(links_folder_url, force_refresh=True)
                full_link_url = self._find_folder_link_by_id(links_folder_url, hrefs, api_id)
                if full_link_url:
                    self.logger.info(f"✅ Coincidencia por ID único encontrada! URL: {full_link_url}")
                    return full_link_url

            self.logger.warning(f"No se pudo encontrar en la carpeta un enlace con el ID '{api_id}'.")
            return None

        except Exception as e:
            self.logger.error(f"Error al buscar en la carpeta de Real-Debrid {links_folder_url}: {e}")
            return None

    async def _get_folder_hrefs(self, links_folder_url, force_refresh=False):
        if not force_refresh and links_folder_url in self._folder_hrefs_cache:
            return self._folder_hrefs_cache[links_folder_url]

        inflight_task = self._folder_hrefs_inflight.get(links_folder_url)
        if inflight_task:
            return await inflight_task

        task = asyncio.create_task(self._fetch_folder_hrefs(links_folder_url))
        self._folder_hrefs_inflight[links_folder_url] = task
        try:
            hrefs = await task
            self._folder_hrefs_cache[links_folder_url] = hrefs
            return hrefs
        finally:
            if self._folder_hrefs_inflight.get(links_folder_url) is task:
                del self._folder_hrefs_inflight[links_folder_url]

    async def _fetch_folder_hrefs(self, links_folder_url):
        response = await self.http_client.get(links_folder_url, follow_redirects=True)
        response.raise_for_status()
        return FOLDER_HREF_REGEX.findall(response.text)

    def _find_folder_link_by_id(self, links_folder_url, hrefs, api_id):
        for href in hrefs:
            if "Parent Directory" in href or href == "../":
                continue

            decoded_href = unquote(href)

            try:
                href_id_start_index = decoded_href.rfind(')') + 1
                href_id = decoded_href[href_id_start_index:] if href_id_start_index > 0 else None
            except:
                href_id = None

            if href_id and api_id == href_id:
                return urljoin(links_folder_url, href)

        return None
