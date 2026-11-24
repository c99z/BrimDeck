"""System readings: CPU, memory, GPU (nvidia-smi), network, disks, temperatures and busiest processes."""
import collections
import os
import shutil
import subprocess
import threading
import time

import psutil

HISTORY = 60


class Snapshot:
    def __init__(self):
        self.cpu = 0.0
        self.cores = []
        self.cpu_freq = None
        self.cpu_temp = None
        self.load = (0, 0, 0)
        self.mem_used = self.mem_total = 0
        self.mem_pct = 0.0
        self.swap_used = self.swap_total = 0
        self.net_down = self.net_up = 0.0
        self.disk_read = self.disk_write = 0.0
        self.disks = []  # (mount, used, total, pct)
        self.nvme_temp = None
        self.gpu = None  # dict
        self.procs = []  # (name, cpu%, rss, pid)
        self.uptime = 0
        self.hist = {}


class SystemMonitor:
    """Samples once a second on a background thread. Expensive parts (processes, GPU, disks) only
    while `detailed` is set, i.e. while the monitor page is open."""

    def __init__(self, on_update):
        self.on_update = on_update
        self.detailed = False
        self.snap = Snapshot()
        self.hist = {k: collections.deque(maxlen=HISTORY)
                     for k in ("cpu", "mem", "down", "up", "gpu", "read", "write")}
        self._prev_net = None
        self._prev_disk = None
        self._prev_t = None
        self._procs = {}
        self._gpu_next = 0
        self._disk_next = 0
        self._stop = False
        self._wake = threading.Event()
        self._nvsmi = shutil.which("nvidia-smi")
        self._ncpu = psutil.cpu_count() or 1
        psutil.cpu_percent(percpu=True)

    def start(self):
        threading.Thread(target=self._run, name="sysmon", daemon=True).start()

    def stop(self):
        self._stop = True
        self._wake.set()

    def wake(self):
        self._wake.set()

    def _run(self):
        while not self._stop:
            t0 = time.monotonic()
            try:
                self._sample()
                self.on_update(self.snap)
            except Exception as e:
                print("brimdeck: sysmon", e)
            if self._wake.wait(max(0.2, (1.0 if self.detailed else 2.0) - (time.monotonic() - t0))):
                self._wake.clear()
                if self.detailed and not self._procs:
                    self._processes()  # prime cpu counters so the list fills on the next sample
                    time.sleep(0.5)

    def _sample(self):
        s = Snapshot()
        now = time.monotonic()
        cores = psutil.cpu_percent(percpu=True)
        s.cores = cores
        s.cpu = sum(cores) / max(1, len(cores))
        try:
            f = psutil.cpu_freq()
            s.cpu_freq = f.current if f else None
        except Exception:
            pass
        s.load = os.getloadavg()
        vm = psutil.virtual_memory()
        s.mem_total, s.mem_used, s.mem_pct = vm.total, vm.total - vm.available, 100.0 * (vm.total - vm.available) / vm.total
        sw = psutil.swap_memory()
        s.swap_used, s.swap_total = sw.used, sw.total
        net = psutil.net_io_counters(pernic=True)
        rx = sum(v.bytes_recv for k, v in net.items() if k != "lo" and not k.startswith(("docker", "veth", "br-", "virbr")))
        tx = sum(v.bytes_sent for k, v in net.items() if k != "lo" and not k.startswith(("docker", "veth", "br-", "virbr")))
        try:
            dio = psutil.disk_io_counters()
            rd, wr = (dio.read_bytes, dio.write_bytes) if dio else (0, 0)
        except Exception:
            rd = wr = 0
        if self._prev_t is not None:
            dt = max(1e-3, now - self._prev_t)
            s.net_down = max(0.0, (rx - self._prev_net[0]) / dt)
            s.net_up = max(0.0, (tx - self._prev_net[1]) / dt)
            s.disk_read = max(0.0, (rd - self._prev_disk[0]) / dt)
            s.disk_write = max(0.0, (wr - self._prev_disk[1]) / dt)
        self._prev_net, self._prev_disk, self._prev_t = (rx, tx), (rd, wr), now
        try:
            temps = psutil.sensors_temperatures()
        except Exception:
            temps = {}
        for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            if temps.get(key):
                pkg = [t for t in temps[key] if t.label.lower().startswith(("package", "tctl", "tdie"))]
                s.cpu_temp = (pkg[0] if pkg else max(temps[key], key=lambda t: t.current)).current
                break
        if temps.get("nvme"):
            comp = [t for t in temps["nvme"] if t.label.lower().startswith("composite")]
            s.nvme_temp = (comp[0] if comp else temps["nvme"][0]).current
        s.uptime = time.time() - psutil.boot_time()

        if self.detailed:
            s.procs = self._processes()
            if now >= self._disk_next or not self.snap.disks:
                s.disks = self._disks()
                self._disk_next = now + 10
            else:
                s.disks = self.snap.disks
        else:
            s.disks = self.snap.disks
        if self._nvsmi and (self.detailed or self.snap.gpu is None) and now >= self._gpu_next:
            s.gpu = self._gpu() or self.snap.gpu
            self._gpu_next = now + 2
        else:
            s.gpu = self.snap.gpu

        h = self.hist
        h["cpu"].append(s.cpu)
        h["mem"].append(s.mem_pct)
        h["down"].append(s.net_down)
        h["up"].append(s.net_up)
        h["read"].append(s.disk_read)
        h["write"].append(s.disk_write)
        h["gpu"].append((s.gpu or {}).get("util", 0) or 0)
        s.hist = {k: list(v) for k, v in h.items()}
        self.snap = s

    def _processes(self):
        seen = set()
        rows = []
        for p in psutil.process_iter(["name", "memory_info"]):
            pid = p.pid
            seen.add(pid)
            proc = self._procs.get(pid)
            if proc is None:
                proc = self._procs[pid] = p
                try:
                    proc.cpu_percent(None)
                except psutil.Error:
                    pass
                continue
            try:
                cpu = proc.cpu_percent(None) / self._ncpu
                mi = p.info.get("memory_info")
                rows.append((p.info.get("name") or str(pid), cpu, mi.rss if mi else 0, pid))
            except psutil.Error:
                pass
        for pid in list(self._procs):
            if pid not in seen:
                del self._procs[pid]
        # Merge processes of the same app (chrome, code...) like a task manager does.
        merged = {}
        for name, cpu, rss, pid in rows:
            m = merged.setdefault(name, [name, 0.0, 0, pid, 0])
            m[1] += cpu
            m[2] += rss
            m[4] += 1
        return sorted((tuple(v) for v in merged.values()), key=lambda r: -r[1])[:40]

    @staticmethod
    def _disks():
        out = []
        seen = set()
        for part in psutil.disk_partitions(all=False):
            if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay", "vfat") or part.mountpoint.startswith(("/snap", "/boot", "/var/snap")):
                continue
            if part.device in seen:
                continue
            seen.add(part.device)
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            if u.total < 1 << 30:
                continue
            out.append((part.mountpoint, u.used, u.total, u.percent))
        return out[:4]

    @staticmethod
    def _gpu():
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,fan.speed",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
            line = r.stdout.strip().splitlines()[0]
            parts = [x.strip() for x in line.split(",")]

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            return {"name": parts[0].replace("NVIDIA ", "").replace("GeForce ", ""), "util": num(parts[1]),
                    "mem_used": (num(parts[2]) or 0) * 1048576, "mem_total": (num(parts[3]) or 0) * 1048576,
                    "temp": num(parts[4]), "power": num(parts[5]), "fan": num(parts[6])}
        except Exception:
            return None
