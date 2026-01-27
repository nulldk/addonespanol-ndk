import httpx # OPTIMIZADO: Importar httpx
from utils.logger import setup_logger

class BaseDebrid:
    def __init__(self, config, http_client: httpx.AsyncClient): # OPTIMIZADO: Recibir el cliente http
        self.config = config
        self.logger = setup_logger(__name__)
        self.http_client = http_client # OPTIMIZADO: Usar el cliente http asíncrono

    async def get_json_response(self, url, method='get', data=None, headers=None, files=None, client=None): # OPTIMIZADO: Convertir a async
        client_to_use = client or self.http_client
        try:
            if method == 'get':
                response = await client_to_use.get(url, headers=headers)
            elif method == 'post':
                response = await client_to_use.post(url, data=data, headers=headers, files=files)
            elif method == 'put':
                response = await client_to_use.put(url, data=data, headers=headers)
            elif method == 'delete':
                response = await client_to_use.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()  # Lanza una excepción para códigos de error 4xx/5xx
            return response.json()

        except httpx.HTTPStatusError as e:
            self.logger.error(f"Request failed with status code {e.response.status_code} and response: {e.response.text}")
            return None
        except httpx.RequestError as e:
            self.logger.error(f"An error occurred while requesting {e.request.url!r}: {e}")
            return None
        except ValueError:
            self.logger.error(f"Failed to parse response as JSON: {response.text}")
            return None