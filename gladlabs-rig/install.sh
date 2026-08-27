#!/usr/bin/env bash
# Restore the rig sensor/lighting stack from this snapshot. Idempotent.
# Prereqs (see README.md): conky-all, python3-pil, OpenLinkHub deb, OpenRGB deb.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

echo "== user configs =="
mkdir -p ~/.config/btop/themes ~/.config/conky ~/.config/OpenRGB ~/.config/openrgb-temp-sync ~/.config/systemd/user
cp -v "$HERE"/btop/btop.conf ~/.config/btop/
cp -v "$HERE"/btop/gladlabs.theme ~/.config/btop/themes/
cp -v "$HERE"/conky/sensor-strip.conf "$HERE"/conky/strip.sh "$HERE"/conky/launch-strip.sh "$HERE"/conky/loop-poll.py "$HERE"/conky/make-strip-bg.py ~/.config/conky/
chmod +x ~/.config/conky/strip.sh ~/.config/conky/launch-strip.sh
python3 ~/.config/conky/make-strip-bg.py
cp -v "$HERE"/openrgb/OpenRGB.json ~/.config/OpenRGB/
cp -v "$HERE"/openrgb/sync.py ~/.config/openrgb-temp-sync/

# Shadows the packaged conky.desktop, whose bare `conky --daemonize` loads stock
# /etc/conky/conky.conf on the primary monitor instead of the strip.
echo "== app-menu entry =="
mkdir -p ~/.local/share/applications
cp -v "$HERE"/applications/conky.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications 2>/dev/null || true

echo "== openrgb-python venv =="
[ -x ~/.local/share/openrgb-venv/bin/python ] || python3 -m venv ~/.local/share/openrgb-venv
~/.local/share/openrgb-venv/bin/pip install -q openrgb-python

echo "== systemd user units =="
cp -v "$HERE"/systemd-user/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable conky-strip openrgb-server argb-temp-sync

echo "== OpenLinkHub (sudo) =="
sudo install -m 755 "$HERE"/openlinkhub/olh-coolant-temp /usr/local/bin/olh-coolant-temp
sudo cp -v "$HERE"/openlinkhub/config.json /opt/OpenLinkHub/
sudo cp -v "$HERE"/openlinkhub/rgb.json /opt/OpenLinkHub/database/
sudo cp -v "$HERE"/openlinkhub/temperatures/*.json /opt/OpenLinkHub/database/temperatures/
sudo cp -v "$HERE"/openlinkhub/profiles/*.json /opt/OpenLinkHub/database/profiles/
sudo mkdir -p /opt/OpenLinkHub/database/rgb
sudo cp -v "$HERE"/openlinkhub/rgb-device/*.json /opt/OpenLinkHub/database/rgb/
# XC7 block LCD animation. Generated rather than committed; the restart below is
# what loads it, since OpenLinkHub caches images at startup.
python3 "$HERE"/openlinkhub/make-lcd-gif.py /tmp
sudo cp -v /tmp/gladlabsloop.gif /opt/OpenLinkHub/database/lcd/images/
sudo cp -v "$HERE"/openlinkhub/lcd/animation.json /opt/OpenLinkHub/database/lcd/
sudo chown -R openlinkhub:openlinkhub /opt/OpenLinkHub/database /opt/OpenLinkHub/config.json 2>/dev/null || true

echo "== restart services =="
sudo systemctl restart OpenLinkHub
systemctl --user restart openrgb-server
sleep 8
systemctl --user restart argb-temp-sync conky-strip

echo "done — strip panel, fan curves, and temp lighting restored"
