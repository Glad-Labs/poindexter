"""Voice agent web client — phone-tap-to-join LiveKit room (poindexter#389).

A single endpoint, ``GET /voice/join``, that:

1. Mints a 7-day LiveKit JWT (HS256, signed with ``LIVEKIT_API_SECRET``)
   scoped to ``room=poindexter`` + ``identity=matt``.
2. Returns a self-contained HTML page that loads the LiveKit JS SDK
   from the CDN, auto-connects to the LiveKit signal endpoint via WSS
   (Tailscale-funneled), and renders a minimal mic-mute / volume /
   leave UI.

Why self-host instead of meet.livekit.io? Mixed-content. meet.livekit.io
is HTTPS, the local LiveKit signal is plain ``ws://`` (dev mode, no
TLS), and browsers block ws:// inside an https:// page. Self-hosting
on the same Tailscale-funneled hostname puts the page and the signal
proxy on the same origin → no mixed content.

Why a static HTML page instead of a React app? The page is ~80 lines.
Building a React app would be more code than the actual logic, plus
add a build step. The LiveKit JS SDK is the only "framework" needed.

Auth: gated behind ``Depends(verify_api_token)`` since 2026-05-12.

Pre-2026-05-12 this route was unauthenticated on the assumption that
the worker was Tailscale-only. The actual deployment exposes the
worker via Tailscale **Funnel** (public internet) at
``https://nightrider.taild4f626.ts.net/voice/join``, so any internet
visitor could mint a 7-day LiveKit JWT with room-join + publish +
subscribe + publish-data permissions for Matt's voice room. Security
audit caught it; this route now requires a valid OAuth JWT before
minting the LiveKit token, AND refuses to mint when
``LIVEKIT_API_SECRET`` is unset or equal to the dev placeholder
(fail-loud per the no-silent-defaults rule).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from middleware.api_token_auth import verify_api_token

router = APIRouter(prefix="/voice", tags=["voice"])


# 2026-05-12 security audit: refuse to mint LiveKit tokens with the dev
# placeholder. Surface a clear error so the operator sees the
# misconfiguration instead of silently shipping forgeable tokens.
_DEV_PLACEHOLDER_SECRET = "devsecret_change_me_change_me_change_me"


# ---------------------------------------------------------------------------
# JWT minter — HS256 with LIVEKIT_API_SECRET. We don't pull in PyJWT for
# this; the JWT shape is small and dependency-free is one less moving piece.
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint_livekit_token(
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    ttl_seconds: int = 7 * 24 * 3600,
) -> str:
    """LiveKit JWT shape per https://docs.livekit.io/realtime/concepts/authentication/."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "iat": now,
        "exp": now + ttl_seconds,
        "nbf": now,
        "name": identity,
        "video": {
            "roomJoin": True,
            "room": room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    h_enc = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p_enc = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h_enc}.{p_enc}".encode()
    sig = hmac.new(api_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h_enc}.{p_enc}.{_b64url(sig)}"


# ---------------------------------------------------------------------------
# /voice/join — HTML page that connects to the LiveKit room
# ---------------------------------------------------------------------------


