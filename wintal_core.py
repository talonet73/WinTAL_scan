"""
WinTAL_scan - motore: tool di sistema + agente AI (Ollama).
Nessuna dipendenza dalla GUI: la GUI registra le callback di conferma e stato.
"""
import os
import sys
import json
import time
import heapq
import shutil
import fnmatch
import hashlib
import platform
import subprocess
import threading
from pathlib import Path
from collections import defaultdict

import psutil

IS_WINDOWS = os.name == "nt"
SYSTEM_DRIVE = (os.environ.get("SystemDrive", "C:") + "\\") if IS_WINDOWS else "/"
HOME = str(Path.home())
SKIP_DIRS = {"$Recycle.Bin", "System Volume Information", "$WinREAgent", "Config.Msi"}
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW: niente finestre nere

# ===================== CONFIG =====================
CONFIG_DIR = Path(os.environ.get("APPDATA", HOME)) / "WinTAL_scan"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_CONFIG = {
    "model": "qwen2.5:7b",
    "ollama_host": "http://localhost:11434",
    "update_interval": 2.0,
    "show_widget": True,
    "max_history": 20,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def resource_path(relative_path):
    """Percorso risorse, funziona sia in sviluppo sia con PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def _run(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW, errors="replace")


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def _walk(path, skip=()):
    skip = SKIP_DIRS | set(skip)
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith("$")]
        yield root, dirs, files


# ===================== MONITOR (sensori) =====================
class SensorCache:
    """Legge i sensori in un thread separato, così la GUI non si blocca mai."""

    def __init__(self, interval=2.0):
        self.interval = interval
        self.data = {"ram": 0.0, "cpu": 0.0, "disk": 0.0, "disk_free": 0.0, "temp": None}
        self._temp_t = 0
        psutil.cpu_percent(None)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                d = dict(self.data)
                d["ram"] = psutil.virtual_memory().percent
                d["cpu"] = psutil.cpu_percent(None)
                try:
                    du = psutil.disk_usage(SYSTEM_DRIVE)
                    d["disk"], d["disk_free"] = du.percent, du.free / 1024**3
                except Exception:
                    pass
                if time.time() - self._temp_t > 15:  # la temperatura è costosa: ogni 15 s
                    self._temp_t = time.time()
                    d["temp"] = read_cpu_temperature()
                self.data = d
            except Exception:
                pass
            time.sleep(self.interval)


def read_cpu_temperature():
    """Temperatura CPU in °C o None. Su Windows usa WMI (spesso richiede admin)."""
    try:
        temps = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
        for entries in temps.values():
            for e in entries:
                if e.current and e.current > 0:
                    return float(e.current)
    except Exception:
        pass
    if IS_WINDOWS:
        ps = ("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
              "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty CurrentTemperature")
        try:
            out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=8).stdout.split()
            vals = [int(v) / 10 - 273.15 for v in out if v.strip().isdigit()]
            vals = [v for v in vals if 5 < v < 120]
            if vals:
                return max(vals)
        except Exception:
            pass
    return None


# ===================== TOOL DI LETTURA =====================
def get_system_info():
    """Info complete: OS, CPU, RAM, dischi, GPU, uptime."""
    vm = psutil.virtual_memory()
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "computer": platform.node(),
        "processore": platform.processor() or platform.machine(),
        "core_fisici": psutil.cpu_count(logical=False),
        "thread": psutil.cpu_count(),
        "cpu_uso_percento": psutil.cpu_percent(interval=0.5),
        "ram_totale_gb": round(vm.total / 1024**3, 2),
        "ram_disponibile_gb": round(vm.available / 1024**3, 2),
        "ram_uso_percento": vm.percent,
        "acceso_da_ore": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    disks = []
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            disks.append({"disco": part.device, "fs": part.fstype,
                          "totale_gb": round(u.total / 1024**3, 1),
                          "libero_gb": round(u.free / 1024**3, 1), "uso_percento": u.percent})
        except Exception:
            pass
    info["dischi"] = disks
    info["gpu"] = _gpu_info()
    t = read_cpu_temperature()
    info["temperatura_cpu"] = f"{t:.0f}°C" if t else "non disponibile (prova ad avviare come amministratore)"
    return json.dumps(info, indent=2, ensure_ascii=False)


def _gpu_info():
    try:
        r = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu",
                  "--format=csv,noheader"], timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    if IS_WINDOWS:
        try:
            r = _run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' (' + [math]::Round($_.AdapterRAM/1GB,1) + ' GB)' }"],
                     timeout=10)
            if r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return "Non disponibile"


def get_cpu_temperature():
    t = read_cpu_temperature()
    return f"{t:.0f}°C" if t else "Non disponibile (su Windows serve spesso avviare come amministratore)"


def top_processes(sort_by="ram", top_n=10):
    """Processi che consumano più RAM o CPU."""
    top_n = int(top_n)
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            procs.append(p)
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.8)
    rows = []
    for p in procs:
        try:
            rows.append((p.info["name"], p.pid, p.info["memory_info"].rss, p.cpu_percent(None)))
        except Exception:
            pass
    key = 3 if str(sort_by).lower().startswith("c") else 2
    rows.sort(key=lambda r: r[key], reverse=True)
    return "\n".join(f"{n} (PID {pid}) - RAM {_fmt_size(m)} - CPU {c:.0f}%"
                     for n, pid, m, c in rows[:top_n]) or "Nessun processo leggibile"


def startup_programs():
    """Programmi che partono all'avvio di Windows."""
    if not IS_WINDOWS:
        return "Disponibile solo su Windows"
    ps = "Get-CimInstance Win32_StartupCommand | ForEach-Object { $_.Name + ' -> ' + $_.Command }"
    try:
        out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=20).stdout.strip()
        return out or "Nessun programma di avvio trovato"
    except Exception as e:
        return f"Errore: {e}"


