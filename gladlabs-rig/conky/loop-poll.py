#!/usr/bin/env python3
"""Poll OpenLinkHub (localhost:27003) for water-loop telemetry.

Prints shell KEY=VALUE lines consumed by strip.sh via eval. Values are
numbers or the literal -- so the output is always eval-safe.
Device serials are stable hardware identities (survive re-plugs/reboots).
"""
import json
import urllib.request

BASE = "http://127.0.0.1:27003/api/devices/"
HUBS = [
    "6FF347875FD0BA5D818DC599838DD666",
    "D88EC280CF680350A725FEFCE85D887D",
]
XC7 = "A62XT4520039MM"


def fetch(serial):
    with urllib.request.urlopen(BASE + serial, timeout=0.5) as r:
        return json.load(r)["device"]


fans = []
airtemps = []
pump_rpm = None
coolant = None
try:
    for h in HUBS:
        for _, dev in (fetch(h).get("devices") or {}).items():
            name = dev.get("name") or ""
            rpm = dev.get("rpm") or 0
            temp = dev.get("temperature") or 0
            if "XD" in name:
                pump_rpm = rpm
                coolant = temp
            elif "ADAPTER" in name:
                continue
            elif rpm > 0:
                fans.append(rpm)
                if temp > 1:
                    airtemps.append(temp)
except Exception:
    pass

block = None
try:
    block = fetch(XC7).get("Temperature")
except Exception:
    pass


def num(v, fmt="{:.1f}"):
    return fmt.format(v) if isinstance(v, (int, float)) and v else "--"


print(f"LOOP_COOL={num(coolant)}")
print(f"LOOP_BLOCK={num(block)}")
print(f"LOOP_PUMP={pump_rpm if pump_rpm else '--'}")
print(f"LOOP_FN={len(fans)}")
print(f"LOOP_FMIN={min(fans) if fans else '--'}")
print(f"LOOP_FMAX={max(fans) if fans else '--'}")
print(f"LOOP_FAVG={sum(fans)//len(fans) if fans else '--'}")
air = sum(airtemps) / len(airtemps) if airtemps else None
print(f"LOOP_AIR={num(air)}")