@router.get("/join", response_class=HTMLResponse)
async def voice_join(
    _principal: str = Depends(verify_api_token),
) -> HTMLResponse:
    """Render the LiveKit web client with a freshly-minted JWT.

    Reads LIVEKIT_API_KEY + LIVEKIT_API_SECRET from env (matches the
    voice-agent-livekit container's env shape). LIVEKIT_WSS_URL points
    at the Tailscale-funneled wss endpoint; default assumes the
    standard /livekit-signal/ path on this funnel hostname.

    Requires a valid OAuth JWT (verified by ``verify_api_token``).
    The 2026-05-12 security audit caught this route shipping unauth
    over the Tailscale **Funnel** (public internet) — any visitor
    could mint a 7-day room-join token with publish + subscribe
    privileges for Matt's voice room. The gate closed that window.
    """
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not api_secret or api_secret == _DEV_PLACEHOLDER_SECRET or not api_key:
        # Fail-loud per the no-silent-defaults rule. Pre-fix this branch
        # silently minted forgeable tokens against the well-known dev
        # placeholder; that's worse than a 503 because forged tokens
        # can join the room undetected.
        raise HTTPException(
            status_code=503,
            detail=(
                "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured "
                "or still set to the dev placeholder. Refusing to mint "
                "tokens. Set both env vars on the worker container and "
                "restart."
            ),
        )

    room = os.environ.get("LIVEKIT_ROOM", "poindexter")
    identity = os.environ.get("LIVEKIT_DEFAULT_IDENTITY", "matt")
    # Default to the same-origin tailscale-funneled path. Operators can
    # override with the public-internet wss URL if running behind a
    # different reverse proxy.
    wss_url = os.environ.get(
        "LIVEKIT_PUBLIC_WSS_URL",
        "wss://nightrider.taild4f626.ts.net/livekit-signal/",
    )

    secret_warn = ""

    token = _mint_livekit_token(api_key, api_secret, identity, room)

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Talk to Poindexter — LiveKit</title>
<style>
:root {{ color-scheme: dark; }}
body {{
    font-family: -apple-system, system-ui, sans-serif;
    background: #0a0a0d; color: #e8e8ec;
    margin: 0; padding: 16px;
    max-width: 480px; margin-inline: auto;
}}
h1 {{ font-size: 20px; margin: 0 0 12px; }}
.status {{ font-size: 13px; color: #8a8a90; margin-bottom: 12px; }}
.row {{ display: flex; gap: 8px; align-items: center; margin: 8px 0; }}
button {{
    background: #1f6feb; color: white; border: 0; padding: 12px 18px;
    border-radius: 8px; font-size: 16px; cursor: pointer; flex: 1;
}}
button.danger {{ background: #b03030; }}
button.muted {{ background: #2a2a30; }}
button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
.log {{
    background: #14141a; padding: 10px; border-radius: 6px;
    font-family: ui-monospace, monospace; font-size: 12px;
    height: 160px; overflow-y: auto; margin-top: 12px;
}}
.log .line {{ margin: 2px 0; }}
.log .err {{ color: #f66; }}
.log .ok {{ color: #6f6; }}
</style>
</head><body>
<h1>Talk to Poindexter</h1>
<div class="status">Room: <b>{room}</b> · Identity: <b>{identity}</b> · 7-day token</div>
{secret_warn}
<div class="row">
    <button id="connect">Connect</button>
    <button id="mute" class="muted" disabled>Mute</button>
    <button id="leave" class="danger" disabled>Leave</button>
</div>
<div class="log" id="log"></div>
<script src="https://cdn.jsdelivr.net/npm/livekit-client@2.5.6/dist/livekit-client.umd.min.js"></script>
<script>
const TOKEN = {json.dumps(token)};
const WSS_URL = {json.dumps(wss_url)};
const logEl = document.getElementById('log');
const log = (msg, cls='') => {{
    const div = document.createElement('div');
    div.className = 'line ' + cls;
    div.textContent = new Date().toLocaleTimeString() + '  ' + msg;
    logEl.prepend(div);
}};
let room = null;
let micPub = null;
let audioEl = null;       // single managed audio element (re-bound on
                          // each new track instead of multiplying DOM nodes)
let statsTimer = null;    // periodic getStats() poller for live diagnostics
let lastBytesReceived = 0;
let silentTicks = 0;

function showStartAudioButton(label) {{
    if (document.getElementById('start-audio-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'start-audio-btn';
    btn.textContent = label;
    btn.style = 'margin-top:8px;background:#2a8c4a;width:100%';
    btn.onclick = async () => {{
        try {{
            await room.startAudio();
            if (audioEl) await audioEl.play();
            log('Audio enabled (manual)', 'ok');
            btn.remove();
        }} catch (e) {{
            log('Still blocked: ' + e.message, 'err');
        }}
    }};
    document.body.appendChild(btn);
}}

// Periodic getStats() over the subscriber peer connection — surfaces
// "audio bytes still flowing?" so a silent stretch is recognisable as
// EITHER "bot isn't talking" OR "packets dropped to zero." Helps
// disambiguate WiFi flakiness from idle.
async function pollSubscriberStats() {{
    if (!room || !room.engine || !room.engine.client || !room.engine.subscriber) return;
    try {{
        const pc = room.engine.subscriber.pc || room.engine.subscriber._pc;
        if (!pc || !pc.getStats) return;
        const stats = await pc.getStats();
        let bytes = 0, packets = 0, lost = 0;
        stats.forEach(r => {{
            if (r.type === 'inbound-rtp' && r.kind === 'audio') {{
                bytes = r.bytesReceived || 0;
                packets = r.packetsReceived || 0;
                lost = r.packetsLost || 0;
            }}
        }});
        const delta = bytes - lastBytesReceived;
        lastBytesReceived = bytes;
        if (delta < 200) {{
            silentTicks++;
            if (silentTicks >= 3) {{
                log('No audio bytes for 3 ticks (' + lost + ' lost so far) — track may be stalled', 'err');
                if (audioEl && audioEl.paused) {{
                    audioEl.play().then(() => log('Re-played audio after stall', 'ok')).catch(() => {{}});
                }}
                silentTicks = 0;
            }}
        }} else {{
            silentTicks = 0;
        }}
    }} catch (e) {{
        // Best-effort — different LiveKit versions expose pc differently.
    }}
}}

document.getElementById('connect').addEventListener('click', async () => {{
    document.getElementById('connect').disabled = true;
    log('Requesting microphone permission...');
    try {{
        room = new LivekitClient.Room({{ adaptiveStream: true, dynacast: true }});
        room
            .on(LivekitClient.RoomEvent.ParticipantConnected, p => log('Joined: ' + p.identity, 'ok'))
            .on(LivekitClient.RoomEvent.TrackSubscribed, (track, _pub, p) => {{
                log('Track from ' + p.identity + ': ' + track.kind, 'ok');
                if (track.kind === 'audio') {{
                    // Reuse the same <audio> element across (re)subscriptions
                    // instead of creating a new one each time. Each new element
                    // would otherwise have to clear mobile-Chrome's autoplay
                    // gate fresh, and the old elements would silently pile up
                    // in the DOM holding stale tracks.
                    if (!audioEl) {{
                        audioEl = document.createElement('audio');
                        audioEl.autoplay = true;
                        audioEl.controls = true;
                        audioEl.style = 'width:100%;margin-top:8px';
                        document.body.appendChild(audioEl);
                    }}
                    track.attach(audioEl);
                    const playPromise = audioEl.play();
                    if (playPromise !== undefined) {{
                        playPromise
                            .then(() => log('Audio playback started', 'ok'))
                            .catch(err => {{
                                log('Autoplay blocked: ' + err.name, 'err');
                                showStartAudioButton('▶ Tap to enable audio');
                            }});
                    }}
                }}
            }})
            .on(LivekitClient.RoomEvent.TrackUnsubscribed, (track, _pub, p) => {{
                log('Track UNsubscribed from ' + p.identity + ': ' + track.kind, 'err');
            }})
            .on(LivekitClient.RoomEvent.ConnectionQualityChanged, (quality, p) => {{
                log('Quality (' + p.identity + '): ' + quality);
            }})
            .on(LivekitClient.RoomEvent.Disconnected, reason => log('Disconnected: ' + reason, 'err'))
            .on(LivekitClient.RoomEvent.Reconnecting, () => log('Reconnecting...', 'err'))
            .on(LivekitClient.RoomEvent.Reconnected, () => log('Reconnected', 'ok'))
            .on(LivekitClient.RoomEvent.MediaDevicesError, e => log('Media error: ' + e.message, 'err'))
            .on(LivekitClient.RoomEvent.AudioPlaybackStatusChanged, () => {{
                log('Audio playback status: ' + (room.canPlaybackAudio ? 'OK' : 'BLOCKED'),
                    room.canPlaybackAudio ? 'ok' : 'err');
                if (!room.canPlaybackAudio) showStartAudioButton('▶ Tap to start audio');
            }});
        await room.connect(WSS_URL, TOKEN);
        log('Connected to ' + room.name, 'ok');
        log('Participants: ' + (room.numParticipants || 1));
        // Prime the audio context proactively (mobile-Chrome gesture
        // requirement) so the first incoming track plays without
        // needing a second tap. The Connect tap counts as the gesture.
        try {{ await room.startAudio(); }} catch (e) {{
            showStartAudioButton('▶ Tap to enable audio');
        }}
        micPub = await room.localParticipant.setMicrophoneEnabled(true);
        log('Mic on. Talk freely.', 'ok');
        document.getElementById('mute').disabled = false;
        document.getElementById('leave').disabled = false;
        // Start the watchdog poll. 3-second ticks = 9-second silent-stall
        // detection threshold (matches the Pipecat output buffer + Kokoro
        // generation worst case so we don't false-positive on a slow LLM).
        statsTimer = setInterval(pollSubscriberStats, 3000);
    }} catch (e) {{
        log('Connect failed: ' + e.message, 'err');
        document.getElementById('connect').disabled = false;
    }}
}});

document.getElementById('mute').addEventListener('click', async () => {{
    if (!room) return;
    const enabled = room.localParticipant.isMicrophoneEnabled;
    await room.localParticipant.setMicrophoneEnabled(!enabled);
    document.getElementById('mute').textContent = enabled ? 'Unmute' : 'Mute';
    log(enabled ? 'Muted' : 'Unmuted');
}});

document.getElementById('leave').addEventListener('click', async () => {{
    if (statsTimer) {{ clearInterval(statsTimer); statsTimer = null; }}
    if (room) {{ await room.disconnect(); room = null; }}
    if (audioEl) {{ audioEl.remove(); audioEl = null; }}
    lastBytesReceived = 0;
    silentTicks = 0;
    document.getElementById('connect').disabled = false;
    document.getElementById('mute').disabled = true;
    document.getElementById('leave').disabled = true;
    log('Left room');
}});
</script>
</body></html>
"""
    return HTMLResponse(content=html)
