# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
import json


# ===================== JSON 配置读写（统一）=====================
def _load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        # default=None 作哨兵时不做类型校验（否则 isinstance(dict, NoneType) 恒为 False，
        # 会把已存的配置误判丢弃，导致 disk_temp/sys_temp 配置永远读不回来）；
        # 传具体类型（如 {}）的调用者仍保留类型校验，非期望类型时回退默认值。
        if default is None:
            return d
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default

def _save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
