import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime
import sys

import httpx
import requests
from aiocron import crontab
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette import status

from debrid.get_debrid_service import get_debrid_service
from metadata.tmdb import TMDB
from utils.actualizarbd import comprobar_actualizacion_contenido, comprobar_actualizacion_addon, establecer_timestamp_arranque
from utils.bd import (setup_index,
                      search_movies, search_tv_shows)
from utils.cargarbd import check_and_download
from utils.detection import detect_quality, post_process_results, detect_languages, detect_quality_spec
from utils.filter_results import filter_items
from utils.logger import setup_logger
from utils.parse_config import parse_config
from utils.stremio_parser import parse_to_debrid_stream
from utils.string_encoding import decodeb64, encodeb64
from utils.cache import cache  # Importamos nuestro cache nativo

from config import (
    VERSION,
    IS_DEV,
    IS_COMMUNITY_VERSION,
    ROOT_PATH,
    DB_ENCRYPTED_PATH,
    DB_DECRYPTED_PATH,
    UPDATE_LOG_FILE,
    VERSION_FILE,
    PING_URL,
    RENDER_API_URL,
    RENDER_AUTH_HEADER,
    DEBRID_API_KEY,
    ADMIN_PATH_DB_ENCRYPTED,
    ADMIN_PATH_DB_DECRYPTED,
    ADMIN_PATH_RESTART,
    WORKING_PATH
)

# --- Inicialización ---
logger = setup_logger(__name__)
# OPTIMIZADO: Crear un cliente httpx para reutilizar conexiones
http_client = httpx.AsyncClient(timeout=30)
# Hardcodeamos el proxy porque sabemos que siempre correrá en local por start.sh
WARP_PROXY_URL = "socks5://127.0.0.1:40000"
logger.info(f"Configurando Proxy Warp para unrestrict: {WARP_PROXY_URL}")
warp_client = httpx.AsyncClient(timeout=30, proxy=WARP_PROXY_URL)


FICHIER_STATUS_KEY = "rd_1fichier_status"

async def check_real_debrid_1fichier_availability():
    if not DEBRID_API_KEY:
        logger.warning("No se ha configurado DEBRID_API_KEY en .env.")
        return

    url = "https://api.real-debrid.com/rest/1.0/hosts/status"
    headers = {"Authorization": f"Bearer {DEBRID_API_KEY}"}
    status = "up"
    try:
        response = await http_client.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        hosts_status = response.json()
        
        for host_domain, info in hosts_status.items():
            if "1fichier" in host_domain.lower():
                if info.get("status", "").lower() != "up":
                    status = "down"
                break
    except Exception as e:
        logger.error(f"Error al comprobar estado de hosts de RD: {e}")
        status = "down"
    finally:
        # Usamos cache.set síncrono
        cache.set(FICHIER_STATUS_KEY, status, ttl=1800)
        logger.info(f"Estado de 1fichier en Real-Debrid actualizado a: '{status}'")

@crontab("*/15 * * * *", start=not IS_DEV)
async def scheduled_fichier_check():
    await check_real_debrid_1fichier_availability()


async def schedule_catalog_update_notification():
    """Espera 15 minutos y luego llama a la URL de actualización."""
    wait_time_seconds = 15 * 60
    logger.info(f"Programando llamada a updatedb en {wait_time_seconds} segundos...")
    
    await asyncio.sleep(wait_time_seconds)
    
    try:
        url = 'https://ndkcatalogs.myblacknass.synology.me/updatedb'
        logger.info(f"Ejecutando llamada diferida a: {url}")
        await http_client.get(url)
        logger.info("Llamada a updatedb realizada con éxito.")
    except Exception as e:
        logger.error(f"Error al llamar a updatedb tras la espera: {e}")

# Variable global para el estado de carga de la BD
IS_DB_READY = False

