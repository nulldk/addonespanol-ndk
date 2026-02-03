import sqlite3
import aiosqlite
import httpx
from contextlib import asynccontextmanager
from utils.logger import setup_logger
from config import DB_DECRYPTED_PATH, DB_ENCRYPTED_PATH
import os


logger = setup_logger(__name__)


@asynccontextmanager
async def get_cursor():
    """
    Proporciona un cursor de base de datos asíncrono gestionando la conexión.

    Yields:
        aiosqlite.Cursor: Un cursor para ejecutar operaciones en la base de datos.
    """
    connection = None
    try:
        connection = await aiosqlite.connect(DB_DECRYPTED_PATH)
        cursor = await connection.cursor()
        yield cursor
        await connection.commit()
    finally:
        if connection:
            await connection.close()

def setup_index(db_path=DB_DECRYPTED_PATH):
    """
    Prepara la base de datos: crea columnas necesarias y los índices para optimizar búsquedas.
    Se ejecuta de forma segura, usando 'IF NOT EXISTS' para no duplicar.
    """
    logger.info("Configurando la base de datos: comprobando columnas e índices...")
    logger.info(f"Ruta de la base de datos: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:        
        # --- Creación de Índices para acelerar búsquedas ---
        logger.info("Creando índices para mejorar el rendimiento de las búsquedas...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_enlaces_pelis_tmdb ON enlaces_pelis(tmdb);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_enlaces_pelis_link ON enlaces_pelis(link);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_enlaces_series_tmdb_season_episode ON enlaces_series(tmdb, temporada, episodio);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_enlaces_series_link ON enlaces_series(link);")
        
        conn.commit()
        logger.info("La configuración de la base de datos ha finalizado con éxito.")
    finally:
        conn.close()


def add_flag(db_path=DB_ENCRYPTED_PATH):
    """
    Prepara la base de datos: crea columnas necesarias y los índices para optimizar búsquedas.
    Se ejecuta de forma segura, usando 'IF NOT EXISTS' para no duplicar.
    """
    logger.info("Configurando la base de datos: comprobando columnas e índices...")
    logger.info(f"Ruta de la base de datos: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        # --- Creación de columnas necesarias ---
        for table in ['enlaces_pelis', 'enlaces_series']:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [column[1] for column in cursor.fetchall()]
            if 'FLAG' not in columns:
                logger.info(f"Creando columna 'FLAG' en la tabla '{table}'.")
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN FLAG INTEGER DEFAULT 0")
            if 'enlace_modificado' not in columns:
                logger.info(f"Creando columna 'enlace_modificado' en la tabla '{table}'.")
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN enlace_modificado TEXT DEFAULT ''")
        
        
        conn.commit()
        logger.info("La adición de columnas FLAG ha finalizado con éxito.")
    finally:
        conn.close()


async def search_movies(id):
    """
    Busca enlaces de películas en la base de datos por su ID de TMDB.

    Args:
        id (str or int): El ID de TMDB de la película.

    Returns:
        list: Una lista de tuplas (link, calidad, audio, info) asociados a la película.
    """
    async with get_cursor() as cursor:
        await cursor.execute("SELECT link, calidad, audio, info FROM enlaces_pelis WHERE tmdb = ?", (id,))
        rows = await cursor.fetchall()
        return [(row[0], row[1], row[2], row[3]) for row in rows]

async def search_tv_shows(id, season, episode):
    """
    Busca enlaces de episodios de series por ID de TMDB, temporada y episodio.

    Args:
        id (str or int): El ID de TMDB de la serie.
        season (str or int): El número de la temporada.
        episode (str or int): El número del episodio.

    Returns:
        list: Una lista de tuplas (link, calidad, audio, info) asociados al episodio.
    """
    async with get_cursor() as cursor:
        await cursor.execute(
            "SELECT link, calidad, audio, info FROM enlaces_series WHERE tmdb = ? AND temporada = ? AND episodio = ?",
            (id, season, episode)
        )
        rows = await cursor.fetchall()
        return [(row[0], row[1], row[2], row[3]) for row in rows]

def getMetadata(link, media_type):
    """
    Obtiene metadatos (calidad, audio, info) para un enlace específico.

    Args:
        link (str): El enlace cuyos metadatos se desean obtener.
        media_type (str): El tipo de medio ('movie' o 'series').

    Returns:
        str: Una cadena de texto representando los metadatos encontrados.
    """
    conn = sqlite3.connect(DB_DECRYPTED_PATH)
    cursor = conn.cursor()
    try:
        table = "enlaces_pelis" if media_type == "movie" else "enlaces_series"
        cursor.execute(f"SELECT calidad, audio, info FROM {table} WHERE link = ?", (link,))
        metadata = cursor.fetchone()
        return str(metadata) if metadata else ""
    finally:
        conn.close()