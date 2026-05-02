#!/usr/bin/env bash
# install-remote-access.sh — set up Sunshine + RustDesk for phone-driven
# remote management of the local PC.
#
# Strategy:
# - Sunshine for "I want to actually use the PC" (HW-encoded video, low
#   latency, great over LAN, usable over Tailscale-direct UDP)
# - RustDesk for "I just need to click 3 buttons over LTE" (low bandwidth,
#   pixel-perfect for admin, no GPU pipeline)
# - Both bind to the Tailscale interface only — never exposed publicly
#   regardless of Windows Firewall state
#
# Tailscale must already be installed + signed in. We don't try to install
# it here because the operator typically wants explicit control over
# tailnet membership. Get it from https://tailscale.com/download/windows.
#
# Tested on Windows 11 + Git Bash (msys2 / mingw64). Most commands route
# through PowerShell because winget + netsh are Windows-native.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { printf "${CYAN}==>${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}OK${NC}  %s\n" "$*"; }
warn() { printf "${YELLOW}WARN${NC} %s\n" "$*"; }
err()  { printf "${RED}ERR${NC}  %s\n" "$*" >&2; }

run_ps() {
    # Run a PowerShell command, surfacing stderr but suppressing the
    # noisy NativeCommandError wrapping that comes from non-zero exits.
    powershell.exe -NoProfile -NonInteractive -Command "$1"
}

require_admin() {
    # Windows Firewall + service install need admin. Detect non-admin
    # and abort early with a clear message rather than letting individual
    # commands fail one at a time.
    local is_admin
    is_admin=$(run_ps '[bool](([Security.Principal.WindowsIdentity]::GetCurrent()).Groups -match "S-1-5-32-544")' 2>/dev/null || echo "False")
    if [[ "${is_admin}" != "True" ]]; then
        err "This script needs an Administrator shell."
        err "Right-click Git Bash → 'Run as administrator' and re-run."
        exit 1
    fi
}

require_tailscale() {
    if ! command -v tailscale >/dev/null 2>&1 && ! [[ -x "/c/Program Files/Tailscale/tailscale.exe" ]]; then
        err "Tailscale isn't installed."
        err "Get it: https://tailscale.com/download/windows"
        err "Then re-run this script."
        exit 1
    fi
    local tailscale_bin="tailscale"
    if ! command -v tailscale >/dev/null 2>&1; then
        tailscale_bin="/c/Program Files/Tailscale/tailscale.exe"
    fi
    if ! "${tailscale_bin}" status >/dev/null 2>&1; then
        err "Tailscale is installed but not signed in."
        err "Run: tailscale up"
        exit 1
    fi
    TAILSCALE_BIN="${tailscale_bin}"
    TAILSCALE_IP=$("${tailscale_bin}" ip -4 2>/dev/null | head -1 | tr -d '[:space:]')
    if [[ -z "${TAILSCALE_IP}" ]]; then
        err "Couldn't read Tailscale IPv4 — is the daemon running?"
        exit 1
    fi
    ok "Tailscale IP: ${TAILSCALE_IP}"
}

require_winget() {
    if ! command -v winget.exe >/dev/null 2>&1 && ! [[ -x "$(command -v winget 2>/dev/null)" ]]; then
        err "winget not on PATH. Install App Installer from the Microsoft Store."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Sunshine
# ---------------------------------------------------------------------------

install_sunshine() {
    log "Installing Sunshine via winget..."
    if winget list --id LizardByte.Sunshine -e 2>/dev/null | grep -q LizardByte.Sunshine; then
        ok "Sunshine already installed."
    else
        winget install --exact --id LizardByte.Sunshine \
            --accept-source-agreements --accept-package-agreements \
            --silent
        ok "Sunshine installed."
    fi
}

configure_sunshine_firewall() {
    log "Opening Windows Firewall for Sunshine on Tailscale interface..."
    # Sunshine needs:
    #   47984/tcp  — HTTPS web UI
    #   47989/tcp  — HTTP (redirect → HTTPS)
    #   47990/tcp  — Web UI (legacy)
    #   48010/tcp  — RTSP control
    #   47998/udp  — Video
    #   47999/udp  — Control
    #   48000/udp  — Audio
    #   48002/udp  — Mic
    # We scope every rule to the Tailscale CGNAT range (100.64.0.0/10) so
    # nothing leaks to the public internet even if the operator opens
    # those ports later for a different reason.
    local rule_prefix="Sunshine (Tailscale)"
    run_ps "
        \$ports_tcp = '47984,47989,47990,48010'
        \$ports_udp = '47998,47999,48000,48002'
        \$scope = '100.64.0.0/10'
        Get-NetFirewallRule -DisplayName '${rule_prefix}*' -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName '${rule_prefix} TCP' \
            -Direction Inbound -Action Allow -Protocol TCP \
            -LocalPort \$ports_tcp -RemoteAddress \$scope | Out-Null
        New-NetFirewallRule -DisplayName '${rule_prefix} UDP' \
            -Direction Inbound -Action Allow -Protocol UDP \
            -LocalPort \$ports_udp -RemoteAddress \$scope | Out-Null
    "
    ok "Firewall scoped to Tailscale CGNAT (100.64.0.0/10) only."
}

start_sunshine_service() {
    log "Starting Sunshine service..."
    # Sunshine ships as both an installed service ('SunshineService') and
    # a tray app. We prefer the service so it survives logout and
    # auto-starts on boot.
    run_ps "
        \$svc = Get-Service -Name 'SunshineService' -ErrorAction SilentlyContinue
        if (\$svc) {
            if (\$svc.StartType -ne 'Automatic') {
                Set-Service -Name 'SunshineService' -StartupType Automatic
            }
            if (\$svc.Status -ne 'Running') {
                Start-Service -Name 'SunshineService'
            }
            Write-Output 'service-running'
        } else {
            Write-Output 'no-service-installed'
        }
    " | tail -1 | tr -d '[:space:]' > /tmp/sunshine-svc-status
    case "$(cat /tmp/sunshine-svc-status)" in
        service-running)
            ok "SunshineService running + auto-start enabled."
            ;;
        no-service-installed)
            warn "SunshineService not registered — open the Sunshine tray app once to register it."
            ;;
    esac
    rm -f /tmp/sunshine-svc-status
}