async def background_db_loader():
    """
    Tarea en segundo plano para descargar y preparar la base de datos.
    """
    global IS_DB_READY
    logger.info("Iniciando carga de base de datos en segundo plano...")
    try:
        # Ejecutar check_and_download en un executor para no bloquear el loop principal
        # ya que contiene muchas operaciones de E/S bloqueantes y CPU
        loop = asyncio.get_running_loop()
        updated = await loop.run_in_executor(None, check_and_download)
        
        if updated:
             # Si se actualizó, ejecutar setup_index también en executor por si acaso
             await loop.run_in_executor(None, setup_index, DB_DECRYPTED_PATH)
             logger.info("Base de datos actualizada y lista.")
             if not IS_DEV:
                asyncio.create_task(schedule_catalog_update_notification())
        else:
             logger.info("Base de datos verificada sin cambios.")
             # Asegurar que setup_index se ejecute si ya existía la BD pero no se actualizó
             if os.path.exists(DB_DECRYPTED_PATH):
                 await loop.run_in_executor(None, setup_index, DB_DECRYPTED_PATH)
        
        IS_DB_READY = True
        logger.info("✅ Sistema listo para recibir peticiones.")
        
    except Exception as e:
        logger.error(f"Error crítico cargando la base de datos: {e}", exc_info=True)
        # Aquí podríamos decidir si reintentar o dejar el servicio en estado degradado

async def lifespan(app: FastAPI):
    """
    Realiza tareas de inicialización al arrancar la aplicación.
    Descarga, descifra y prepara la base de datos para su uso.
    """
    logger.info("Iniciando tareas de arranque...")
    os.makedirs(WORKING_PATH, exist_ok=True)

    cache.set(FICHIER_STATUS_KEY, "up")
    logger.info(f"Estado inicial de 1fichier establecido a 'up' por defecto.")

    logger.info("Estableciendo timestamps de arranque...")
    establecer_timestamp_arranque("CONTENIDO")
    establecer_timestamp_arranque("ADDON")
    logger.info("Timestamps de arranque establecidos.")

    # Lanzar la carga de BD en background
    asyncio.create_task(background_db_loader())
    
    logger.info("Servidor HTTP iniciado. La carga de datos continúa en segundo plano.")
    yield
    logger.info("La aplicación se está cerrando.")

