# -*- coding: utf-8 -*-
"""
RGB 燈光開關（分享版）
可以帶到任何 Windows 電腦使用：自動偵測硬體與各品牌燈光軟體，
以 OpenRGB 為底層統一關閉 / 恢復 RGB 燈光。

命令列用法：
    RGB燈光開關.exe --all-off   直接關閉所有燈光後結束
    RGB燈光開關.exe --all-on    直接恢復所有燈光後結束
"""
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

from openrgb import OpenRGBClient
from openrgb.utils import ModeColors, RGBColor

APP_NAME = "RGB 燈光開關"
FROZEN = getattr(sys, "frozen", False)
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RGBLightSwitch"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OPENRGB_DIR = DATA_DIR / "OpenRGB"
STATE_FILE = DATA_DIR / "rgb_state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PAUSED_FILE = DATA_DIR / "paused_vendor.json"
LOG_FILE = DATA_DIR / "rgb_control.log"
ICON_FILE = RES_DIR / "lightbulb.ico"

OPENRGB_ZIP_URL = ("https://codeberg.org/OpenRGB/OpenRGB/releases/download/"
                   "release_1.0/OpenRGB_1.0_Windows_64_81bbe18.zip")
PAWNIO_SETUP_URL = "https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe"
PAWNIO_URL = "https://pawnio.eu"
HOST, PORT = "127.0.0.1", 6742
CREATE_NO_WINDOW = 0x08000000
BLACK = RGBColor(0, 0, 0)


class FastOpenRGBClient(OpenRGBClient):
    """OpenRGB 1.0 在 --server 模式下不回應「外掛清單」查詢，
    原本每次連線 / 更新都要乾等 10 秒逾時；我們用不到外掛，直接略過。"""

    def update_plugins(self):
        pass

# 各品牌會和 OpenRGB 搶燈光控制權的服務與程式（只列燈光相關部分）
VENDORS = [
    {"brand": "ASUS Aura / Armoury Crate",
     "services": ["LightingService"],
     "processes": ["AacAmbientLighting", "LightingService"]},
    {"brand": "Razer Chroma / Synapse",
     "services": ["Razer Chroma SDK Service", "Razer Chroma SDK Server", "Razer Chroma Stream Server"],
     "processes": ["RzChromaConnectManager", "RzChromaConnectServer", "RzChromaStreamServer",
                   "RzSmartlightingDeviceManager"]},
    {"brand": "Logitech G HUB / LGS",
     "services": [],
     "processes": ["lghub_agent", "LCore"]},
    {"brand": "MSI Mystic Light / MSI Center",
     "services": ["Mystic_Light_Service"],
     "processes": ["LEDKeeper2", "LEDKeeper", "MysticLight", "MysticLight3", "MSI_LED"]},
    {"brand": "Gigabyte RGB Fusion",
     "services": [],
     "processes": ["RGBFusion", "RGBFusion2", "RGBFusionSDKServer", "SelLedService"]},
    {"brand": "ASRock Polychrome",
     "services": [],
     "processes": ["AsrPolychromeRGB", "PolychromeRGB", "AsrLED"]},
    {"brand": "Corsair iCUE",
     "services": ["CorsairService", "iCUEDevicePluginHost"],
     "processes": ["iCUE", "iCUEDevicePluginHost", "Corsair.Service"]},
    {"brand": "SteelSeries GG",
     "services": [],
     "processes": ["SteelSeriesGG", "SteelSeriesGGClient", "SteelSeriesEngine"]},
    {"brand": "NZXT CAM",
     "services": [],
     "processes": ["NZXT CAM"]},
    {"brand": "Lian Li L-Connect",
     "services": [],
     "processes": ["L-Connect 3", "L-Connect-Service"]},
    {"brand": "HyperX NGENUITY",
     "services": [],
     "processes": ["NGENUITY", "NGenuity2"]},
    {"brand": "Cooler Master MasterPlus",
     "services": [],
     "processes": ["MasterPlus", "CMMasterPlus"]},
    {"brand": "Thermaltake TT RGB Plus",
     "services": [],
     "processes": ["TT RGB Plus", "TTRGBPlus"]},
    {"brand": "SignalRGB",
     "services": [],
     "processes": ["SignalRgb", "SignalRgbLauncher"]},
]

RAINBOW_NAMES = ["spectrum cycle", "rainbow wave", "rainbow", "spectrum", "wave",
                 "color cycle", "colour cycle", "cycle"]

RESTORE_CHOICES = [
    ("原本的效果（讀不到時用彩虹）", "original"),
    ("彩虹效果", "rainbow"),
    ("白光", "white"),
    ("自選顏色", "color"),
    ("交還給原廠燈光軟體", "vendor"),
]

TYPE_NAMES = {
    "MOTHERBOARD": "主機板", "DRAM": "記憶體", "GPU": "顯示卡", "COOLER": "散熱器",
    "LEDSTRIP": "燈條", "KEYBOARD": "鍵盤", "MOUSE": "滑鼠", "MOUSEMAT": "滑鼠墊",
    "HEADSET": "耳機", "HEADSET_STAND": "耳機架", "GAMEPAD": "手把", "LIGHT": "燈具",
    "SPEAKER": "喇叭", "VIRTUAL": "虛擬裝置", "STORAGE": "SSD / 儲存裝置", "CASE": "機殼",
    "MICROPHONE": "麥克風", "ACCESSORY": "配件", "KEYPAD": "小鍵盤", "UNKNOWN": "其他",
}

RGB_SOFTWARE_PATTERN = (r"RGB|Aura|Armoury|Chroma|Synapse|G HUB|Logitech Gaming|iCUE|Mystic|MSI Center|"
                        r"Dragon Center|Fusion|Gigabyte Control|Polychrome|SteelSeries|NZXT|L-Connect|"
                        r"NGENUITY|MasterPlus|Thermaltake|SignalRGB|OpenRGB|Corsair|Razer|HyperX")


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 系統工具
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def relaunch_as_admin():
    if FROZEN:
        exe, params = sys.executable, ""
    else:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        params = f'"{Path(__file__).resolve()}"'
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32


def run_quiet(args, encoding="mbcs"):
    try:
        return subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW, encoding=encoding, errors="replace")
    except Exception as e:  # noqa: BLE001
        log(f"run_quiet {args}: {e}")
        return None


