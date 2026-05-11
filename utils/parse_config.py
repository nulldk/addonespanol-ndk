import hashlib
import json

from utils.cache import cache
from utils.string_encoding import decodeb64

PARSED_CONFIG_TTL = 60 * 60


def parse_config(b64config):
    cache_key = _parse_config_cache_key(b64config)
    cached_config = cache.get(cache_key)
    if cached_config is not None:
        return cached_config

    config = json.loads(decodeb64(b64config))
    cache.set(cache_key, config, ttl=PARSED_CONFIG_TTL)
    return config

def _parse_config_cache_key(b64config):
    key_hash = hashlib.sha256(b64config.encode("utf-8")).hexdigest()
    return f"config:parsed:{key_hash}"
