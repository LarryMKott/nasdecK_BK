"""存储/Docker/端口/网络四个面板用例单测：mock 领域接口，验证编排与容错语义。"""
import os

import pytest

from backend.application.usecases.docker_usecase import DockerUseCase
from backend.application.usecases.network_usecase import NetworkUseCase
from backend.application.usecases.ports_usecase import PortsUseCase
from backend.application.usecases.storage_usecase import StorageUseCase
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.network_service import INetworkService


class BaseCollector(IHardwareCollector):
    """最小假采集器：默认返回空载荷，按需覆写/注入异常。"""

    def __init__(self, **kw):
        self._kw = kw

    def _raise(self, key):
        if key in self._kw:
            raise self._kw[key]

    def get_system(self, force=False): return {}
    def get_disks(self, force=False): return []
    def get_raid_card(self): return {}
    def get_storage(self):
        self._raise("storage_raise")
        return self._kw.get("storage", {"total": "10T"})

    def get_volume_map(self):
        self._raise("vmap_raise")
        return self._kw.get("vmap", {"volumes": [1]})

    def get_docker(self):
        self._raise("docker_raise")
        return self._kw.get("docker", {"containers": [1]})

    def get_listening_ports(self):
        self._raise("ports_raise")
        return self._kw.get("ports", [1, 2])

    def get_cpu_usage(self): return None
    def get_board(self): return {}
    def get_memory_modules(self): return []
    def get_network_nics(self): return []
    def get_rapl_power(self): return {}
    def get_gpu_live(self): return []
    def get_realtime_metrics(self): return {"net": [], "disk": []}
    # ---- 阶段 3 批 A 第三单接口扩展（本组用例不触达，补齐抽象方法即可）----
    def get_disks_meta_map(self): return {}
    def get_live_mem_percent(self): return None
    def get_load_avg(self): return [0.1, 0.2, 0.3]
    def get_cpu_freq_mhz(self): return None
    def is_standalone_disk(self, dev): return (True, "")
    def get_temp_snapshot(self): return {}
    def disk_friendly_info(self, dev): return {"name": dev, "model": "", "category": "", "is_system": False}
    def get_disk_temps_cached(self, devs): return {}
    def list_all_disk_devs(self): return []
    def invalidate_system_cache(self): pass


class FakeNetwork(INetworkService):
    def __init__(self, **kw):
        self._kw = kw

    def nics(self):
        if "nics_raise" in self._kw:
            raise self._kw["nics_raise"]
        return self._kw.get("nics", [{"name": "eth0"}])

    def set_wol(self, name, enable):
        return self._kw["wol_result"]

    def proc_comm(self, pid):
        if "comm_raise" in self._kw:
            raise self._kw["comm_raise"]
        return self._kw.get("comm", "my-app")  # 默认非关键进程

    def kill_term(self, pid, timeout=5):
        if "kill_raise" in self._kw:
            raise self._kw["kill_raise"]
        return self._kw.get("kill_out", "killed\n")

    def port_app_icon(self, appid):
        # 模拟白名单语义：仅 apps 集合内的 appid 有图标
        if appid not in self._kw.get("apps", set()):
            return None
        return self._kw.get("icon")


# ---------- 存储卷 ----------

def test_storage_panel_ok():
    r = StorageUseCase(BaseCollector()).panel()
    assert list(r.keys()) == ["storage", "time", "elapsed"]
    assert r["storage"]["volume_map"] == {"volumes": [1]}


def test_storage_volume_map_failure_degrades_and_logs():
    logs = []
    err = RuntimeError("boom")
    r = StorageUseCase(BaseCollector(vmap_raise=err), log=logs.append).panel()
    assert r["storage"]["volume_map"] == {"volumes": [], "loose": [], "error": "boom"}
    assert logs == ["volume_map failed: boom"]


def test_storage_top_failure_error_shape():
    r = StorageUseCase(BaseCollector(storage_raise=RuntimeError("x"))).panel()
    assert set(r.keys()) == {"error", "time"} and r["error"] == "x"


# ---------- Docker ----------

def test_docker_panel_ok_and_failure():
    r = DockerUseCase(BaseCollector()).panel()
    assert list(r.keys()) == ["docker", "time", "elapsed"]
    r2 = DockerUseCase(BaseCollector(docker_raise=RuntimeError("d"))).panel()
    assert set(r2.keys()) == {"error", "time"} and r2["error"] == "d"


# ---------- 端口占用 ----------

def test_ports_list_propagates_for_500_mapping():
    with pytest.raises(RuntimeError):
        PortsUseCase(BaseCollector(ports_raise=RuntimeError("p")), FakeNetwork()).list_panel()
    assert PortsUseCase(BaseCollector(), FakeNetwork()).list_panel()["ports"] == [1, 2]


def test_app_icon_passthrough():
    net = FakeNetwork(apps={"homebox"}, icon=("/a.png", "image/png"))
    uc = PortsUseCase(BaseCollector(), net)
    assert uc.app_icon("homebox") == ("/a.png", "image/png")
    assert uc.app_icon("unknown") is None  # 白名单外 → 端点 404


def test_release_port_safety_matrix():
    net = FakeNetwork()
    uc = PortsUseCase(BaseCollector(), net)
    # 非法 PID（无 blocked 标记）
    assert uc.release_port("abc") == (400, {"ok": False, "error": "PID 非法"})
    assert uc.release_port("0")[1] == {"ok": False, "error": "PID 非法"}
    assert uc.release_port("1")[1] == {"ok": False, "error": "PID 非法"}
    # 自身进程 → blocked
    s, b = uc.release_port(str(os.getpid()))
    assert s == 400 and b["blocked"] is True and "自身" in b["error"]
    # 关键进程名单 → blocked
    s, b = PortsUseCase(BaseCollector(), FakeNetwork(comm="systemd")).release_port("123")
    assert s == 400 and b["blocked"] is True and "系统关键进程" in b["error"]
    # 正常释放
    s, b = uc.release_port("456")
    assert s == 200 and b == {"ok": True, "pid": 456, "process": "my-app", "output": "killed\n"}
    # 终止失败 → 500
    net2 = FakeNetwork(kill_raise=RuntimeError("perm"))
    s, b = PortsUseCase(BaseCollector(), net2).release_port("456")
    assert s == 500 and b["error"] == "释放失败：perm"


# ---------- 网络 ----------

def test_network_nics_propagates_for_500_mapping():
    with pytest.raises(RuntimeError):
        NetworkUseCase(FakeNetwork(nics_raise=RuntimeError("n"))).nics()
    assert NetworkUseCase(FakeNetwork()).nics()["nics"] == [{"name": "eth0"}]


def test_set_wol_mapping():
    uc = NetworkUseCase(FakeNetwork(wol_result={"ok": True, "phy": "eth0", "enabled": True, "err": None}))
    assert uc.set_wol({"name": "eth0", "enable": True}) == (200, {"ok": True, "name": "eth0", "enabled": True})
    uc2 = NetworkUseCase(FakeNetwork(wol_result={"ok": False, "phy": None, "enabled": None, "err": "缺少网卡名"}))
    assert uc2.set_wol({}) == (400, {"ok": False, "error": "缺少网卡名"})
    uc3 = NetworkUseCase(FakeNetwork(wol_result={"ok": False, "phy": "eth0", "enabled": None, "err": None}))
    s, b = uc3.set_wol({"name": "eth0", "enable": True})
    assert s == 400 and b["error"] == "设置失败"