def search_files(name_pattern, path=SYSTEM_DRIVE, max_results=30):
    """Cerca file per nome (wildcard * e ?)."""
    max_results = int(max_results)
    if not os.path.exists(path):
        return f"Percorso non trovato: {path}"
    pat = name_pattern.lower()
    if not any(c in pat for c in "*?"):
        pat = f"*{pat}*"
    results = []
    for root, _, files in _walk(path):
        for f in files:
            if fnmatch.fnmatch(f.lower(), pat):
                fp = os.path.join(root, f)
                try:
                    results.append(f"{_fmt_size(os.path.getsize(fp))} - {fp}")
                except OSError:
                    results.append(fp)
                if len(results) >= max_results:
                    return "\n".join(results) + f"\n\n(Mostrati i primi {max_results} risultati)"
    return "\n".join(results) if results else f"Nessun file '{name_pattern}' trovato in {path}"


def find_large_files(path=SYSTEM_DRIVE, top_n=20, min_size_mb=100):
    """I file più grandi di un percorso (scansione completa, tiene in memoria solo la top N)."""
    top_n, min_b = int(top_n), int(min_size_mb) * 1024 * 1024
    if not os.path.exists(path):
        return f"Percorso non trovato: {path}"
    heap = []
    for root, _, files in _walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                s = os.path.getsize(fp)
            except OSError:
                continue
            if s >= min_b:
                if len(heap) < top_n:
                    heapq.heappush(heap, (s, fp))
                elif s > heap[0][0]:
                    heapq.heapreplace(heap, (s, fp))
    rows = sorted(heap, reverse=True)
    return "\n".join(f"{_fmt_size(s)} - {p}" for s, p in rows) or "Nessun file oltre la soglia"


def find_same_name_files(path=HOME, max_groups=20):
    """File con lo stesso nome in cartelle diverse."""
    names = defaultdict(list)
    if not os.path.exists(path):
        return f"Percorso non trovato: {path}"
    for root, _, files in _walk(path, skip={"AppData", "node_modules", ".git"}):
        for f in files:
            names[f.lower()].append(os.path.join(root, f))
    dups = [(n, p) for n, p in names.items() if len(p) > 1]
    if not dups:
        return "Nessun file con lo stesso nome in cartelle diverse."
    dups.sort(key=lambda x: len(x[1]), reverse=True)
    out = []
    for n, paths in dups[:int(max_groups)]:
        out.append(f"{n} ({len(paths)} copie)")
        out += [f"  - {p}" for p in paths[:10]]
    return "\n".join(out)


