import time
from collections import OrderedDict
from functools import wraps
from threading import Lock


_cache_store = OrderedDict()
_cache_lock = Lock()
_cache_generation = 0
_MAX_ENTRIES = 256


def ttl_cache(seconds=60):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (
                func.__module__,
                func.__name__,
                args,
                tuple(sorted(kwargs.items())),
            )
            with _cache_lock:
                now = time.monotonic()
                cached = _cache_store.get(key)
                if cached and now < cached[1]:
                    _cache_store.move_to_end(key)
                    return cached[0]
                _cache_store.pop(key, None)
                generation = _cache_generation

            result = func(*args, **kwargs)
            with _cache_lock:
                if generation == _cache_generation:
                    _cache_store[key] = (result, time.monotonic() + seconds)
                    _cache_store.move_to_end(key)
                    if len(_cache_store) > _MAX_ENTRIES:
                        _cache_store.popitem(last=False)
            return result

        def invalidate():
            global _cache_generation
            with _cache_lock:
                _cache_generation += 1
                for key in list(_cache_store):
                    if key[:2] == (func.__module__, func.__name__):
                        _cache_store.pop(key)

        wrapper.invalidate = invalidate
        return wrapper

    return decorator


def invalidate_all():
    global _cache_generation
    with _cache_lock:
        _cache_generation += 1
        _cache_store.clear()