def run_powershell_json(script):
    """執行 PowerShell 並以 UTF-8 JSON 取回結果（中文不會亂碼）"""
    import base64
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n$ProgressPreference='SilentlyContinue'\n" + script
    enc = base64.b64encode(full.encode("utf-16-le")).decode()
    r = run_quiet(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], encoding="utf-8")
    if not r or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        log(f"PowerShell JSON 解析失敗：{r.stdout[:300]} {r.stderr[:300]}")
        return None


def process_table():
    """回傳 {小寫程式名稱: 執行檔路徑}"""
    data = run_powershell_json(
        "Get-CimInstance Win32_Process | Select-Object Name,ExecutablePath | ConvertTo-Json -Compress")
    table = {}
    if isinstance(data, dict):
        data = [data]
    for p in data or []:
        name = (p.get("Name") or "").lower().removesuffix(".exe")
        if name and name not in table:
            table[name] = p.get("ExecutablePath") or ""
    return table


def service_running(name):
    r = run_quiet(["sc", "query", name])
    return bool(r and "RUNNING" in r.stdout)


def service_exists(name):
    r = run_quiet(["sc", "query", name])
    return bool(r and r.returncode == 0)


def pawnio_installed():
    return service_exists("PawnIO") or Path(r"C:\Program Files\PawnIO").exists()