def find_duplicates(path=HOME, max_files=20000):
    """Duplicati veri: stesso contenuto (prima per dimensione, poi hash completo)."""
    if not os.path.exists(path):
        return f"Percorso non trovato: {path}"
    by_size, count = defaultdict(list), 0
    for root, _, files in _walk(path, skip={"AppData", "node_modules", ".git"}):
        for f in files:
            fp = os.path.join(root, f)
            try:
                s = os.path.getsize(fp)
            except OSError:
                continue
            if s > 4096:
                by_size[s].append(fp)
                count += 1
        if count >= int(max_files):
            break

    def h(fp, limit=None):
        d = hashlib.blake2b(digest_size=16)
        with open(fp, "rb") as fh:
            read = 0
            while chunk := fh.read(1024 * 1024):
                d.update(chunk)
                read += len(chunk)
                if limit and read >= limit:
                    break
        return d.hexdigest()

    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        quick = defaultdict(list)
        for p in paths:
            try:
                quick[h(p, 64 * 1024)].append(p)
            except OSError:
                pass
        for cand in quick.values():
            if len(cand) < 2:
                continue
            full = defaultdict(list)
            for p in cand:
                try:
                    full[h(p)].append(p)
                except OSError:
                    pass
            groups += [(size, g) for g in full.values() if len(g) > 1]
    if not groups:
        return f"Nessun duplicato trovato ({count} file analizzati)."
    groups.sort(key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)
    wasted = sum(s * (len(g) - 1) for s, g in groups)
    out = [f"{len(groups)} gruppi di duplicati, spazio recuperabile ~{_fmt_size(wasted)}\n"]
    for s, g in groups[:15]:
        out.append(f"{_fmt_size(s)} x {len(g)}:")
        out += [f"  - {p}" for p in g]
    return "\n".join(out)


