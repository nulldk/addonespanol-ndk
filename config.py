import os

# --- Constantes de configuración ---
# Secrets come from the environment (or a gitignored .env). Nothing is stored in the repo.

VERSION = "1.0.0"
IS_DEV = os.getenv("NODE_ENV") == "development"
IS_COMMUNITY_VERSION = os.getenv("IS_COMMUNITY_VERSION") == "true"

ROOT_PATH = os.environ.get("ROOT_PATH", "")

DEBRID_API_KEY = os.getenv('DEBRID_API_KEY')
FICHIER_API_KEY = os.getenv('FICHIER_API_KEY')
ENCRYPTION_KEY_B64 = os.getenv('ENCRYPTION_KEY_B64')
ZIP_DECODE_BASE64 = os.getenv('ZIP_DECODE_BASE64')
ADMIN_PATH_DB_ENCRYPTED = os.getenv("ADMIN_PATH_DB_ENCRYPTED")
ADMIN_PATH_DB_DECRYPTED = os.getenv("ADMIN_PATH_DB_DECRYPTED")
ADMIN_PATH_RESTART = os.getenv("ADMIN_PATH_RESTART")
RENDER_API_KEY = os.getenv('RENDER_API_KEY')
RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# --- Constantes de archivos y URLs ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_PREFIX = "tmp" if not IS_DEV else ""
WORKING_PATH = os.path.join(BASE_DIR, DB_PATH_PREFIX)
DATA_PATH = os.path.join(WORKING_PATH, "Mipal2025-main")

DB_ENCRYPTED_PATH = os.path.join(WORKING_PATH, "Mipal2025-main", "92b33381-pl3-42a1-bee0-bbb9d132e83f.tmp")
DB_DECRYPTED_PATH = os.path.join(WORKING_PATH, "Mipal2025-main", "bd.tmp")
UPDATE_LOG_FILE = os.path.join(WORKING_PATH, "actualizar.txt")
VERSION_FILE = os.path.join(WORKING_PATH, "version.txt")

