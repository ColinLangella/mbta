from dataclasses import dataclass
from typing import Dict, Optional, Any

from datetime import datetime, timedelta
import threading


@dataclass
class CacheEntry:
    """Cache entry with metadata for HTTP caching."""
    data: Any
    timestamp: datetime
    last_modified: Optional[str] = None


class CacheManager:
    """Manages caching with thread safety, ttl, and HTTP last modified headers."""


    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()


    def get(self, key: str, ttl_seconds: int = 3600) -> tuple[ bool, Optional[CacheEntry] ]:
        """Get if item is expired and item info."""
        with self._lock:
            if key not in self._cache: return False, None
            entry = self._cache[key]

            # Check if entry has expired based on TTL or Expires header
            now = datetime.now()
            ttl_expired = now - entry.timestamp > timedelta(seconds=ttl_seconds)\

            if ttl_expired: return True, entry
            return False, entry


    def set(self, key: str, data: Any, last_modified: str = None) -> None:
        """Cache data with HTTP headers and timestamp."""
        with self._lock:
            self._cache[key] = CacheEntry(
                data=data,
                timestamp=datetime.now(),
                last_modified=last_modified )


    def refresh(self, key: str) -> None:
        """Refresh the cache entry timestamp."""
        with self._lock:
            if key in self._cache:
                self._cache[key].timestamp = datetime.now()


    def get_cache_headers(self, key: str) -> Dict[str, str]:
        """Get cache headers for conditional requests."""
        with self._lock:
            if key not in self._cache:
                return {}

            entry = self._cache[key]
            headers = {}

            if entry.last_modified:
                headers['if-modified-since'] = entry.last_modified

            return headers


    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        with self._lock:
            total_entries = len(self._cache)
            has_last_modified = 0

            now = datetime.now()
            for entry in self._cache.values():
                if entry.last_modified:
                    has_last_modified += 1

            return {
                'total_entries': total_entries,
                'entries_with_last_modified': has_last_modified,
                'cache_efficiency': {
                    'last_modified_coverage': has_last_modified / total_entries if total_entries > 0 else 0 } }


    def invalidate_cache_pattern(self, pattern: str) -> int:
        """Invalidate cache entries matching a pattern. Returns number of entries removed."""
        with self._lock:
            keys_to_remove = [key for key in self._cache.keys() if pattern in key]
            for key in keys_to_remove:
                del self._cache[key]
            return len(keys_to_remove)


if __name__ == "__main__":
    # Example usage
    cache_manager = CacheManager()
    cache_manager.set("example_key", "example_data", last_modified="Wed, 21 Oct 2015 07:28:00 GMT")
    entry = cache_manager.get("example_key")
    print(entry.data if entry else "Cache miss")
    headers = cache_manager.get_cache_headers("example_key")
    print(headers)
    stats = cache_manager.get_cache_stats()
    print(stats)
    removed_count = cache_manager.invalidate_cache_pattern("example")
    print(f"Removed {removed_count} entries matching pattern.")