def install_pawnio(progress=None):
    setup = DATA_DIR / "PawnIO_setup.exe"

    def hook(blocks, bsize, total):
        if progress and total > 0:
            progress(min(100, blocks * bsize * 100 // total))

    urllib.request.urlretrieve(PAWNIO_SETUP_URL, setup, hook)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", str(setup), None, None, 1)


def find_openrgb():
    candidates = []
    for base in (OPENRGB_DIR, Path(sys.executable).parent / "OpenRGB"):
        if base.exists():
            candidates += list(base.rglob("OpenRGB.exe"))
    candidates += [Path(r"C:\Program Files\OpenRGB\OpenRGB.exe"),
                   Path(r"C:\Program Files (x86)\OpenRGB\OpenRGB.exe")]
    for c in candidates:
        if c.exists():
            return c
    return None


def download_openrgb(progress=None):
    OPENRGB_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OPENRGB_DIR / "OpenRGB.zip"

    def hook(blocks, bsize, total):
        if progress and total > 0:
            progress(min(100, blocks * bsize * 100 // total))

    urllib.request.urlretrieve(OPENRGB_ZIP_URL, zip_path, hook)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(OPENRGB_DIR)
    zip_path.unlink(missing_ok=True)
    return find_openrgb()


def port_open():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def openrgb_log_issues(limit=20):
    """讀取 OpenRGB 最新記錄檔中的錯誤與警告（例如某個裝置初始化失敗）"""
    logs = Path(os.environ.get("APPDATA", "")) / "OpenRGB" / "logs"
    try:
        latest = max(logs.glob("OpenRGB_*.log"), key=lambda f: f.stat().st_mtime)
        text = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return []
    out = []
    for line in text:
        if ("[Error" in line or "[Warning" in line) and "NetworkServer" not in line:
            line = re.sub(r"^\[\s*\d+\s*\]", "", line).strip()
            if line not in out:
                out.append(line)
    return out[-limit:]


# ---------------------------------------------------------------- 硬體偵測
def detect_hardware():
    script = r"""
$r = [ordered]@{}
$b = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$r.board = ("$($b.Manufacturer) $($b.Product)").Trim()
$r.cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$r.gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name } |
           Where-Object { $_ -notmatch 'Oray|Parsec|Virtual|Remote|Basic Display|Idd' })
$r.ram = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object {
           ("$($_.Manufacturer) $($_.PartNumber)").Trim() + " (" + [math]::Round($_.Capacity/1GB) + "GB)" })
$r.disk = @(Get-CimInstance Win32_DiskDrive | ForEach-Object { $_.Model })
$brands = '(Razer|Logitech|Corsair|SteelSeries|HyperX|ASUS|ROG|MSI|Redragon|Cooler ?Master|NZXT|Lian ?Li|Glorious|Wooting|Ducky|Keychron|Roccat|Turtle|Zowie|Fantech|Aula|Royal ?Kludge|Thermaltake|Gigabyte|AORUS|Patriot|Kingston|G\.?Skill|Govee|Nanoleaf|Philips Hue|Akko|Leopold|Xtrfy|Endgame|Pulsar|Lamzu|Finalmouse|Cherry|AMD Wraith|Wraith)'
$generic = 'HID|USB Input|Composite|Root Hub|Virtual|標準|符合|輸入裝置|Standard|Generic|Controller|Hub$|System|Component|Interface|Product Name|Firmware|ACPI|Driver|Service|Monitor|Audio|Microphone|Speaker|Bluetooth|Wireless Radio|Wi-?Fi|Ethernet|Network|Camera|Update'
$r.peripherals = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -and $_.FriendlyName -notmatch $generic -and
                   ($_.Class -in 'Keyboard','Mouse' -or $_.FriendlyName -match $brands) } |
    ForEach-Object { $_.FriendlyName } | Sort-Object -Unique)
$keys = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
$r.software = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match '__PATTERN__' -and $_.DisplayName -notmatch 'HAL$|Component$|add-on|SDK$|Driver' } |
    ForEach-Object { $_.DisplayName } | Sort-Object -Unique)
$r.usb = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -match '^(USB|HID)\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})' } |
    ForEach-Object { [pscustomobject]@{ id = ($Matches[2] + ':' + $Matches[3]); name = $_.FriendlyName } } |
    Where-Object { $_.id -notmatch '^(8087|1D6B|0BDA|8086|1022):' } |
    Group-Object id | ForEach-Object {
        $names = @($_.Group | ForEach-Object { $_.name } | Where-Object { $_ } | Sort-Object -Unique)
        $_.Name + '  ' + ($names -join ' / ') })
$os = Get-CimInstance Win32_OperatingSystem
$r.os = "$($os.Caption) $($os.Version)"
$r | ConvertTo-Json -Depth 3 -Compress
""".replace("__PATTERN__", RGB_SOFTWARE_PATTERN)
    data = run_powershell_json(script) or {}
    for k in ("gpu", "ram", "disk", "peripherals", "software", "usb"):
        v = data.get(k)
        if v is None:
            data[k] = []
        elif isinstance(v, str):
            data[k] = [v]
    return data


# ---------------------------------------------------------------- 廠商軟體
def vendor_status(procs=None):
    """回傳 [(品牌, [正在執行的項目])]"""
    procs = procs if procs is not None else process_table()
    found = []
    for v in VENDORS:
        items = [s for s in v["services"] if service_running(s)]
        items += [p for p in v["processes"] if p.lower() in procs and p not in items]
        if items:
            found.append((v["brand"], items))
    return found


def pause_vendor():
    """停止各廠商燈光服務 / 程式，並記下來以便之後恢復"""
    procs = process_table()
    paused = load_json(PAUSED_FILE, {"services": [], "processes": {}})
    for v in VENDORS:
        for s in v["services"]:
            if service_running(s):
                run_quiet(["sc", "stop", s])
                if s not in paused["services"]:
                    paused["services"].append(s)
        for p in v["processes"]:
            path = procs.get(p.lower())
            if path is not None:
                run_quiet(["taskkill", "/f", "/im", p + ".exe"])
                # 服務本身的程式會隨服務重新啟動，不需要另外記
                if path and p not in v["services"]:
                    paused["processes"][p] = path
    save_json(PAUSED_FILE, paused)
    return paused


def resume_vendor(restart=False):
    """重新啟動廠商燈光軟體；restart=True 會先停止再啟動，讓它們重新套用自己的燈光效果"""
    if restart:
        pause_vendor()
        time.sleep(3)
    paused = load_json(PAUSED_FILE, {"services": [], "processes": {}})
    for s in paused.get("services", []):
        run_quiet(["sc", "start", s])
    time.sleep(3)
    running = process_table()
    for name, path in paused.get("processes", {}).items():
        if name.lower() not in running and path and Path(path).exists():
            # 透過檔案總管啟動，讓程式以一般使用者身分執行（而不是系統管理員）
            subprocess.Popen(["explorer.exe", path], creationflags=CREATE_NO_WINDOW)
    PAUSED_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- 燈光控制核心
class RGBController:
    def __init__(self):
        self.client = None
        self.server_proc = None
        self.state = load_json(STATE_FILE, {})
        self.lock = threading.RLock()
        self.last_results = {}  # 最近一次操作的結果，放進診斷報告

    def device_count(self):
        """重新讀取裝置清單並回傳數量"""
        with self.lock:
            if not self.client:
                return 0
            try:
                self.client.update()
            except Exception:  # noqa: BLE001
                pass
            return len(self.client.devices)

    def start_server(self):
        if port_open():
            return True
        exe = find_openrgb()
        if not exe:
            raise RuntimeError("找不到 OpenRGB 控制核心，請先下載。")
        self.server_proc = subprocess.Popen([str(exe), "--server", "--server-port", str(PORT)],
                                            cwd=str(exe.parent), creationflags=CREATE_NO_WINDOW,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
        for _ in range(60):
            if port_open():
                return True
            time.sleep(0.5)
        raise RuntimeError("OpenRGB 啟動逾時，請再試一次。")

    def stop_server(self):
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
        self.server_proc = None

    def connect(self, status=None):
        self.start_server()
        last_err = None
        for _ in range(20):
            try:
                self.client = FastOpenRGBClient(HOST, PORT, "RGB燈光開關")
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.5)
        else:
            raise RuntimeError(f"無法連線 OpenRGB：{last_err}")
        # OpenRGB 剛啟動時仍在偵測硬體，等裝置數量穩定下來（最多 20 秒）
        stable, count = 0, -1
        for _ in range(40):
            n = self.device_count()
            if status:
                status(f"正在偵測燈光裝置…目前找到 {n} 個")
            stable = stable + 1 if n == count else 0
            count = n
            if stable >= 3 and (n > 0 or stable >= 8):
                break
            time.sleep(0.5)
        return self.client.devices

    def rescan(self, status=None):
        # 重新啟動 OpenRGB，讓它重新偵測硬體（例如暫停廠商軟體之後）
        self.disconnect()
        if self.server_proc and self.server_proc.poll() is None:
            self.stop_server()
            for _ in range(20):
                if not port_open():
                    break
                time.sleep(0.5)
        return self.connect(status)

    def disconnect(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.client = None

    @staticmethod
    def key(dev):
        loc = ""
        try:
            loc = dev.metadata.location or ""
        except Exception:  # noqa: BLE001
            pass
        return f"{dev.type.name}|{dev.name}|{loc}"

    def is_off(self, dev):
        return self.state.get(self.key(dev), {}).get("off", False)

    def _save_state(self):
        save_json(STATE_FILE, self.state)

    def turn_off(self, dev):
        """關燈並回傳預期結果（給事後檢查用）"""
        with self.lock:
            before = dev.modes[dev.active_mode].name
            try:
                expect = self._turn_off(dev)
            except Exception as e:  # noqa: BLE001
                log(f"關閉失敗 {dev.name}：原模式 {before}，可用模式 {[m.name for m in dev.modes]}，錯誤 {e}")
                raise
            log(f"關閉 {dev.name}：{before} → {expect['mode']}"
                f"{'（黑色）' if expect['black'] else ''}，裝置回報 {dev.modes[dev.active_mode].name}")
            self.last_results[self.key(dev)] = f"{before} → {expect['mode']}{'（黑色）' if expect['black'] else ''}"
            return expect

    def _turn_off(self, dev):
        k = self.key(dev)
        if not self.state.get(k, {}).get("off"):
            mode = dev.modes[dev.active_mode]
            self.state[k] = {
                "off": True,
                "name": dev.name,
                "mode": dev.active_mode,
                "mode_name": mode.name,
                "mode_colors": [[c.red, c.green, c.blue] for c in (mode.colors or [])],
                "colors": [[c.red, c.green, c.blue] for c in dev.colors],
            }
            self._save_state()

        off_mode = next((m for m in dev.modes if m.name.lower() in ("off", "關閉", "disable", "disabled")), None)
        if off_mode is not None:
            dev.set_mode(off_mode)
            return {"mode": off_mode.name, "black": False}
        try:
            dev.set_custom_mode()  # 通常是 Direct 模式
            dev.set_color(BLACK)
            return {"mode": dev.modes[dev.active_mode].name, "black": True}
        except Exception as e:  # noqa: BLE001
            log(f"{dev.name} custom mode 失敗：{e}")
        static = next((m for m in dev.modes if m.name.lower() == "static"), None)
        if static is not None:
            if getattr(static, "brightness", None) is not None and static.brightness_min is not None:
                static.brightness = static.brightness_min
            if static.colors is not None:
                static.colors = [BLACK] * max(1, static.colors_min or 1)
            dev.set_mode(static, force=True)
            dev.set_color(BLACK)
            return {"mode": static.name, "black": True}
        raise RuntimeError("這個裝置不支援關燈模式")

    def find_reverted(self, expectations):
        """重新讀取裝置，找出被其他軟體改回來的裝置。expectations：{key: 預期結果}"""
        with self.lock:
            if not self.client:
                return []
            try:
                self.client.update()
            except Exception as e:  # noqa: BLE001
                log(f"檢查時更新失敗：{e}")
                return []
            reverted = []
            for dev in self.client.devices:
                exp = expectations.get(self.key(dev))
                if not exp:
                    continue
                now = dev.modes[dev.active_mode].name
                changed = now.lower() != exp["mode"].lower()
                if not changed and exp["black"]:
                    changed = any(c.red or c.green or c.blue for c in dev.colors)
                if changed:
                    log(f"被改回：{dev.name} 預期 {exp['mode']}，實際 {now}")
                    self.last_results[self.key(dev)] += f"（之後被改回 {now}）"
                    reverted.append(dev.name)
            return reverted

    def devices_by_keys(self, keys):
        with self.lock:
            if not self.client:
                return []
            return [d for d in self.client.devices if self.key(d) in keys]

    @staticmethod
    def _saved_is_blank(saved):
        """記錄裡沒有可用的燈光（例如原本由廠商軟體控制，OpenRGB 只讀到黑色）"""
        if not saved:
            return True
        name = saved.get("mode_name", "").lower()
        if name in ("off", "關閉", "disable", "disabled"):
            return True
        cols = saved.get("mode_colors") or saved.get("colors") or []
        has_color = any(any(c) for c in cols)
        return name in ("direct", "custom", "static", "") and not has_color

    @staticmethod
    def _full_brightness(mode):
        if getattr(mode, "brightness", None) is not None and getattr(mode, "brightness_max", None) is not None:
            mode.brightness = max(mode.brightness_min or 0, mode.brightness_max)

    def _set_rainbow(self, dev):
        names = [m.name.lower() for m in dev.modes]
        for want in RAINBOW_NAMES:
            if want in names:
                mode = dev.modes[names.index(want)]
                self._full_brightness(mode)
                dev.set_mode(mode, force=True)
                return
        self._set_solid(dev, RGBColor(255, 255, 255))

    def _set_solid(self, dev, color):
        static = next((m for m in dev.modes if m.name.lower() == "static"), None)
        if static is not None:
            self._full_brightness(static)
            if static.colors is not None:
                static.colors = [color] * max(1, static.colors_min or 1)
            dev.set_mode(static, force=True)
        else:
            dev.set_custom_mode()
        dev.set_color(color)

    def turn_on(self, dev, style="original"):
        with self.lock:
            before = dev.modes[dev.active_mode].name
            try:
                self._turn_on(dev, style)
            except Exception as e:  # noqa: BLE001
                log(f"恢復失敗 {dev.name}：{e}")
                raise
            after = dev.modes[dev.active_mode].name
            log(f"恢復 {dev.name}：{before} → {after}")
            self.last_results[self.key(dev)] = f"恢復 {before} → {after}"

    def _turn_on(self, dev, style="original"):
        """style：original（記錄的原本效果，沒有就彩虹）、rainbow、white、或 (r, g, b)"""
        k = self.key(dev)
        saved = self.state.get(k)
        if style == "original" and not self._saved_is_blank(saved):
            idx = saved.get("mode", 0)
            if not (0 <= idx < len(dev.modes)):
                idx = 0
            mode = dev.modes[idx]
            if mode.colors is not None and saved.get("mode_colors"):
                mode.colors = [RGBColor(*c) for c in saved["mode_colors"]]
            self._full_brightness(mode)
            dev.set_mode(mode, force=True)
            if mode.color_mode == ModeColors.PER_LED and saved.get("colors") \
                    and len(saved["colors"]) == len(dev.leds):
                dev.set_colors([RGBColor(*c) for c in saved["colors"]])
        elif style in ("original", "rainbow"):
            self._set_rainbow(dev)
        elif style == "white":
            self._set_solid(dev, RGBColor(255, 255, 255))
        else:
            self._set_solid(dev, RGBColor(*style))
        self.state.pop(k, None)
        self._save_state()


def restore_style(settings):
    style = settings.get("restore_style", "original")
    if style == "color":
        return tuple(settings.get("restore_color", [255, 255, 255]))
    return style


# ---------------------------------------------------------------- 命令列模式
def cli_mode(turn_on):
    ctl = RGBController()
    settings = load_json(SETTINGS_FILE, {})
    try:
        style = restore_style(settings)
        if turn_on and style == "vendor":
            if is_admin():
                resume_vendor(restart=True)
                STATE_FILE.write_text("{}", encoding="utf-8")
                return
            style = "original"
        if not turn_on and settings.get("auto_pause", True) and is_admin() and vendor_status():
            pause_vendor()
            time.sleep(2)
        devices = ctl.connect()
        for dev in devices:
            try:
                ctl.turn_on(dev, style) if turn_on else ctl.turn_off(dev)
            except Exception as e:  # noqa: BLE001
                log(f"{dev.name}: {e}")
        time.sleep(1)
    except Exception as e:  # noqa: BLE001
        log(f"命令列模式失敗：{e}")
    finally:
        ctl.disconnect()
        if not settings.get("keep_server"):
            ctl.stop_server()


# ---------------------------------------------------------------- 圖形介面
def gui():
    import tkinter as tk
    from tkinter import colorchooser, messagebox, ttk

    ctl = RGBController()
    settings = load_json(SETTINGS_FILE, {"keep_server": False})
    info = {"hw": None, "vendors": [], "devices": []}

    root = tk.Tk()
    root.title(APP_NAME + ("" if is_admin() else "（未使用系統管理員權限）"))
    root.geometry("680x760")
    root.minsize(580, 600)
    if ICON_FILE.exists():
        try:
            root.iconbitmap(str(ICON_FILE))
        except tk.TclError:
            pass

    font = ("Microsoft JhengHei UI", 10)
    bold = ("Microsoft JhengHei UI", 11, "bold")
    root.option_add("*Font", font)
    style = ttk.Style()
    style.configure("Big.TButton", font=bold, padding=8)
    style.configure("TNotebook.Tab", font=font, padding=(14, 6))

    status_var = tk.StringVar(value="準備中…")
    busy = {"on": False}
    device_rows = []  # (BooleanVar, device, label, kind)

    def set_status(text):
        root.after(0, status_var.set, text)

    def run_bg(work, done=None):
        if busy["on"]:
            status_var.set("上一個動作還在進行中，請稍等…")
            return
        busy["on"] = True

        def wrapper():
            try:
                result, err = work(), None
            except Exception as e:  # noqa: BLE001
                result, err = None, e
                log(f"錯誤：{e}")

            def finish():
                busy["on"] = False
                if err:
                    status_var.set(f"發生問題：{err}")
                elif done:
                    done(result)
                refresh_setup()
            root.after(0, finish)
        threading.Thread(target=wrapper, daemon=True).start()

    ttk.Label(root, textvariable=status_var, relief="sunken", anchor="w", padding=(8, 4)).pack(
        fill="x", side="bottom")
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)
    main = ttk.Frame(nb, padding=4)
    hwtab = ttk.Frame(nb, padding=8)
    nb.add(main, text="燈光開關")
    nb.add(hwtab, text="電腦配置")

    # ======== 燈光開關分頁 ========
    # ① 必要設定
    setup = ttk.LabelFrame(main, text=" ① 必要設定 ", padding=8)
    setup.pack(fill="x", padx=4, pady=(4, 6))
    setup_info = ttk.Label(setup, text="", justify="left", wraplength=600)
    setup_info.pack(anchor="w")
    setup_btns = ttk.Frame(setup)
    setup_btns.pack(anchor="w", pady=(6, 0))

    def do_download():
        run_bg(lambda: download_openrgb(lambda p: set_status(f"下載 OpenRGB 控制核心中… {p}%")),
               lambda _: (status_var.set("下載完成，正在偵測燈光裝置…"), do_connect()))

    def do_pawnio():
        if not messagebox.askyesno("安裝 PawnIO 驅動",
                                   "PawnIO 是 OpenRGB 官方建議的免費驅動，用來控制記憶體和部分主機板的燈光。\n\n"
                                   "接下來會下載並開啟它的安裝程式，請依畫面按「安裝」。\n"
                                   "安裝完成後請重新開機，再打開本程式。\n\n要繼續嗎？"):
            return
        run_bg(lambda: install_pawnio(lambda p: set_status(f"下載 PawnIO 中… {p}%")),
               lambda _: status_var.set("已開啟 PawnIO 安裝程式；裝好後請重新開機"))

    def do_admin():
        if not messagebox.askyesno("以系統管理員重新啟動",
                                   "記憶體、主機板燈光和暫停原廠軟體都需要系統管理員權限。\n要重新啟動本程式嗎？"):
            return
        ctl.disconnect()
        ctl.stop_server()
        if relaunch_as_admin():
            root.destroy()

    btn_dl = ttk.Button(setup_btns, text="下載 OpenRGB", command=do_download)
    btn_pawn = ttk.Button(setup_btns, text="安裝 PawnIO 驅動", command=do_pawnio)
    btn_admin = ttk.Button(setup_btns, text="以系統管理員重新啟動", command=do_admin)
    for b in (btn_dl, btn_pawn, btn_admin):
        b.pack(side="left", padx=(0, 6))

    def refresh_setup():
        has_orgb = find_openrgb() is not None or port_open()
        has_pawn = pawnio_installed()
        admin = is_admin()
        lines = [
            ("✔ " if has_orgb else "✘ ") + "OpenRGB 控制核心" + ("" if has_orgb else "（尚未下載）"),
            ("✔ " if has_pawn else "✘ ") + "PawnIO 驅動" +
            ("" if has_pawn else "（沒有它，記憶體和部分主機板燈光無法控制；裝好後需重新開機）"),
            ("✔ " if admin else "✘ ") + "系統管理員權限" + ("" if admin else "（記憶體 / 主機板燈光需要）"),
        ]
        setup_info.config(text="\n".join(lines))
        btn_dl.state(["disabled"] if has_orgb else ["!disabled"])
        btn_pawn.state(["disabled"] if has_pawn else ["!disabled"])
        btn_admin.state(["disabled"] if admin else ["!disabled"])

    # ② 原廠燈光軟體
    vend = ttk.LabelFrame(main, text=" ② 原廠燈光軟體（會把燈改回來） ", padding=8)
    vend.pack(fill="x", padx=4, pady=6)
    vend_info = ttk.Label(vend, text="檢查中…", wraplength=600, justify="left")
    vend_info.pack(anchor="w")
    vend_btns = ttk.Frame(vend)
    vend_btns.pack(anchor="w", pady=(6, 0))

    def refresh_vendor():
        def work():
            found = vendor_status()
            root.after(0, show, found)

        def show(found):
            info["vendors"] = found
            if found:
                vend_info.config(text="執行中：" + "、".join(b for b, _ in found) +
                                 "\n它們可能會把燈重新打開。建議先按「暫停原廠燈光軟體」。")
            else:
                vend_info.config(text="沒有偵測到正在執行的原廠燈光軟體。")
        threading.Thread(target=work, daemon=True).start()

    def do_pause():
        if not is_admin():
            messagebox.showinfo("需要系統管理員", "暫停原廠軟體需要系統管理員權限，請先按「以系統管理員重新啟動」。")
            return
        brands = "、".join(b for b, _ in info["vendors"]) or "偵測到的原廠燈光軟體"
        if not messagebox.askyesno("暫停原廠燈光軟體",
                                   f"將暫時停止：{brands}\n\n"
                                   "只會停止燈光相關的程式，重新開機後會自動恢復。\n"
                                   "（Corsair、NZXT、Lian Li 的軟體也控制風扇，暫停期間風扇會回到硬體預設轉速）\n\n"
                                   "要繼續嗎？"):
            return
        run_bg(lambda: (pause_vendor(), time.sleep(2)),
               lambda _: (status_var.set("已暫停原廠燈光軟體，重新偵測燈光裝置…"), refresh_vendor(), do_rescan()))

    def do_resume():
        if not is_admin():
            messagebox.showinfo("需要系統管理員", "恢復原廠軟體需要系統管理員權限。")
            return
        run_bg(resume_vendor, lambda _: (status_var.set("已恢復原廠燈光軟體"), refresh_vendor()))

    ttk.Button(vend_btns, text="暫停原廠燈光軟體", command=do_pause).pack(side="left", padx=(0, 6))
    ttk.Button(vend_btns, text="恢復原廠燈光軟體", command=do_resume).pack(side="left", padx=(0, 6))

    # ③ 裝置清單
    devf = ttk.LabelFrame(main, text=" ③ 選擇要開關的燈光 ", padding=8)
    devf.pack(fill="both", expand=True, padx=4, pady=6)
    canvas = tk.Canvas(devf, highlightthickness=0, height=160)
    sb = ttk.Scrollbar(devf, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    canvas.bind("<Enter>", lambda e: canvas.bind_all(
        "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def fill_devices(devices, checked_keys=None):
        info["devices"] = list(devices or [])
        for w in inner.winfo_children():
            w.destroy()
        device_rows.clear()
        if not devices:
            tips = ["沒有找到可控制的燈光裝置，可以試試："]
            if not is_admin():
                tips.append("・按上方「以系統管理員重新啟動」")
            if not pawnio_installed():
                tips.append("・安裝 PawnIO 驅動並重新開機")
            if info["vendors"]:
                tips.append("・按「暫停原廠燈光軟體」後再「重新掃描」")
            tips.append("・到「電腦配置」分頁按「複製診斷報告」傳給幫忙的人")
            ttk.Label(inner, text="\n".join(tips), justify="left").pack(anchor="w")
            return
        order = list(TYPE_NAMES)
        devices = sorted(devices, key=lambda d: order.index(d.type.name) if d.type.name in order else 99)
        for dev in devices:
            var = tk.BooleanVar(value=checked_keys is None or ctl.key(dev) in checked_keys)
            row = ttk.Frame(inner)
            row.pack(fill="x", anchor="w", pady=2)
            ttk.Checkbutton(row, variable=var).pack(side="left")
            lab = ttk.Label(row, text="")
            lab.pack(side="left")
            device_rows.append((var, dev, lab, TYPE_NAMES.get(dev.type.name, dev.type.name)))
        update_labels()

    def update_labels():
        for var, dev, lab, kind in device_rows:
            state = "● 已關閉" if ctl.is_off(dev) else "○ 開啟中"
            lab.config(text=f"[{kind}] {dev.name}　　{state}")

    # ④ 操作
    act = ttk.Frame(main, padding=(4, 4))
    act.pack(fill="x")
    sel = ttk.Frame(act)
    sel.pack(fill="x", pady=(0, 6))

    def select_all(v):
        for var, *_ in device_rows:
            var.set(v)

    ttk.Button(sel, text="全選", command=lambda: select_all(True)).pack(side="left", padx=(0, 6))
    ttk.Button(sel, text="全不選", command=lambda: select_all(False)).pack(side="left", padx=(0, 6))
    ttk.Button(sel, text="重新掃描", command=lambda: do_rescan()).pack(side="left", padx=(0, 6))

    def handback_vendor(targets):
        if not is_admin():
            messagebox.showinfo("需要系統管理員",
                                "交還給原廠軟體需要系統管理員權限，請先按「以系統管理員重新啟動」。\n"
                                "或把「恢復成」改選彩虹、白光或自選顏色。")
            return
        if not messagebox.askyesno("交還給原廠軟體",
                                   "將重新啟動原廠燈光軟體，讓它們套用原本設定的燈光效果。\n要繼續嗎？"):
            return

        def work():
            set_status("正在把燈光交還給原廠軟體…")
            ctl.disconnect()
            ctl.stop_server()
            for dev in targets:
                ctl.state.pop(ctl.key(dev), None)
            ctl._save_state()
            resume_vendor(restart=True)
            time.sleep(3)
            return ctl.connect(set_status)

        def done(devs):
            fill_devices(devs)
            refresh_vendor()
            status_var.set("已交還給原廠軟體；如果燈還沒亮，請打開原廠燈光軟體看一下設定")
        run_bg(work, done)

    def apply(turn_on, only_selected):
        targets = [dev for var, dev, *_ in device_rows if var.get() or not only_selected]
        if not targets:
            status_var.set("沒有勾選任何裝置")
            return
        if not turn_on:
            turn_off_flow({ctl.key(d) for d in targets}, auto_pause_var.get(), include_new=not only_selected)
            return
        style_now = restore_style(settings)
        if style_now == "vendor":
            handback_vendor(targets)
            return

        def work():
            fails = []
            for i, dev in enumerate(targets, 1):
                set_status(f"恢復中… {i}/{len(targets)} {dev.name}")
                try:
                    ctl.turn_on(dev, style_now)
                except Exception as e:  # noqa: BLE001
                    fails.append(f"{dev.name}：{e}")
            return fails

        def done(fails):
            update_labels()
            if fails:
                status_var.set(f"完成，但有 {len(fails)} 個裝置失敗")
                messagebox.showwarning("部分裝置失敗", "\n".join(fails))
            else:
                status_var.set(f"已恢復 {len(targets)} 個裝置的燈光")
        run_bg(work, done)

    def turn_off_flow(keys, pause_first, include_new=False):
        def work():
            paused = False
            if pause_first and is_admin() and vendor_status():
                set_status("暫停原廠燈光軟體中…")
                pause_vendor()
                time.sleep(2)
                ctl.rescan(set_status)  # 原廠軟體釋放裝置後重新偵測
                paused = True
            if include_new and ctl.client:
                # 「全部關閉」：連暫停原廠軟體後才出現的裝置（例如滑鼠）也一起關
                with ctl.lock:
                    devs = list(ctl.client.devices)
                keys.update(ctl.key(d) for d in devs)
            else:
                devs = ctl.devices_by_keys(keys)
            expectations, fails = {}, []
            for i, dev in enumerate(devs, 1):
                set_status(f"關閉中… {i}/{len(devs)} {dev.name}")
                try:
                    expectations[ctl.key(dev)] = ctl.turn_off(dev)
                except Exception as e:  # noqa: BLE001
                    fails.append(f"{dev.name}：{e}")
            set_status("確認燈光有沒有被其他軟體改回來…")
            time.sleep(2.5)
            reverted = ctl.find_reverted(expectations)
            return {"paused": paused, "fails": fails, "reverted": reverted, "count": len(devs)}

        def done(r):
            if r["paused"]:
                fill_devices(ctl.client.devices if ctl.client else [], keys)
                refresh_vendor()
            else:
                update_labels()
            if r["fails"]:
                messagebox.showwarning("部分裝置失敗", "\n".join(r["fails"]))
            if not r["reverted"]:
                status_var.set(f"已關閉 {r['count'] - len(r['fails'])} 個裝置的燈光"
                               + ("（已暫停原廠燈光軟體）" if r["paused"] else ""))
                return
            names = "\n".join("・" + n for n in r["reverted"])
            status_var.set(f"有 {len(r['reverted'])} 個裝置的燈被其他軟體改回來了")
            if not r["paused"] and info["vendors"] and is_admin():
                if messagebox.askyesno(
                        "燈被改回來了",
                        f"下面的裝置關燈後，又被原廠燈光軟體改回來了：\n{names}\n\n"
                        "要暫停原廠燈光軟體後自動重試嗎？\n（重新開機後原廠軟體會自動恢復）"):
                    auto_pause_var.set(True)
                    save_auto_pause()
                    turn_off_flow(keys, True, include_new)
            elif not is_admin():
                messagebox.showinfo("燈被改回來了",
                                    f"下面的裝置關燈後又被改回來了：\n{names}\n\n"
                                    "請按「以系統管理員重新啟動」，再按一次關閉。")
            else:
                messagebox.showinfo("燈被改回來了",
                                    f"下面的裝置關燈後又被改回來了：\n{names}\n\n"
                                    "可能還有其他燈光程式在執行。\n請到「電腦配置」分頁按「複製診斷報告」，傳給幫忙的人。")
        run_bg(work, done)

    big = ttk.Frame(act)
    big.pack(fill="x")
    big.columnconfigure((0, 1), weight=1)
    ttk.Button(big, text="🌑 關閉勾選的燈光", style="Big.TButton",
               command=lambda: apply(False, True)).grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=2)
    ttk.Button(big, text="💡 恢復勾選的燈光", style="Big.TButton",
               command=lambda: apply(True, True)).grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=2)
    ttk.Button(big, text="全部關閉", command=lambda: apply(False, False)).grid(
        row=1, column=0, sticky="ew", padx=(0, 4), pady=2)
    ttk.Button(big, text="全部恢復", command=lambda: apply(True, False)).grid(
        row=1, column=1, sticky="ew", padx=(4, 0), pady=2)

    rest = ttk.Frame(act)
    rest.pack(fill="x", pady=(8, 0))
    ttk.Label(rest, text="恢復成：").pack(side="left")
    labels = [t for t, _ in RESTORE_CHOICES]
    values = [v for _, v in RESTORE_CHOICES]
    cur = settings.get("restore_style", "original")
    style_var = tk.StringVar(value=labels[values.index(cur)] if cur in values else labels[0])
    combo = ttk.Combobox(rest, textvariable=style_var, values=labels, state="readonly", width=28)
    combo.pack(side="left", padx=(0, 6))
    swatch = tk.Label(rest, text="      ", relief="solid", bd=1)
    color_btn = ttk.Button(rest, text="選顏色…")

    def show_color():
        r, g, b = settings.get("restore_color", [255, 255, 255])
        swatch.config(bg=f"#{r:02x}{g:02x}{b:02x}")
        if settings.get("restore_style") == "color":
            swatch.pack(side="left", padx=(0, 6))
            color_btn.pack(side="left")
        else:
            swatch.pack_forget()
            color_btn.pack_forget()

    def pick_color():
        r, g, b = settings.get("restore_color", [255, 255, 255])
        rgb, _ = colorchooser.askcolor(color=f"#{r:02x}{g:02x}{b:02x}", title="選擇恢復的燈光顏色")
        if rgb:
            settings["restore_color"] = [int(x) for x in rgb]
            save_json(SETTINGS_FILE, settings)
            show_color()

    def on_style(_=None):
        settings["restore_style"] = values[labels.index(style_var.get())]
        save_json(SETTINGS_FILE, settings)
        show_color()
        if settings["restore_style"] == "color" and "restore_color" not in settings:
            pick_color()

    color_btn.config(command=pick_color)
    combo.bind("<<ComboboxSelected>>", on_style)
    show_color()

    keep_var = tk.BooleanVar(value=settings.get("keep_server", False))

    def save_keep():
        settings["keep_server"] = keep_var.get()
        save_json(SETTINGS_FILE, settings)

    auto_pause_var = tk.BooleanVar(value=settings.get("auto_pause", True))

    def save_auto_pause():
        settings["auto_pause"] = auto_pause_var.get()
        save_json(SETTINGS_FILE, settings)

    ttk.Checkbutton(act, text="關燈時自動暫停原廠燈光軟體（建議開啟，否則燈可能被改回來）",
                    variable=auto_pause_var, command=save_auto_pause).pack(anchor="w", pady=(6, 0))
    ttk.Checkbutton(act, text="關閉本程式後讓 OpenRGB 留在背景（燈光較不會被其他程式改回來）",
                    variable=keep_var, command=save_keep).pack(anchor="w")

    # ======== 電腦配置分頁 ========
    ttk.Label(hwtab, text="這台電腦的硬體與燈光相關資訊（自動偵測）", font=bold).pack(anchor="w")
    hw_text = tk.Text(hwtab, wrap="word", height=20, relief="solid", bd=1, padx=8, pady=6)
    hw_text.pack(fill="both", expand=True, pady=8)
    hw_btns = ttk.Frame(hwtab)
    hw_btns.pack(fill="x")

    def build_report():
        hw = info["hw"] or {}
        lines = [f"【{APP_NAME} 診斷報告】{time.strftime('%Y-%m-%d %H:%M')}", ""]
        lines.append(f"作業系統：{hw.get('os', '偵測中…')}")
        lines.append(f"主機板：{hw.get('board', '偵測中…')}")
        lines.append(f"處理器：{hw.get('cpu', '偵測中…')}")
        for title, key in (("顯示卡", "gpu"), ("記憶體", "ram"), ("硬碟", "disk"),
                           ("鍵盤 / 滑鼠 / 周邊", "peripherals"), ("已安裝的燈光相關軟體", "software")):
            items = hw.get(key) or []
            lines.append(f"{title}：" + ("" if items else "（無）"))
            lines += [f"　・{x}" for x in items]
        lines.append("")
        lines.append("正在執行的原廠燈光軟體：" + ("" if info["vendors"] else "（無）"))
        lines += [f"　・{b}（{'、'.join(items)}）" for b, items in info["vendors"]]
        lines.append("")
        lines.append(f"可控制的燈光裝置（OpenRGB 偵測到 {len(info['devices'])} 個）：")
        for d in info["devices"]:
            try:
                modes = "、".join(m.name for m in d.modes[:10])
                active = d.modes[d.active_mode].name
            except Exception:  # noqa: BLE001
                modes, active = "", "?"
            lines.append(f"　・[{TYPE_NAMES.get(d.type.name, d.type.name)}] {d.name}")
            lines.append(f"　　目前模式：{active}；可用模式：{modes}")
            res = ctl.last_results.get(ctl.key(d))
            if res:
                lines.append(f"　　最近操作：{res}")
        lines.append("")
        usb = hw.get("usb") or []
        lines.append("USB 裝置代碼（用來查詢是否支援）：" + ("" if usb else "（無）"))
        lines += [f"　・{x}" for x in usb]
        lines.append("")
        orgb_lines = openrgb_log_issues()
        if orgb_lines:
            lines.append("OpenRGB 回報的問題：")
            lines += [f"　{x}" for x in orgb_lines]
            lines.append("")
        try:
            tail = LOG_FILE.read_text(encoding="utf-8").splitlines()[-25:]
        except OSError:
            tail = []
        if tail:
            lines.append("最近的操作記錄：")
            lines += [f"　{x}" for x in tail]
            lines.append("")
        lines.append(f"系統管理員：{'是' if is_admin() else '否'}　PawnIO：{'已安裝' if pawnio_installed() else '未安裝'}"
                     f"　OpenRGB：{'有' if find_openrgb() else '無'}")
        return "\n".join(lines)

    def show_report():
        hw_text.config(state="normal")
        hw_text.delete("1.0", "end")
        hw_text.insert("1.0", build_report())
        hw_text.config(state="disabled")

    def copy_report():
        root.clipboard_clear()
        root.clipboard_append(build_report())
        messagebox.showinfo("已複製", "診斷報告已複製，可以直接貼到 LINE 或訊息傳給幫忙的人。")

    def save_report():
        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
                desktop = Path(winreg.QueryValueEx(k, "Desktop")[0])
        except OSError:
            pass
        path = desktop / f"RGB診斷報告_{time.strftime('%Y%m%d_%H%M')}.txt"
        path.write_text(build_report(), encoding="utf-8-sig")
        messagebox.showinfo("已儲存", f"診斷報告已存到桌面：\n{path.name}")

    def do_detect_hw():
        hw_text.config(state="normal")
        hw_text.delete("1.0", "end")
        hw_text.insert("1.0", "偵測中，請稍候…")
        hw_text.config(state="disabled")

        def work():
            data = detect_hardware()
            root.after(0, lambda: (info.update(hw=data), show_report()))
        threading.Thread(target=work, daemon=True).start()

    ttk.Button(hw_btns, text="重新偵測", command=do_detect_hw).pack(side="left", padx=(0, 6))
    ttk.Button(hw_btns, text="複製診斷報告", command=copy_report).pack(side="left", padx=(0, 6))
    ttk.Button(hw_btns, text="存成文字檔到桌面", command=save_report).pack(side="left", padx=(0, 6))
    nb.bind("<<NotebookTabChanged>>", lambda e: show_report() if nb.index("current") == 1 and info["hw"] else None)

    # ======== 連線流程 ========
    def do_connect():
        if find_openrgb() is None and not port_open():
            if messagebox.askyesno("第一次使用",
                                   "需要先下載免費的 OpenRGB 控制核心（約 20MB，只需下載一次）。\n要現在下載嗎？"):
                do_download()
            else:
                status_var.set("請按「下載 OpenRGB」後才能控制燈光")
            return
        run_bg(lambda: ctl.connect(set_status), on_devices)

    def do_rescan():
        run_bg(lambda: ctl.rescan(set_status), on_devices)

    def on_devices(devs):
        fill_devices(devs)
        status_var.set(f"找到 {len(devs)} 個燈光裝置")
        watch_late_devices(len(devs))

    def watch_late_devices(known, tries=[0]):
        # 有些裝置（例如記憶體）偵測得比較慢：之後 15 秒內每 1.5 秒看一次，有新裝置就更新清單
        tries[0] = 0

        def tick():
            if tries[0] >= 10 or not ctl.client:
                return
            tries[0] += 1
            if busy["on"]:
                root.after(1500, tick)
                return

            def work():
                n = ctl.device_count()
                root.after(0, after, n)

            def after(n):
                nonlocal known
                if n != known and not busy["on"]:
                    known = n
                    fill_devices(ctl.client.devices)
                    status_var.set(f"找到 {n} 個燈光裝置")
                root.after(1500, tick)
            threading.Thread(target=work, daemon=True).start()
        root.after(1500, tick)

    def on_close():
        ctl.disconnect()
        if not keep_var.get():
            ctl.stop_server()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh_setup()
    refresh_vendor()
    do_detect_hw()
    root.after(300, do_connect)
    root.mainloop()


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    if "--all-off" in args:
        cli_mode(turn_on=False)
    elif "--all-on" in args:
        cli_mode(turn_on=True)
    else:
        gui()
