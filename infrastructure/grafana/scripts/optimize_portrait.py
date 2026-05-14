"""Optimize Grafana dashboards for 960x1080 portrait monitor and take validation screenshots."""
import json, asyncio, urllib.request
from playwright.async_api import async_playwright

GPU_LAYOUT = {
    1:  {"h": 6, "w": 12, "x": 0,  "y": 0},
    2:  {"h": 6, "w": 12, "x": 12, "y": 0},
    3:  {"h": 6, "w": 12, "x": 0,  "y": 6},
    4:  {"h": 6, "w": 12, "x": 12, "y": 6},
    5:  {"h": 6, "w": 12, "x": 0,  "y": 12},
    6:  {"h": 6, "w": 12, "x": 12, "y": 12},
    10: {"h": 10, "w": 24, "x": 0, "y": 18},
    11: {"h": 10, "w": 24, "x": 0, "y": 28},
    12: {"h": 10, "w": 24, "x": 0, "y": 38},
    13: {"h": 10, "w": 24, "x": 0, "y": 48},
}

CC_LAYOUT = {
    1:  {"h": 4, "w": 12, "x": 0,  "y": 0},
    2:  {"h": 4, "w": 12, "x": 12, "y": 0},
    3:  {"h": 4, "w": 12, "x": 0,  "y": 4},
    4:  {"h": 4, "w": 12, "x": 12, "y": 4},
    5:  {"h": 4, "w": 12, "x": 0,  "y": 8},
    6:  {"h": 4, "w": 12, "x": 12, "y": 8},
    10: {"h": 10, "w": 24, "x": 0, "y": 12},
    11: {"h": 10, "w": 24, "x": 0, "y": 22},
    20: {"h": 10, "w": 24, "x": 0, "y": 32},
    21: {"h": 8,  "w": 24, "x": 0, "y": 42},
    30: {"h": 8,  "w": 24, "x": 0, "y": 50},
    31: {"h": 10, "w": 24, "x": 0, "y": 58},
    40: {"h": 8,  "w": 24, "x": 0, "y": 68},
}

BO_LAYOUT = {
    1:  {"h": 4, "w": 12, "x": 0,  "y": 0},
    8:  {"h": 4, "w": 12, "x": 12, "y": 0},
    10: {"h": 4, "w": 12, "x": 0,  "y": 4},
    14: {"h": 4, "w": 12, "x": 12, "y": 4},
    2:  {"h": 8, "w": 24, "x": 0,  "y": 8},
    5:  {"h": 10, "w": 24, "x": 0, "y": 16},
    9:  {"h": 8, "w": 24, "x": 0,  "y": 26},
    11: {"h": 10, "w": 24, "x": 0, "y": 34},
    13: {"h": 10, "w": 24, "x": 0, "y": 44},
    7:  {"h": 8, "w": 24, "x": 0,  "y": 54},
}

BASE_URL = "http://localhost:3000"
import base64
AUTH_HEADER = "Basic " + base64.b64encode(b"admin:gladlabs").decode()


def api_get(uid):
    url = f"{BASE_URL}/api/dashboards/uid/{uid}"
    req = urllib.request.Request(url, headers={"Authorization": AUTH_HEADER})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def api_save(dashboard):
    url = f"{BASE_URL}/api/dashboards/db"
    payload = json.dumps({"dashboard": dashboard, "overwrite": True}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": AUTH_HEADER,
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def optimize_dashboard(uid, layout, is_gpu=False):
    data = api_get(uid)
    dash = data["dashboard"]
    for panel in dash["panels"]:
        pid = panel["id"]
        if pid in layout:
            panel["gridPos"] = layout[pid]
        if panel.get("type") == "gauge" and is_gpu:
            panel.setdefault("options", {})["text"] = {"titleSize": 16, "valueSize": 40}
        if panel.get("type") == "stat":
            panel.setdefault("options", {})["textMode"] = "value"
            panel["options"]["graphMode"] = "none"
            panel["options"]["text"] = {"titleSize": 14, "valueSize": 48}
        if panel.get("type") == "piechart":
            opts = panel.setdefault("options", {})
            if "legend" in opts:
                opts["legend"]["placement"] = "bottom"
    result = api_save(dash)
    print(f"  API save {uid}: version={result.get('version', '?')}")
    return dash


def write_local_file(filepath, layout, is_gpu=False):
    with open(filepath, encoding="utf-8") as f:
        d = json.load(f)
    for panel in d["panels"]:
        pid = panel["id"]
        if pid in layout:
            panel["gridPos"] = layout[pid]
        if panel.get("type") == "gauge" and is_gpu:
            panel.setdefault("options", {})["text"] = {"titleSize": 16, "valueSize": 40}
        if panel.get("type") == "stat":
            panel.setdefault("options", {})["textMode"] = "value"
            panel["options"]["graphMode"] = "none"
            panel["options"]["text"] = {"titleSize": 14, "valueSize": 48}
        if panel.get("type") == "piechart":
            opts = panel.setdefault("options", {})
            if "legend" in opts:
                opts["legend"]["placement"] = "bottom"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print(f"  File written: {filepath}")


async def main():
    # Resolve the repo root from this file's location (the script
# lives at infrastructure/grafana/scripts/optimize_portrait.py;
# repo root is three parents up).
import os, pathlib
base = str(pathlib.Path(__file__).resolve().parents[3])
    ss_dir = f"{base}/infrastructure/grafana/screenshots"
    db_dir = f"{base}/infrastructure/grafana/dashboards"

    configs = [
        ("gpu-metrics", "gpu-metrics", GPU_LAYOUT, True),
        ("command-center", "glad-labs-command-center", CC_LAYOUT, False),
        ("brain-operations", "brain-operations", BO_LAYOUT, False),
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 960, "height": 1080})
        page = await context.new_page()

        # Login
        await page.goto("http://localhost:3000/login")
        await page.fill('input[name="user"]', "admin")
        await page.fill('input[name="password"]', "gladlabs")
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        for uid, slug, layout, is_gpu in configs:
            print(f"\nOptimizing {uid}...")

            # Push via API
            optimize_dashboard(uid, layout, is_gpu)

            # Also write local file
            write_local_file(f"{db_dir}/{uid.replace('-', '-')}.json", layout, is_gpu)

            # Load dashboard immediately
            await page.goto(f"http://localhost:3000/d/{uid}/{slug}")
            await page.wait_for_load_state("networkidle")

            # Wait for SVG/canvas panel content
            try:
                await page.wait_for_selector("svg", timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(4)

            # Scroll to trigger lazy loading
            for i in range(25):
                await page.evaluate("window.scrollBy(0, 400)")
                await asyncio.sleep(0.1)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)

            # Verify panel dimensions
            panel_info = await page.evaluate("""() => {
                const items = document.querySelectorAll('.react-grid-item');
                return Array.from(items).map(el => ({
                    text: el.textContent.substring(0, 80).trim(),
                    w: Math.round(el.getBoundingClientRect().width),
                    h: Math.round(el.getBoundingClientRect().height)
                }));
            }""")
            for i, pt in enumerate(panel_info):
                print(f"  Panel {i}: {pt['w']}x{pt['h']}  \"{pt['text'][:60]}\"")

            await page.screenshot(
                path=f"{ss_dir}/{uid}-portrait.png",
                full_page=True
            )
            print(f"  Screenshot saved: {uid}-portrait.png")

        await browser.close()
    print("\nDone!")


asyncio.run(main())
