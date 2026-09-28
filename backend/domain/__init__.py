# 领域层（整洁架构最内层）：实体 / 抽象接口契约 / 纯业务规则。
# 铁律：只允许导入 Python 标准库（含本包内部），禁止 FastAPI / SQLAlchemy /
# subprocess / 任何 backend 外层模块 —— scripts/check_layers.py G4 门禁强制。
