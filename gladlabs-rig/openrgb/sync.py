#!/usr/bin/env python3
"""Temperature-reactive ARGB sync via the OpenRGB SDK server.

Colors (deuteranomaly-safe diverging ramp, cool -> hot):
  #33bbee (cool blue) -> #eaeaea (near-white mid) -> #ee7733 (hot orange)

Targets and their temperature sources:
  - ASUS Aura headers (Strimer + case strips): coolant temp from OpenLinkHub
  - Corsair Vengeance DDR5 (x2): own SPD hwmon sensor (spd5118)
  - GPU shrouds (5090 Astral, 3090 Strix): own core temp via nvidia-smi
Writes are skipped when the target color is unchanged (no needless SMBus/USB
traffic). OpenLinkHub being down = hold last Aura color, keep ticking.
"""
import glob
import json
import subprocess
import time
import urllib.request

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

OLH_HUB = "http://127.0.0.1:27003/api/devices/D88EC280CF680350A725FEFCE85D887D"
TICK_S = 5
GPU_EVERY = 3  # nvidia-smi every 3rd tick

# Glad Labs thermal palette: mint (cool) -> amber (warm) -> amber-orange (hot)
COOL = (0x00, 0xE5, 0xD6)
MID = (0xFF, 0xB8, 0x33)
HOT = (0xFF, 0x80, 0x00)


def ramp(t, lo, hi):
    x = max(0.0, min(1.0, (t - lo) / (hi - lo)))
    if x < 0.5:
        a, b, f = COOL, MID, x / 0.5
    else:
        a, b, f = MID, HOT, (x - 0.5) / 0.5
    return RGBColor(*(round(a[i] + (b[i] - a[i]) * f) for i in range(3)))


def coolant_temp():
    try:
        with urllib.request.urlopen(OLH_HUB, timeout=2) as r:
            return float(json.load(r)["device"]["devices"]["13"]["temperature"])
    except Exception:
        return None


def dimm_temps():
    out = []
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            if open(d + "/name").read().strip() == "spd5118":
                out.append(int(open(d + "/temp1_input").read()) / 1000)
        except OSError:
            pass
    return out


def gpu_temps():
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        return [float(x) for x in res.stdout.split()]
    except Exception:
        return []


def set_static(dev, color):
    mode = next((m for m in dev.modes if m.name.lower() == "static"), None)
    if mode is None:
        dev.set_color(color)
        return
    if mode.colors is not None:
        mode.colors = [color] * len(mode.colors) if mode.colors else [color]
    dev.set_mode(mode)
    try:
        dev.set_color(color)
    except Exception:
        pass


def log(msg):
    print(msg, flush=True)


def main():
    client = None
    last = {}
    tick = 0
    gpus_cached = []
    while True:
        try:
            if client is None:
                client = OpenRGBClient("127.0.0.1", 6742, "temp-sync")
                last.clear()
                log(f"connected: {len(client.devices)} devices")

            if not client.devices:
                client.update()
                log(f"device list refreshed: {len(client.devices)} devices")

            aura = [d for d in client.devices if "X870E" in d.name]
            dimms = [d for d in client.devices if "Vengeance" in d.name]
            gpus = [d for d in client.devices if "GeForce" in d.name]

            cool = coolant_temp()
            if aura and cool is not None:
                c = ramp(cool, 28, 38)
                if last.get("aura") != c.__dict__:
                    for d in aura:
                        set_static(d, c)
                    last["aura"] = c.__dict__
                    log(f"aura <- {c} (coolant {cool}°)")

            for i, (d, t) in enumerate(zip(dimms, dimm_temps())):
                c = ramp(t, 40, 50)
                if last.get(f"dimm{i}") != c.__dict__:
                    d.set_color(c)
                    last[f"dimm{i}"] = c.__dict__

            if tick % GPU_EVERY == 0:
                gpus_cached = gpu_temps()
            # nvidia-smi order: 0=5090 (Astral), 1=3090 (Strix)
            for d in gpus:
                idx = 0 if "5090" in d.name else 1
                if idx < len(gpus_cached):
                    c = ramp(gpus_cached[idx], 35, 60)
                    if last.get(f"gpu{idx}") != c.__dict__:
                        set_static(d, c)
                        last[f"gpu{idx}"] = c.__dict__
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                if client is not None:
                    client.disconnect()
            except Exception:
                pass
            client = None
            time.sleep(10)
        tick += 1
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
