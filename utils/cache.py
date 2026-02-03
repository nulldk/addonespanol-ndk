import time
from typing import Dict, Any, Optional

class CacheManager:
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def set(self, key: str, value: Any, ttl: int = 1800) -> None:
        self._store[key] = {
            'data': value,
            'expire': time.time() + ttl
        }

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        
        if time.time() > item['expire']:
            del self._store[key]
            return None
            
        return item['data']

    def delete(self, key: str) -> None:
        if key in self._store:
            del self._store[key]

    def clear(self) -> None:
        self._store.clear()

cache = CacheManager()
