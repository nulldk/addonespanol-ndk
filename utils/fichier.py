import os
import httpx
import random
import string
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Parse keys once at startup
_api_keys_str = os.getenv('FICHIER_API_KEY', '')
FICHIER_API_KEYS = [k.strip() for k in _api_keys_str.split(',') if k.strip()]

def get_random_api_key():
    if not FICHIER_API_KEYS:
        logger.warning("No FICHIER_API_KEY configured!")
        return None
    return random.choice(FICHIER_API_KEYS)

def generate_guid(length=10):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def get_file_info(http_client: httpx.AsyncClient, url: str):
    info_url = "https://api.1fichier.com/v1/file/info.cgi"
    api_key = get_random_api_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"url": url}

    try:
        response = await http_client.post(info_url, headers=headers, json=data)
        response.raise_for_status()
        file_info = response.json()
        return file_info.get('filename'), file_info, url
    except httpx.RequestError as e:
        logger.error(f"Error al obtener la información del archivo desde {url}: {e}")
        return None, None, url

async def copy_file(http_client: httpx.AsyncClient, url: str, rename=None, _retry=False):
    cp_url = "https://api.1fichier.com/v1/file/cp.cgi"
    api_key = get_random_api_key()
    
    if not api_key:
        logger.error("No hay claves API de 1fichier configuradas")
        return None, None
    
    new_filename = rename[:rename.rfind('.')] + generate_guid() if rename else generate_guid()
    
    logger.info(f"Intentando copiar URL: {url}")
    logger.debug(f"Nuevo nombre de archivo: {new_filename}")
    logger.debug(f"Usando clave API: {api_key[:10]}...{api_key[-4:]}")
    data = {"urls": [url], "rename": new_filename}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = await http_client.post(cp_url, headers=headers, json=data)
        response.raise_for_status()
        response_json = response.json()

        if response.status_code == 200 and 'urls' in response_json:
            logger.info(f"Archivo copiado exitosamente: {new_filename}")
            urls_data = response_json['urls'][0]
            from_url = urls_data.get('from_url')
            to_url = urls_data.get('to_url')
            if from_url and to_url:
                return from_url, to_url
        
        if (response_json.get("status") == "KO" and "Bad filename characters" in response_json.get("message", "") and rename is not None and not _retry):
            logger.debug("Reintentando sin rename por error de caracteres inválidos.")
            return await copy_file(http_client, url, rename=None, _retry=True)
        
        logger.debug(f"Error en la respuesta de la API al copiar: {response_json}")
        return None, None
        
    except httpx.HTTPStatusError as e:
        logger.error(f"Error HTTP {e.response.status_code} al copiar archivo {url}: {e.response.text}")
        return None, None
    except httpx.RequestError as e:
        logger.error(f"Excepción de red al copiar el archivo {url}: {e}")
        return None, None
    except (KeyError, IndexError) as e:
        logger.error(f"Error al procesar la respuesta JSON de la API: {e}")
        return None, None