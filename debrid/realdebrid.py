from debrid.base_debrid import BaseDebrid
from utils.logger import setup_logger
import httpx
import os
import re
import secrets
import string
from urllib.parse import unquote, urljoin

logger = setup_logger(__name__)

class RealDebrid(BaseDebrid):
    def __init__(self, config, http_client: httpx.AsyncClient, warp_client: httpx.AsyncClient = None):
        super().__init__(config, http_client)
        self.warp_client = warp_client
        self.base_url = "https://api.real-debrid.com"
        self.headers = {"Authorization": f"Bearer {self.config['debridKey']}"}

    async def unrestrict_link(self, link):
        url = f"{self.base_url}/rest/1.0/unrestrict/link"
        link_to_unrestrict = await self._prepare_1fichier_link(link)
        data = {"link": link_to_unrestrict}
        return await self.get_json_response(url, method='post', headers=self.headers, data=data, client=self.warp_client)

    async def _prepare_1fichier_link(self, link):
        if "1fichier.com" not in link.lower():
            return link

        fichier_api_key = self.config.get("fichierApiKey") or os.getenv("FICHIER_API_KEY")
        if not fichier_api_key:
            self.logger.warning("Enlace 1fichier detectado, pero no hay fichierApiKey configurada. Se enviará el enlace original a Real-Debrid.")
            return link

        copied_url = await self._copy_1fichier_link(link, fichier_api_key)
        if not copied_url:
            self.logger.warning("No se pudo copiar el enlace de 1fichier. Se enviará el enlace original a Real-Debrid.")
            return link

        random_filename = self._random_filename()
        updated_url = await self._rename_1fichier_link(copied_url, random_filename, fichier_api_key)
        if not updated_url:
            self.logger.warning("No se pudo renombrar la copia de 1fichier. Se enviará la copia sin renombrar a Real-Debrid.")
            return copied_url

        return updated_url

    async def _copy_1fichier_link(self, link, api_key):
        payload = {"urls": [link]}
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
        url = f"https://api.1fichier.com/v1/file/{endpoint}"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = await self.http_client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"1fichier {endpoint} falló con status {e.response.status_code}: {e.response.text}")
            return None
        except httpx.RequestError as e:
            self.logger.error(f"Error llamando a 1fichier {endpoint}: {e}")
            return None
        except ValueError:
            self.logger.error(f"1fichier {endpoint} devolvió una respuesta no JSON: {response.text}")
            return None

    def _random_filename(self, length=16):
        return ''.join(secrets.choice(string.ascii_letters) for _ in range(length))

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
            response = await self.http_client.get(links_folder_url, follow_redirects=True)
            response.raise_for_status()
            html_content = response.text
            
            hrefs = re.findall(r'<a href="([^"]+)">[^<]*<\/a>', html_content)

            for href in hrefs:
                if "Parent Directory" in href or href == "../":
                    continue
                
                decoded_href = unquote(href)
                
                # 2. Extraer el ID del nombre del archivo en el href
                try:
                    href_id_start_index = decoded_href.rfind(')') + 1
                    href_id = decoded_href[href_id_start_index:] if href_id_start_index > 0 else None
                except:
                    href_id = None
                
                # 3. Comparar los IDs
                if href_id and api_id == href_id:
                    full_link_url = urljoin(links_folder_url, href)
                    self.logger.info(f"✅ Coincidencia por ID único encontrada! URL: {full_link_url}")
                    return full_link_url

            self.logger.warning(f"No se pudo encontrar en la carpeta un enlace con el ID '{api_id}'.")
            return None

        except Exception as e:
            self.logger.error(f"Error al buscar en la carpeta de Real-Debrid {links_folder_url}: {e}")
            return None