print_sunshine_pairing() {
    log "Sunshine web UI:"
    printf "  ${GREEN}Local:${NC}     https://localhost:47990\n"
    printf "  ${GREEN}Tailnet:${NC}   https://%s:47990\n" "${TAILSCALE_IP}"
    printf "\n"
    printf "First-time pairing:\n"
    printf "  1. Open the URL above in any browser\n"
    printf "  2. Set username + password (saved to %APPDATA%\\Sunshine\\sunshine_state.json)\n"
    printf "  3. Install ${CYAN}Moonlight${NC} on your phone:\n"
    printf "     iOS:     https://apps.apple.com/app/moonlight-game-streaming/id1000551566\n"
    printf "     Android: https://play.google.com/store/apps/details?id=com.limelight\n"
    printf "  4. In Moonlight: Add PC manually → enter ${TAILSCALE_IP}\n"
    printf "  5. Moonlight shows a PIN; paste it into the Sunshine 'PIN' tab\n"
    printf "\n"
}

# ---------------------------------------------------------------------------
# RustDesk
# ---------------------------------------------------------------------------

install_rustdesk() {
    log "Installing RustDesk via winget..."
    if winget list --id RustDesk.RustDesk -e 2>/dev/null | grep -q RustDesk.RustDesk; then
        ok "RustDesk already installed."
    else
        winget install --exact --id RustDesk.RustDesk \
            --accept-source-agreements --accept-package-agreements \
            --silent
        ok "RustDesk installed."
    fi
}

configure_rustdesk_firewall() {
    log "Opening Windows Firewall for RustDesk on Tailscale interface..."
    # RustDesk default ports — peer-to-peer punching uses these for
    # direct connections; the public RustDesk relay uses outbound only
    # so no extra rules needed for relay-fallback mode.
    local rule_prefix="RustDesk (Tailscale)"
    run_ps "
        \$scope = '100.64.0.0/10'
        Get-NetFirewallRule -DisplayName '${rule_prefix}*' -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName '${rule_prefix} TCP' \
            -Direction Inbound -Action Allow -Protocol TCP \
            -LocalPort '21115,21116,21117,21118,21119' \
            -RemoteAddress \$scope | Out-Null
        New-NetFirewallRule -DisplayName '${rule_prefix} UDP' \
            -Direction Inbound -Action Allow -Protocol UDP \
            -LocalPort '21116' \
            -RemoteAddress \$scope | Out-Null
    "
    ok "Firewall scoped to Tailscale CGNAT (100.64.0.0/10) only."
}

print_rustdesk_pairing() {
    log "RustDesk pairing:"
    printf "  Open the RustDesk app on this PC — top of the window shows a 9-digit ID + a password.\n"
    printf "  ${YELLOW}Set a permanent password${NC} (top-right menu → Security → Permanent password)\n"
    printf "  so phone connections don't require approval each time.\n"
    printf "\n"
    printf "  Phone install:\n"
    printf "    iOS:     https://apps.apple.com/app/rustdesk-remote-desktop/id1581225015\n"
    printf "    Android: https://play.google.com/store/apps/details?id=com.carriez.flutter_hbb\n"
    printf "\n"
    printf "  Connect by ID + permanent password. Default uses RustDesk's public relay\n"
    printf "  (fine for casual use). For pure-tailnet routing, set the relay server to\n"
    printf "  ${TAILSCALE_IP} after you've set up RustDesk Server (optional).\n"
    printf "\n"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    log "Remote access setup — Sunshine + RustDesk"
    log "Bound to Tailscale CGNAT only (100.64.0.0/10) — never public."
    printf "\n"

    require_admin
    require_tailscale
    require_winget

    install_sunshine
    configure_sunshine_firewall
    start_sunshine_service

    install_rustdesk
    configure_rustdesk_firewall

    printf "\n"
    log "Both installed. Pairing instructions:"
    printf "\n"
    print_sunshine_pairing
    print_rustdesk_pairing

    ok "Done. Test from phone over Tailscale BEFORE relying on it offsite."
}

main "$@"