def list_heavy_installs(top_n=15):
    """Cartelle più pesanti in Program Files."""
    bases = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    sizes = []
    for base in filter(os.path.isdir, set(bases)):
        for item in os.scandir(base):
            if item.is_dir(follow_symlinks=False):
                total = 0
                for root, _, files in _walk(item.path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                sizes.append((total, item.path))
    sizes.sort(reverse=True)
    return "\n".join(f"{_fmt_size(s)} - {p}" for s, p in sizes[:int(top_n)]) or "Nessuna installazione trovata"


def preview_cleanup():
    """Stima lo spazio recuperabile SENZA cancellare nulla."""
    rows = []
    for label, dirs in _cleanup_targets().items():
        total = n = 0
        for d in dirs:
            for root, _, files in _walk(d):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                        n += 1
                    except OSError:
                        pass
        rows.append(f"{label}: {n} file, ~{_fmt_size(total)}")
    rows.append(f"Cestino: {_recycle_bin_size()}")
    return "\n".join(rows)


# ===================== TOOL CHE MODIFICANO (richiedono conferma) =====================
def _cleanup_targets():
    win = os.environ.get("SystemRoot", r"C:\Windows")
    temp = {os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
            os.path.join(HOME, "AppData", "Local", "Temp"), os.path.join(win, "Temp")}
    return {
        "File temporanei": [d for d in temp if d and os.path.isdir(d)],
        "Cache Windows Update": [d for d in [os.path.join(win, "SoftwareDistribution", "Download")] if os.path.isdir(d)],
    }


def _delete_tree_contents(dirs):
    deleted = freed = errors = 0
    for d in dirs:
        for root, subdirs, files in os.walk(d, topdown=False, onerror=lambda e: None):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    s = os.path.getsize(fp)
                    os.remove(fp)
                    deleted += 1
                    freed += s
                except OSError:
                    errors += 1  # file in uso: normale
            for sd in subdirs:
                try:
                    os.rmdir(os.path.join(root, sd))
                except OSError:
                    pass
    return deleted, freed, errors


def clean_temp_files():
    """Cancella i file temporanei (TEMP utente e di Windows)."""
    d, f, e = _delete_tree_contents(_cleanup_targets()["File temporanei"])
    return f"File temporanei: {d} cancellati, ~{_fmt_size(f)} liberati ({e} in uso e saltati)"


def clean_windows_update():
    """Svuota la cache download di Windows Update."""
    dirs = _cleanup_targets()["Cache Windows Update"]
    if not dirs:
        return "Cache Windows Update non trovata"
    d, f, e = _delete_tree_contents(dirs)
    note = " (servono i permessi di amministratore per il resto)" if e else ""
    return f"Cache Windows Update: {d} file, ~{_fmt_size(f)}{note}"


def _recycle_bin_size():
    if not IS_WINDOWS:
        return "n/d"
    ps = ("$s=(New-Object -ComObject Shell.Application).NameSpace(10).Items() | "
          "Measure-Object -Property Size -Sum; \"$($s.Count) elementi, $([math]::Round($s.Sum/1MB,1)) MB\"")
    try:
        return _run(["powershell", "-NoProfile", "-Command", ps], timeout=20).stdout.strip() or "n/d"
    except Exception:
        return "n/d"


def empty_recycle_bin():
    """Svuota il Cestino."""
    if not IS_WINDOWS:
        return "Disponibile solo su Windows"
    try:
        _run(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"], timeout=60)
        return "Cestino svuotato."
    except Exception as e:
        return f"Impossibile svuotare il cestino: {e}"


def find_and_clean_logs(path=SYSTEM_DRIVE, min_size_mb=50):
    """Cancella file .log/.tmp grandi (mai .bak/.old, mai dentro Windows o Documenti)."""
    min_b = int(min_size_mb) * 1024 * 1024
    protected = {"Windows", "Documents", "Documenti", "Desktop", "Pictures", "Videos", "Music", "OneDrive"}
    deleted = freed = 0
    for root, _, files in _walk(path, skip=protected):
        for f in files:
            if f.lower().endswith((".log", ".tmp")):
                fp = os.path.join(root, f)
                try:
                    s = os.path.getsize(fp)
                    if s >= min_b:
                        os.remove(fp)
                        deleted += 1
                        freed += s
                except OSError:
                    pass
    return f"Log/tmp grandi: {deleted} file cancellati, ~{_fmt_size(freed)} liberati"


def pulizia_automatica():
    """Pulizia completa e sicura: temporanei, cestino, cache Windows Update."""
    return "\n".join([
        "=== PULIZIA AUTOMATICA WINTAL ===",
        "1. " + clean_temp_files(),
        "2. " + empty_recycle_bin(),
        "3. " + clean_windows_update(),
        "=== COMPLETATA ===",
    ])


def deframmenta_disco(drive="C:"):
    """Ottimizza il disco: TRIM su SSD, deframmentazione su HDD (scelto da Windows)."""
    drive = drive.strip().rstrip("\\")
    if len(drive) == 1:
        drive += ":"
    try:
        r = _run(["defrag", drive, "/O", "/U"], timeout=3600)
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            return f"Ottimizzazione di {drive} completata.\n{out[-1200:]}"
        return f"Ottimizzazione di {drive} non riuscita (serve avviare come amministratore?).\n{out[-1200:]}"
    except subprocess.TimeoutExpired:
        return f"Ottimizzazione di {drive} ancora in corso (normale su dischi grandi)."
    except Exception as e:
        return f"Errore: {e}"


def rename_file(old_path, new_path):
    """Rinomina o sposta un file/cartella."""
    if os.path.exists(new_path):
        return f"Esiste già: {new_path}"
    try:
        os.rename(old_path, new_path)
        return f"Rinominato: {old_path} -> {new_path}"
    except Exception as e:
        return f"Errore rinomina: {e}"


def _is_protected(path):
    p = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    win = os.environ.get("SystemRoot", r"C:\Windows")
    roots = [SYSTEM_DRIVE, win, os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
             os.environ.get("ProgramData", ""), HOME, os.path.join(HOME, "AppData"), os.path.dirname(HOME)]
    roots = {os.path.normcase(os.path.abspath(r)).rstrip("\\/") for r in roots if r}
    if p in roots or len(p) <= 3:
        return True
    return p.startswith(os.path.normcase(win).rstrip("\\") + os.sep)


def delete_path(path):
    """Sposta nel Cestino (se possibile) un file o una cartella."""
    if not os.path.exists(path):
        return f"Percorso non trovato: {path}"
    if _is_protected(path):
        return f"Bloccato: {path} è una cartella di sistema o protetta."
    try:
        from send2trash import send2trash
        send2trash(path)
        return f"Spostato nel Cestino: {path}"
    except ImportError:
        pass
    except Exception as e:
        return f"Errore: {e}"
    try:
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        return f"Cancellato definitivamente: {path}"
    except Exception as e:
        return f"Errore cancellazione: {e}"


# ===================== REGISTRO TOOL =====================
TOOLS = {f.__name__: f for f in [
    get_system_info, get_cpu_temperature, top_processes, startup_programs, search_files,
    find_large_files, find_duplicates, find_same_name_files, list_heavy_installs, preview_cleanup,
    clean_temp_files, empty_recycle_bin, clean_windows_update, find_and_clean_logs,
    pulizia_automatica, deframmenta_disco, rename_file, delete_path,
]}
TOOLS["delete_temp_files"] = clean_temp_files  # compatibilità con la versione precedente

# Tool che modificano il PC: chiedono sempre conferma all'utente.
DANGEROUS = {
    "clean_temp_files": "Cancellare i file temporanei",
    "delete_temp_files": "Cancellare i file temporanei",
    "empty_recycle_bin": "Svuotare il Cestino (irreversibile)",
    "clean_windows_update": "Svuotare la cache di Windows Update",
    "find_and_clean_logs": "Cancellare i file .log/.tmp grandi",
    "pulizia_automatica": "Pulizia automatica: temporanei, Cestino e cache Windows Update",
    "deframmenta_disco": "Ottimizzare/deframmentare il disco (può richiedere molto tempo)",
    "rename_file": "Rinominare/spostare un file",
    "delete_path": "Eliminare un file o una cartella",
}

TOOL_LABELS = {
    "get_system_info": "Leggo le info di sistema", "get_cpu_temperature": "Leggo la temperatura",
    "top_processes": "Analizzo i processi", "startup_programs": "Leggo i programmi di avvio",
    "search_files": "Cerco i file", "find_large_files": "Cerco i file più pesanti",
    "find_duplicates": "Confronto i file (duplicati)", "find_same_name_files": "Cerco nomi ripetuti",
    "list_heavy_installs": "Misuro i programmi installati", "preview_cleanup": "Stimo lo spazio recuperabile",
}


def _p(desc, typ="string"):
    return {"type": typ, "description": desc}


def _schema(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


PATH = _p("Percorso di partenza, es. C:\\ oppure C:\\Users\\nome")
TOOL_SCHEMAS = [
    _schema("get_system_info", "Info complete sul PC: sistema operativo, CPU, RAM, dischi, GPU, temperatura, uptime"),
    _schema("get_cpu_temperature", "Temperatura attuale della CPU"),
    _schema("top_processes", "Processi che usano più RAM o CPU (cosa rallenta il PC)",
            {"sort_by": _p("'ram' oppure 'cpu'"), "top_n": _p("quanti", "integer")}),
    _schema("startup_programs", "Programmi che si avviano con Windows"),
    _schema("search_files", "Cerca file per nome, supporta * e ?",
            {"name_pattern": _p("es. '*.MOV' o '*fattura*'"), "path": PATH, "max_results": _p("max", "integer")},
            ["name_pattern"]),
    _schema("find_large_files", "Trova i file più grandi", {"path": PATH, "top_n": _p("quanti", "integer"),
                                                           "min_size_mb": _p("soglia MB", "integer")}),
    _schema("find_duplicates", "Trova file duplicati identici (stesso contenuto) e lo spazio sprecato", {"path": PATH}),
    _schema("find_same_name_files", "Trova file con lo stesso nome in cartelle diverse", {"path": PATH}),
    _schema("list_heavy_installs", "Programmi installati più pesanti", {"top_n": _p("quanti", "integer")}),
    _schema("preview_cleanup", "Stima quanto spazio si libererebbe con la pulizia, senza cancellare"),
    _schema("clean_temp_files", "Cancella i file temporanei"),
    _schema("empty_recycle_bin", "Svuota il Cestino"),
    _schema("clean_windows_update", "Svuota la cache di Windows Update"),
    _schema("find_and_clean_logs", "Cancella file .log/.tmp molto grandi",
            {"path": PATH, "min_size_mb": _p("soglia MB", "integer")}),
    _schema("pulizia_automatica", "Pulizia completa: temporanei + Cestino + cache Windows Update. "
                                  "Usala per 'pulisci tutto' o 'pulizia automatica'"),
    _schema("deframmenta_disco", "Ottimizza o deframmenta un disco", {"drive": _p("es. C:")}),
    _schema("rename_file", "Rinomina o sposta un file/cartella",
            {"old_path": _p("percorso attuale"), "new_path": _p("nuovo percorso")}, ["old_path", "new_path"]),
    _schema("delete_path", "Elimina un file o una cartella (va nel Cestino)", {"path": _p("percorso")}, ["path"]),
]

SYSTEM_PROMPT = f"""Sei WinTAL, l'assistente AI locale che vive dentro questo PC Windows e parla in prima persona come se fossi il PC.
Rispondi sempre in italiano, in modo chiaro, breve e amichevole.
Cartella utente: {HOME}. Disco di sistema: {SYSTEM_DRIVE}
Regole:
- Usa SEMPRE i tool quando servono dati reali: non inventare mai numeri, file o percorsi.
- "Duplicati" = find_duplicates (contenuto identico); "stesso nome" = find_same_name_files.
- Prima di proporre una pulizia, se utile, usa preview_cleanup per dire quanto spazio si libera.
- Per "pulisci tutto"/"pulizia automatica" usa pulizia_automatica.
- Le azioni che modificano il PC vengono confermate dall'utente con una finestra: se rifiuta, non insistere.
- Quando mostri liste di file, riassumi i punti principali e dai un consiglio pratico.
"""


# ===================== AGENTE =====================
def list_models(host):
    try:
        from ollama import Client
        res = Client(host=host).list()
        models = getattr(res, "models", None) or res.get("models", [])
        return sorted({getattr(m, "model", None) or m.get("model") or m.get("name") for m in models})
    except Exception:
        return []


def _coerce(func, args):
    """I modelli piccoli passano spesso numeri come stringhe: li convertiamo."""
    import inspect
    sig = inspect.signature(func)
    clean = {}
    for k, v in (args or {}).items():
        if k not in sig.parameters:
            continue
        default = sig.parameters[k].default
        if isinstance(default, int) and not isinstance(v, int):
            try:
                v = int(float(v))
            except (TypeError, ValueError):
                continue
        clean[k] = v
    return clean


def run_agent(user_message, history, cfg, confirm=None, status=None, max_steps=10):
    """Esegue il ciclo LLM -> tool -> LLM. confirm(name, args, desc)->bool, status(text)."""
    try:
        from ollama import Client
    except ImportError:
        return "Manca il pacchetto 'ollama': esegui  pip install ollama"
    client = Client(host=cfg["ollama_host"])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-cfg.get("max_history", 20):]
    messages.append({"role": "user", "content": user_message})
    for _ in range(max_steps):
        try:
            response = client.chat(model=cfg["model"], messages=messages, tools=TOOL_SCHEMAS)
        except Exception as e:
            return (f"Non riesco a parlare con Ollama ({e}).\n"
                    f"Controlla che Ollama sia avviato e che il modello '{cfg['model']}' sia scaricato:\n"
                    f"  ollama pull {cfg['model']}")
        msg = response.message
        messages.append(msg)
        calls = getattr(msg, "tool_calls", None)
        if not calls:
            return (msg.content or "").strip() or "Nessuna risposta dal modello."
        for call in calls:
            name = call.function.name
            func = TOOLS.get(name)
            if not func:
                result = f"Tool '{name}' inesistente."
            else:
                args = _coerce(func, call.function.arguments)
                if name in DANGEROUS and confirm and not confirm(name, args, DANGEROUS[name]):
                    result = "Operazione annullata dall'utente."
                else:
                    if status:
                        status(TOOL_LABELS.get(name, "Eseguo: " + DANGEROUS.get(name, name)) + "…")
                    try:
                        result = func(**args)
                    except Exception as e:
                        result = f"Errore nel tool {name}: {e}"
            if status:
                status("WinTAL sta pensando…")
            messages.append({"role": "tool", "tool_name": name, "content": str(result)[:8000]})
    return "Ho fatto troppi passaggi senza arrivare a una risposta. Prova a riformulare la domanda."
