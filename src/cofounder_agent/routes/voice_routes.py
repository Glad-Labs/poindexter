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

Auth: **tailnet-only** as of 2026-06-02 — served via Tailscale
**Serve** (not Funnel) and gated on the ``Tailscale-User-Login`` header
through ``require_tailnet``. It also refuses to mint when
``LIVEKIT_API_SECRET`` is unset or equal to the dev placeholder
(fail-loud per the no-silent-defaults rule).

History: pre-2026-05-12 the route was unauthenticated on the assumption
that the worker was Tailscale-only. Operators exposing it over a
Tailscale **Funnel** (public internet) found that any visitor could
mint a 7-day LiveKit JWT (room-join + publish + subscribe) for the
operator's voice room. The 2026-05-12 audit bolted on machine OAuth —
which closed that hole but locked out the operator's *phone* (a browser
has no client-credentials bearer). The real fix (2026-06-02) addresses
the actual mistake: move ``/voice`` off the public Funnel onto Serve
(tailnet-only) and gate on the Serve-set ``Tailscale-User-Login``
header, which a phone on the tailnet carries and the public internet
never can.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/voice", tags=["voice"])


def require_tailnet(request: Request) -> str:
    """Allow only Tailscale-tailnet callers (Serve), reject public ones.

    ``tailscale serve`` injects a ``Tailscale-User-Login`` header
    identifying the authenticated tailnet device on every proxied
    request. Public **Funnel** traffic never carries it, and Tailscale
    strips any client-supplied ``Tailscale-*`` headers at ingress, so a
    public visitor cannot forge it.

    This replaced the OAuth gate on ``/voice/join`` (2026-06-02). The
    route mints a 7-day LiveKit room token; the 2026-05-12 audit gated
    it behind machine OAuth because it was exposed over a public Funnel,
    but that locked out the operator's phone (a browser has no bearer).
    The route is now served tailnet-only (Serve, not Funnel), and this
    header — set by Serve for authenticated tailnet devices — is the
    auth boundary. A phone on the tailnet gets in with no bearer; the
    public internet can't reach the route at all (and is denied here
    even if a topology mistake re-exposes it). See
    ``feedback_no_silent_defaults`` — fail closed when the signal is
    absent.
    """
    identity = request.headers.get("Tailscale-User-Login", "").strip()
    if not identity:
        raise HTTPException(
            status_code=403,
            detail=(
                "Voice is tailnet-only. Open this page over Tailscale "
                "(Serve), not the public internet."
            ),
        )
    return identity


# 2026-05-12 security audit: refuse to mint LiveKit tokens with the dev
# placeholder. Surface a clear error so the operator sees the
# misconfiguration instead of silently shipping forgeable tokens.
_DEV_PLACEHOLDER_SECRET = "devsecret_change_me_change_me_change_me"

# Two-room voice split (#1006). The join page mints a token scoped to ONE of
# these rooms, picked via ``?room=``. Allow-listed so a typo or hostile value
# can't mint a token for an arbitrary room; anything else -> the default.
#   * poindexter  — always-on Emma (local GLM ops assistant)
#   * claude-code — the claude -p dev brain (host-side; full repo/git/write)
_ALLOWED_VOICE_ROOMS = ("poindexter", "claude-code")


def _resolve_voice_room(requested: str | None, default_room: str = "poindexter") -> str:
    """Pick the LiveKit room for a join request (#1006 two-room split).

    Allow-listed against :data:`_ALLOWED_VOICE_ROOMS`; whitespace/case are
    normalised. An unknown or absent value falls back to ``default_room``
    (caller-supplied, typically the DB-seeded ``voice_agent_room_name``) so
    a typo can't mint a token for an arbitrary room.
    """
    req = (requested or "").strip().lower()
    return req if req in _ALLOWED_VOICE_ROOMS else default_room


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
    name: str | None = None,
) -> str:
    """LiveKit JWT shape per https://docs.livekit.io/realtime/concepts/authentication/.

    ``identity`` must be UNIQUE per room (LiveKit kicks a duplicate-identity
    participant), so it carries a per-device suffix; ``name`` is the
    human-facing display label (defaults to ``identity``).
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "iat": now,
        "exp": now + ttl_seconds,
        "nbf": now,
        "name": name or identity,
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
    request: Request,
    room: str | None = Query(
        default=None,
        description=(
            "LiveKit room to join: 'poindexter' (Emma/GLM ops) or "
            "'claude-code' (the dev brain). Unknown/absent -> operator default."
        ),
    ),
    _tailnet_user: str = Depends(require_tailnet),
) -> HTMLResponse:
    """Render the LiveKit web client with a freshly-minted JWT.

    Resolves all LiveKit config **DB-first** (#1000, #717): creds from
    ``app_settings`` (``livekit_api_key`` / ``livekit_api_secret``), room from
    ``voice_agent_room_name``, identity from ``voice_agent_default_identity``,
    and the WSS URL from ``voice_agent_livekit_url`` — all via the
    lifespan-bound SiteConfig, falling back to env vars when those rows are
    empty so rotation and room config are one place instead of every minter.

    Requires a tailnet caller — the ``Tailscale-User-Login`` header set
    by Tailscale Serve, verified by ``require_tailnet`` (NOT an OAuth
    bearer, so the operator's phone browser works). Public Funnel
    traffic never carries that header, so the voice room stays protected
    even if the route is accidentally re-exposed over a Funnel.
    """
    # DB-first creds (#1000) — app_settings, then env. site_config is bound to
    # app.state by main.py's lifespan; absent in router-only tests, where the
    # resolver falls back to env (monkeypatched LIVEKIT_API_* there).
    from services.voice_pipecat import resolve_livekit_creds_async

    site_config = getattr(getattr(request.app, "state", None), "site_config", None)
    wss_url, api_key, api_secret = await resolve_livekit_creds_async(site_config)
    if (
        not api_secret
        or api_secret == _DEV_PLACEHOLDER_SECRET
        or not api_key
        or api_key == "devkey"  # the resolver's dev default == unconfigured
    ):
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

    # DB-first room, identity, wss_url (#717).  site_config.get() returns ''
    # for unseeded keys; treat '' as unset so the env (or hard default) wins.
    default_room = (
        site_config.get("voice_agent_room_name", "") if site_config is not None else ""
    ) or os.environ.get("LIVEKIT_ROOM", "poindexter")
    room = _resolve_voice_room(room, default_room)
    # UNIQUE identity per device/page-load (#1006). LiveKit requires a unique
    # identity per room — two clients sharing one identity collide
    # (DUPLICATE_IDENTITY) and flap, each kicking the other every few seconds
    # (observed 2026-06-04: phone + idle PC both joined as "operator"/"matt"
    # and booted each other, so neither heard replies). Appending a random
    # suffix lets multiple devices coexist; the bot keys on speech (VAD), not
    # identity, so this is transparent to it. A LiveKit auto-reconnect reuses
    # the same minted token (same suffix → resumes); only a fresh page-load
    # gets a new identity. ``name`` keeps a clean human-facing label.
    display_name = (
        site_config.get("voice_agent_default_identity", "") if site_config is not None else ""
    ) or os.environ.get("LIVEKIT_DEFAULT_IDENTITY", "operator")
    identity = f"{display_name}-{secrets.token_hex(3)}"
    # wss_url: already resolved DB-first by resolve_livekit_creds_async above
    # via voice_agent_livekit_url (app_settings) → LIVEKIT_URL → ws://localhost:7880.

    secret_warn = ""

    token = _mint_livekit_token(api_key, api_secret, identity, room, name=display_name)

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
