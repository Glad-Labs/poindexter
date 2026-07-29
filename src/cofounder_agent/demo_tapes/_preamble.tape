# Shared VHS preamble — prepended to every demo tape by
# ``services/demo_clips.py``. Individual tapes carry only their metadata
# header and their Type/Enter/Wait content, so the brand look lives in
# exactly one place.
#
# ``Output`` is NOT set here: the baker injects it, so a tape can never
# write outside the bake directory.
#
# ``@@name@@`` placeholders are rendered from app_settings (demo_clip_*
# keys) before this reaches vhs. The delimiter is deliberately not ``{}``
# or ``$`` — both collide with the theme's JSON braces and the shell's
# ``$`` respectively, and a substitution bug here would be silent.
# Edit the settings, not this file, unless changing the preamble's shape.

Set Shell bash
Set FontFamily "@@font_family@@"
Set FontSize @@font_size@@
Set Width @@width@@
Set Height @@height@@
Set Padding @@padding@@
Set Framerate @@framerate@@
Set TypingSpeed @@typing_speed@@
Set CursorBlink true
Set Theme { "background": "@@bg@@", "foreground": "@@fg@@", "cursor": "@@accent@@", "black": "@@bg@@", "red": "@@failure@@", "green": "@@active@@", "yellow": "@@active@@", "blue": "@@accent@@", "magenta": "@@attention@@", "cyan": "@@accent@@", "white": "@@fg@@", "brightBlack": "@@dim@@", "brightRed": "@@failure@@", "brightGreen": "@@active@@", "brightYellow": "@@active@@", "brightBlue": "@@accent_bright@@", "brightMagenta": "@@attention@@", "brightCyan": "@@accent_bright@@", "brightWhite": "#FFFFFF" }

# Every tape starts from a clean, prompt-normalised shell. Hidden so the
# setup never lands on camera. ``poindexter`` is aliased because the worker
# image installs dependencies but not the project itself, so the console
# script does not exist there — the on-camera command stays the documented
# one either way.
Hide
Type "cd /app && export PS1='$ ' && alias poindexter='python -m poindexter' && clear"
Enter
Show
