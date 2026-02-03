import httpx
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from utils.logger import setup_logger

from config import (
    WORKING_PATH,
    CONTENIDO_REPO_URL
)

logger = setup_logger(__name__)

# --- Constantes para el CONTENIDO (Base de Datos) ---
CONTENIDO_TIMESTAMP_FILE = os.path.join(WORKING_PATH,'contenido_last_update.txt')

# --- Constantes para el ADDON (Código) ---
ADDON_TIMESTAMP_FILE = os.path.join(WORKING_PATH,'addon_last_update.txt')
ADDON_REPO_URL = "https://github.com/nulldk/addonespanol-ndk/commits/main.atom"

def establecer_timestamp_arranque(tipo_contenido):
    """Establece el timestamp de arranque a la hora actual (UTC)."""
    fichero_timestamp = CONTENIDO_TIMESTAMP_FILE if tipo_contenido == "CONTENIDO" else ADDON_TIMESTAMP_FILE
    # Usar UTC explícitamente para evitar problemas de offset-naive vs offset-aware
    ahora = datetime.now(timezone.utc).isoformat()
    with open(fichero_timestamp, 'w') as f:
        f.write(ahora)
    logger.info(f"Timestamp de arranque establecido para {tipo_contenido}: {ahora}")
    return ahora

async def _comprobar_remoto(url_atom, fichero_timestamp, tipo_contenido):
    """Compara el timestamp del último commit remoto con la hora de arranque local."""
    logger.info(f"Comprobando actualizaciones del {tipo_contenido}...")
    
    hora_arranque = None
    if os.path.exists(fichero_timestamp):
        with open(fichero_timestamp, 'r') as f:
            hora_arranque_str = f.read().strip()
            try:
                hora_arranque = datetime.fromisoformat(hora_arranque_str)
                # Si el timestamp leído no tiene timezone, asumimos UTC
                if hora_arranque.tzinfo is None:
                    hora_arranque = hora_arranque.replace(tzinfo=timezone.utc)
            except ValueError:
                hora_arranque = None
    
    if not hora_arranque:
        logger.warning(f"No se encontró timestamp de arranque para {tipo_contenido}. Estableciendo ahora...")
        hora_arranque = datetime.now(timezone.utc)
        with open(fichero_timestamp, 'w') as f:
            f.write(hora_arranque.isoformat())

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url_atom, timeout=30)
            response.raise_for_status()
        
        root = ET.fromstring(response.text)
        namespace = '{http://www.w3.org/2005/Atom}'
        latest_entry = root.find(f'{namespace}entry')
        if latest_entry is None:
            logger.warning(f"No se encontró 'entry' en el feed de commits para {tipo_contenido}.")
            return False

        latest_remote_timestamp_str = latest_entry.find(f'{namespace}updated').text.strip()
        # Convertir Z a +00:00 para compatibilidad con fromisoformat en versiones antiguas de Python
        latest_remote_timestamp = datetime.fromisoformat(latest_remote_timestamp_str.replace('Z', '+00:00'))
        
        # Asegurar que ambos timestamps tengan zona horaria
        if latest_remote_timestamp.tzinfo is None:
            latest_remote_timestamp = latest_remote_timestamp.replace(tzinfo=timezone.utc)
            
    except Exception as e:
        logger.error(f"No se pudo comprobar la actualización para {tipo_contenido}: {e}")
        return False

    logger.info(f"Hora de arranque {tipo_contenido}: {hora_arranque}")
    logger.info(f"Último commit remoto {tipo_contenido}: {latest_remote_timestamp}")

    if latest_remote_timestamp > hora_arranque:
        logger.info(f"¡Nueva actualización de {tipo_contenido} detectada! (commit posterior al arranque)")
        return True
    
    return False

async def comprobar_actualizacion_contenido():
    return await _comprobar_remoto(CONTENIDO_REPO_URL, CONTENIDO_TIMESTAMP_FILE, "CONTENIDO")

async def comprobar_actualizacion_addon():
    return await _comprobar_remoto(ADDON_REPO_URL, ADDON_TIMESTAMP_FILE, "ADDON")
