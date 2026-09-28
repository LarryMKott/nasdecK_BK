# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
import functools
import time


# ===================== TTL 缓存装饰器（复用 _FAN_ENUM_CACHE 思路）=====================
def _ttl_cache(ttl):
    def deco(fn):
        store = {}
        @functools.wraps(fn)
        def wrapper(*a, **k):
            key = (a, tuple(sorted(k.items())))
            now = time.time()
            if key in store and now - store[key][0] < ttl:
                return store[key][1]
            val = fn(*a, **k)
            store[key] = (now, val)
            return val
        wrapper.cache_clear = store.clear   # 允许在「写操作成功后」主动失效，避免读到旧值
        return wrapper
    return deco
