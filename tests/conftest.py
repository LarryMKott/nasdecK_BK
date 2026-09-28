import sys
from pathlib import Path

# 仓库根入 sys.path：保证 tests/ 树无论从根目录、IDE 还是 CI 触发都能 import backend.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