# Configuración de la aplicación FastAPI
app = FastAPI(root_path=f"/{ROOT_PATH}" if ROOT_PATH and not ROOT_PATH.startswith("/") else ROOT_PATH, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LogFilterMiddleware:
    """Filtra datos sensibles de las URLs en los logs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            path = request.url.path
            # Oculta configuraciones codificadas en la URL para no exponerlas en logs
            re.sub(r'/ey.*?/', '/<SENSITIVE_DATA>/', path)
        await self.app(scope, receive, send)


if not IS_DEV:
    app.add_middleware(LogFilterMiddleware)

templates = Jinja2Templates(directory="templates")


# --- Endpoints de la Interfaz y Manifiesto ---


@app.get("/", include_in_schema=False)
async def root():
    """Redirige a la página de configuración."""
    return RedirectResponse(url="/configure")


@app.get("/configure")
@app.get("/{config}/configure")
async def configure(request: Request):
    """Sirve la página de configuración del addon."""
    context = {
        "request": request,
        "isCommunityVersion": IS_COMMUNITY_VERSION,
        "version": VERSION
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/static/{file_path:path}", include_in_schema=False)
async def static_files(file_path: str):
    """Sirve archivos estáticos para la interfaz web."""
    return FileResponse(f"templates/{file_path}")


@app.get("/manifest.json")
@app.get("/{config}/manifest.json")
async def get_manifest():
    """
    Proporciona el manifiesto del addon a Stremio.
    Define las capacidades y metadatos del addon.
    """
    addon_name = f"NDK {' Community' if IS_COMMUNITY_VERSION else ''}{' (Dev)' if IS_DEV else ''}"
    return {
        "id": "test.streamioaddon.ndk",
        "icon": "https://i.ibb.co/zGmkQZm/ndk.jpg",
        "version": VERSION,
        "catalogs": [],
        "resources": ["stream"],
        "types": ["movie", "series"],
        "name": addon_name,
        "description": "El mejor AddOn para ver contenido en español. El contenido es obtenido de fuentes de terceros.",
        "behaviorHints": {"configurable": True},
    }


# --- Lógica Principal del Addon ---


async def _get_unrestricted_link(debrid_service, original_link: str) -> dict | None:
    """
    Obtiene el enlace de descarga directa (sin restricciones) de un servicio Debrid.
    Devuelve un diccionario con metadatos: {'download': str, 'filename': str, 'filesize': int}
    """
    debrid_name = type(debrid_service).__name__
    try:
        unrestricted_data = await debrid_service.unrestrict_link(original_link)
        
        if not unrestricted_data:
            return None

        result = {
            'download': None,
            'filename': None,
            'filesize': 0
        }

        if debrid_name == "RealDebrid":
            result['download'] = unrestricted_data.get('download')
            result['filename'] = unrestricted_data.get('filename')
            result['filesize'] = unrestricted_data.get('filesize', 0)

            http_folder = debrid_service.config.get('debridHttp')
            
            if http_folder:
                unrestricted_filename = result['filename']
                
                if unrestricted_filename:
                    folder_link = await debrid_service.find_link_in_folder(http_folder, unrestricted_filename)
                    
                    if folder_link:
                        result['download'] = folder_link
                else:
                    logger.warning("No se recibió 'filename' de la API de RD. Se usará el enlace por defecto.")
            
            if not http_folder:
                logger.info("Devolviendo el enlace de descarga estándar de la API de Real-Debrid.")

        elif debrid_name == "AllDebrid":
            data = unrestricted_data.get('data', {})
            result['download'] = data.get('link')
            result['filename'] = data.get('filename')
            result['filesize'] = data.get('filesize', 0)
            
        if not result['download']:
            return None

        return result
    except Exception as e:
        logger.error(f"Error al desrestringir el enlace {original_link} con {debrid_name}: {e}")
        return None




async def _process_single_link(debrid_service, link, config, db_calidad, db_audio, db_info):
    # 1. Intentar recuperar metadatos del caché global (independiente del usuario)
    cached_metadata = cache.get(link)
    
    if cached_metadata:
        # Cache HIT: Reconstruimos el objeto data usando la info del cache + info de la DB
        data = {
            'link': link,
            'filesize': cached_metadata.get('filesize', 0),
            'quality': cached_metadata.get('quality', db_calidad or ''),
            'nombre_fichero': cached_metadata.get('nombre_fichero', ''),
            'db_calidad': db_calidad or '',
            'db_audio': db_audio or '',
            'db_info': db_info or '',
            'languages': cached_metadata.get('languages'),
            'quality_spec': cached_metadata.get('quality_spec')
        }
        # Devolvemos True en el tercer argumento para indicar que es válido (aunque no tengamos el final_link del usuario)
        return (link, data, True)

    # 2. Cache MISS: Consultamos a Debrid
    data = {
        'link': link,
        'filesize': 0,
        'quality': db_calidad or '', 
        'nombre_fichero': '',
        'db_calidad': db_calidad or '',
        'db_audio': db_audio or '',
        'db_info': db_info or ''
    }

    try:
        unrestricted_info = await _get_unrestricted_link(debrid_service, link)
        if unrestricted_info:
            data['filesize'] = unrestricted_info.get('filesize', 0)
            data['nombre_fichero'] = unrestricted_info.get('filename', '')
            # Nota: final_link es específico del usuario actual, NO lo cacheamos
            final_link_user = unrestricted_info.get('download')
            
            detected_quality = detect_quality(data['nombre_fichero']) if data['nombre_fichero'] else None
            
            if not detected_quality:
                detected_quality = detect_quality(data['quality'])

            data['quality'] = detected_quality or data['quality']

            if data['nombre_fichero']:
                data['languages'] = detect_languages(data['nombre_fichero'])
                data['quality_spec'] = detect_quality_spec(data['nombre_fichero'])
            
            # Guardamos SOLO los metadatos en el caché global
            if data['nombre_fichero'] and data['filesize'] > 0:
                cache.set(link, {
                    'filesize': data['filesize'],
                    'quality': data['quality'],
                    'nombre_fichero': data['nombre_fichero'],
                    'languages': data.get('languages'),
                    'quality_spec': data.get('quality_spec')
                }, ttl=3600) # Cache por 1 hora

            # Devolvemos el link final para este usuario (aunque en get_results no se usa para generar la lista)
            return (link, data, final_link_user)
        else:
            logger.debug(f"Fallo al desrestringir enlace: {link}") 
    except Exception as e:
        logger.error(f"Error processing {link}: {e}")
    
    return (link, data, None)


@app.get("/{config_str}/stream/{stream_type}/{stream_id}")
async def get_results(config_str: str, stream_type: str, stream_id: str):
    """
    Busca y devuelve los streams disponibles para un item (película o serie).
    """
    if not IS_DB_READY:
        return {
            "streams": [
                {
                    "name": "⚠️ NDK",
                    "title": "🔴 NDK ACTUALIZANDO... 🔴",
                    "description": "El addon está cargando la base de datos. Por favor, espera unos segundos y vuelve a intentarlo.",
                    "url": "https://google.com"
                }
            ]
        }

    start_time = time.time()
    stream_id = stream_id.replace(".json", "")
    config = parse_config(config_str)

    metadata_provider = TMDB(config, http_client)
    media = await metadata_provider.get_metadata(stream_id, stream_type)

    if not media:
        logger.warning(f"No se pudo obtener metadatos para {stream_type} {stream_id}")
        return {"streams": []}

    debrid_service = get_debrid_service(config, http_client, warp_client)
    debrid_name = type(debrid_service).__name__

    # Recuperamos estado de 1fichier del cache
    fichier_status_rd = cache.get(FICHIER_STATUS_KEY) or "up"

    if media.type == "movie":
        search_results = await search_movies(media.id)
    else:
        search_results = await search_tv_shows(media.id, media.season, media.episode)

    if not search_results:
        logger.info(f"No se encontraron resultados para {media.type} {stream_id}. Tiempo total: {time.time() - start_time:.2f}s")
        return {"streams": []}

    tasks = []
    for result in search_results:
        if isinstance(result, tuple):
            link, db_calidad, db_audio, db_info = result
        else:
            link = result
            db_calidad = db_audio = db_info = ""
        
        tasks.append(_process_single_link(debrid_service, link, config, db_calidad, db_audio, db_info))

    processed_results = await asyncio.gather(*tasks)
    
    results_data = []
    
    # is_valid es el tercer valor devuelto por _process_single_link (puede ser el link final o True)
    for link, data, is_valid in processed_results:
        # Si ya está en cache, is_valid es True.
        # Si NO está en cache, is_valid es el final_link (cadena o None).
        
        # Validamos si es válido (o tiene contenido)
        if not is_valid:
            continue
            
        # IMPORTANTE: Si es válido (sea True o link), lo añadimos
        # Antes el filtro era implícito porque final_link era string
        
        filesize_gb = data.get('filesize', 0) / (1024 ** 3)
        if 'maxSize' in config and filesize_gb > int(config['maxSize']):
            continue
        if "selectedQualityExclusion" in config and data.get("quality") in config["selectedQualityExclusion"]:
            continue
            
        results_data.append((link, data))

    results_data.sort(key=lambda x: x[1].get('filesize', 0), reverse=True)

    streams_unfiltered = []
    for link, data in results_data:
        # Codificamos el enlace ORIGINAL, no el unrestricteado
        encoded_link = encodeb64(link)
        encoded_file_name = encodeb64(data.get('nombre_fichero', 'unknown'))
        playback_url = f"{config['addonHost']}/playback/{config_str}/{encoded_file_name}/{encoded_link}"
        stream = post_process_results(link, media, debrid_name, playback_url, data)
        streams_unfiltered.append(stream)

    streams = filter_items(streams_unfiltered, media, config=config)
    parse_to_debrid_stream(streams, config, media, debrid_name, fichier_is_up=(fichier_status_rd == "up"))

    logger.info(f"Resultados encontrados. Tiempo total: {time.time() - start_time:.2f}s")
    return {"streams": streams}


async def _handle_playback(config_str: str, query: str, file_name) -> str:
    """
    Lógica compartida para manejar las peticiones de reproducción.
    SIEMPRE desrestringe en tiempo real usando las credenciales del usuario actual.
    """
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query requerido.")

    config = parse_config(config_str)
    start_time = time.time()

    logger.info("Solicitud de playback recibida. Desrestringiendo en tiempo real...")
    decoded_query = decodeb64(query)
    # file_name se recibe pero no se usa estrictamente para la lógica, es decorativo en la URL
    
    debrid_service = get_debrid_service(config, http_client, warp_client)

    unrestricted_info = await _get_unrestricted_link(debrid_service, decoded_query)
    final_link = unrestricted_info.get('download') if unrestricted_info else None

    if final_link:
        logger.info(f"Enlace desrestringido exitosamente. Tiempo total: {time.time() - start_time:.2f}s")
        return final_link

    logger.error(f"No se pudo obtener el enlace final para la consulta: {query}")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo procesar el enlace.")


@app.get("/playback/{config_str}/{file_name}/{query}")
async def get_playback(config_str: str, file_name, query: str):
    """Redirige al stream final (GET)."""
    final_url = await _handle_playback(config_str, query, file_name)
    return RedirectResponse(url=final_url, status_code=status.HTTP_301_MOVED_PERMANENTLY)


# TODO: Implementar HEAD para playback
@app.head("/playback/{config_str}/{query}")
async def head_playback():
    return Response(status_code=200)


# --- Tareas Programadas (Crons) y Rutas de Administración ---

async def trigger_render_restart():
    """Llama al deploy hook de Render para reiniciar el servicio."""
    if not RENDER_API_URL or not RENDER_AUTH_HEADER.startswith("Bearer"):
        logger.warning("Las variables de entorno de Render no están configuradas. No se puede reiniciar.")
        return False
    
    logger.info("Activando el hook de reinicio de Render...")
    headers = {"accept": "application/json", "authorization": RENDER_AUTH_HEADER, "content-type": "application/json"}
    try:
        response = await http_client.post(RENDER_API_URL, json={"clearCache": "clear"}, headers=headers)
        response.raise_for_status()
        logger.info("✅ Hook de reinicio de Render activado exitosamente.")
        return True
    except httpx.RequestError as e:
        logger.error(f"Error de red al contactar Render: {e}")
        return False
    except httpx.HTTPStatusError as e:
        logger.error(f"Error en la respuesta de Render ({e.response.status_code}): {e.response.text}")
        return False

@crontab("*/5 * * * *", start=not IS_DEV)
async def actualizar_bd():
    """
    Tarea programada que comprueba si hay nuevas versiones y reinicia el servicio si es necesario.
    Compara el timestamp del último commit remoto con la hora de arranque del servidor.
    """
    contenido_actualizado = await comprobar_actualizacion_contenido()
    addon_actualizado = await comprobar_actualizacion_addon()

    if contenido_actualizado or addon_actualizado:
        if contenido_actualizado:
            logger.info("Tarea programada: Nueva versión de CONTENIDO detectada (commit posterior al arranque).")
        if addon_actualizado:
            logger.info("Tarea programada: Nueva versión de ADDON detectada (commit posterior al arranque).")
        
        logger.info("Iniciando secuencia de reinicio...")
        
        if RENDER_API_URL:
            if await trigger_render_restart():
                return

        logger.warning("Render API no disponible o falló. Ejecutando sys.exit(1) para forzar reinicio.")
        sys.exit(1)


@crontab("* * * * *", start=not IS_DEV)
async def ping_service():
    """Mantiene el servicio activo en plataformas como Render haciendo un ping cada minuto."""
    try:
        async with httpx.AsyncClient() as client:
            await client.get(PING_URL)
    except httpx.RequestError as e:
        logger.error(f"Fallo en el ping al servicio: {e}")

@app.get("/fecha")
async def fecha_actualizacion():
    """Devuelve la fecha de la última actualización de la base de datos."""
    try:
        with open(UPDATE_LOG_FILE, 'r') as file:
            lines = file.readlines()
        return {"ultima_actualizacion": lines[-1].strip() if lines else "No hay registros."}
    except FileNotFoundError:
        return {"error": f"El archivo {UPDATE_LOG_FILE} no existe."}

@app.get("/version")
async def version_actualizacion():
    """Devuelve el contenido del archivo de versión."""
    try:
        with open(VERSION_FILE, 'r') as file:
            return {"version_info": file.readlines()}
    except FileNotFoundError:
        return {"error": f"El archivo {VERSION_FILE} no existe."}

# --- Endpoints de Administración (URLs ofuscadas) ---

@app.get(ADMIN_PATH_DB_ENCRYPTED)
async def coger_basedatos_encrypted():
    """Permite descargar el archivo de la base de datos encriptada."""
    if not os.path.exists(DB_ENCRYPTED_PATH):
        raise HTTPException(status_code=404, detail="Archivo no disponible.")
    return FileResponse(DB_ENCRYPTED_PATH, media_type='application/octet-stream')

@app.get(ADMIN_PATH_DB_DECRYPTED)
async def coger_basedatos_decrypted():
    """Permite descargar el archivo de la base de datos descifrada."""
    if not os.path.exists(DB_DECRYPTED_PATH):
        raise HTTPException(status_code=404, detail="Archivo no disponible.")
    return FileResponse(DB_DECRYPTED_PATH, media_type='application/octet-stream')

@app.get(ADMIN_PATH_RESTART)
async def reiniciar_servicio():
    """Reinicia el servicio en Render.com a través de su API."""
    if await trigger_render_restart():
        return {"status": "Servicio reiniciado exitosamente"}
    else:
        raise HTTPException(status_code=500, detail="Fallo al reiniciar el servicio. Revisa los logs.")
