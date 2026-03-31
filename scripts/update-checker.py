"""Weekly package update checker for Glad Labs infrastructure.
Checks winget for available upgrades, notifies Matt via Telegram.
Runs as a Windows Scheduled Task (weekly).
"""
import json
import os
import subprocess
import urllib.request

# Critical packages to track — alert immediately if updates available
CRITICAL_PACKAGES = {
    "Ollama.Ollama",
    "OpenJS.NodeJS.LTS",
    "Python.Python.3.12",
    "Python.Python.3.13",
    "GrafanaLabs.Alloy",
    "Git.Git",
    "Docker.DockerDesktop",
}

# Telegram config
TELEGRAM_CHAT_ID = "5318613610"


def get_telegram_token():
    """Read Telegram bot token from OpenClaw .env."""
    env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def send_telegram(message):
    """Send message to Matt via Telegram."""
    token = get_telegram_token()
    if not token:
        print("No Telegram token available")
        return
    try:
        payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")


def check_updates():
    """Run winget upgrade and parse available updates."""
    try:
        result = subprocess.run(
            ["winget", "upgrade", "--include-unknown"],
            capture_output=True, text=True, timeout=120,
        )
        lines = result.stdout.strip().split("\n")
    except Exception as e:
        return [], f"winget failed: {e}"

    updates = []
    parsing = False
    for line in lines:
        if line.startswith("Name") and "Id" in line and "Version" in line:
            parsing = True
            continue
        if line.startswith("-"):
            continue
        if parsing and line.strip():
            # winget output is column-aligned; split on 2+ spaces
            parts = [p.strip() for p in line.split("  ") if p.strip()]
            if len(parts) >= 4:
                name, pkg_id, current, available = parts[0], parts[1], parts[2], parts[3]
                updates.append({
                    "name": name,
                    "id": pkg_id,
                    "current": current,
                    "available": available,
                    "critical": pkg_id in CRITICAL_PACKAGES,
                })

    return updates, None


def check_processes():
    """Verify critical processes are running."""
    critical = {
        "ollama": "Ollama (LLM inference)",
        "aida64": "AIDA64 (sensor monitoring)",
        "alloy": "Grafana Alloy (metrics)",
        "python": "Python (worker/exporter/daemon)",
        "node": "Node.js (OpenClaw/Next.js)",
    }
    missing = []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Select-Object -ExpandProperty Name -Unique"],
            capture_output=True, text=True, timeout=10,
        )
        running = set(result.stdout.lower().split())
        for proc, desc in critical.items():
            if not any(proc in r for r in running):
                missing.append(f"{proc} ({desc})")
    except Exception:
        pass
    return missing


def main():
    updates, error = check_updates()
    missing = check_processes()

    if error:
        send_telegram(f"Update checker error: {error}")
        return

    critical_updates = [u for u in updates if u["critical"]]
    other_updates = [u for u in updates if not u["critical"]]

    # Build message
    parts = ["PC Health Check:"]

    if missing:
        parts.append(f"\nMISSING PROCESSES:")
        for m in missing:
            parts.append(f"  {m}")

    if critical_updates:
        parts.append(f"\nCRITICAL UPDATES ({len(critical_updates)}):")
        for u in critical_updates:
            parts.append(f"  {u['name']}: {u['current']} -> {u['available']}")

    if other_updates:
        parts.append(f"\nOther updates ({len(other_updates)}):")
        for u in other_updates[:10]:
            parts.append(f"  {u['name']}: {u['current']} -> {u['available']}")
        if len(other_updates) > 10:
            parts.append(f"  ... +{len(other_updates) - 10} more")

    if not updates and not missing:
        parts.append("All packages up to date. All processes running.")

    message = "\n".join(parts)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
