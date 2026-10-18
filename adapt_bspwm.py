#!/usr/bin/env python3
"""Usage (Arch Linux, without sudo): python3 adapt-bspwm.py PATH_TO_REPOSITORY"""
from datetime import datetime
from pathlib import Path
import argparse
import os
import re
import shutil
import sys

CONFIGS = ('alacritty', 'bspwm', 'polybar', 'rofi', 'sxhkd', 'fish',
           'flameshot', 'gtk-2.0', 'gtk-3.0', 'gtk-4.0', 'lsd', 'zathura')


def write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
    if executable:
        path.chmod(0o755)


def install(repo, home):
    copies = [(repo / 'config' / n, home / '.config' / n) for n in CONFIGS]
    copies += [(repo / s, home / d) for s, d in (
        ('bin', 'bin'), ('Images', 'Images'), ('themes', '.themes'),
        ('local/share/icons', '.local/share/icons'),
        ('Xresources', '.Xresources'), ('gtkrc-2.0', '.gtkrc-2.0'))]
    extra = [home / '.xinitrc']
    # Validate every target before copying anything.
    for source, target in copies:
        if not source.exists():
            raise RuntimeError(f'Missing repository file: {source}')
    for target in [t for _, t in copies] + extra:
        if target.is_symlink():
            raise RuntimeError(f'Refusing to overwrite a symlink: {target}')
    backup = home / 'dotfiles-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    for target in [t for _, t in copies] + extra:
        if not target.exists():
            continue
        saved = backup / target.relative_to(home)
        saved.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            shutil.copytree(target, saved, symlinks=True)
        else:
            shutil.copy2(target, saved)
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(source, target)
    roots = [home / '.config' / n for n in CONFIGS] + [home / 'bin', home / '.gtkrc-2.0']
    for root in roots:
        for path in (root.rglob('*') if root.is_dir() else [root]):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            if '\x00' not in text and '/home/user' in text:
                write(path, text.replace('/home/user', str(home)))

    write(home / '.config/bspwm/dual_monitors.sh', '''#!/usr/bin/env bash
set -eu
mapfile -t monitors < <(xrandr --query | awk '/ connected/{print $1}')
if ((${#monitors[@]} == 0)); then exit 0; fi
primary=$(xrandr --query | awk '/ connected primary/{print $1; exit}')
primary=${primary:-${monitors[0]}}
xrandr --output "$primary" --primary --auto
bspc monitor "$primary" -d 1 2 3 4 5 6 7 8 9
for monitor in "${monitors[@]}"; do
    [[ "$monitor" == "$primary" ]] && continue
    xrandr --output "$monitor" --auto --right-of "$primary"
    bspc monitor "$monitor" -d "${monitor}-1" "${monitor}-2"
done
''', True)
    bspwm = home / '.config/bspwm/bspwmrc'
    text = bspwm.read_text(encoding='utf-8').replace('xrdb merge', 'xrdb -merge')
    disabled = ('battery-alert', 'cursor_tracker', 'greenclip', 'pgrep -x redshift', 'picom --config')
    text = '\n'.join('# Disabled for desktop: ' + line if any(t in line for t in disabled) else line for line in text.splitlines()) + '\n'
    text += '''pgrep -x picom >/dev/null || picom --config "$HOME/.config/bspwm/picom-desktop.conf" &
pgrep -x nm-applet >/dev/null || nm-applet &
pgrep -x clipmenud >/dev/null || clipmenud &
pgrep -f '^/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1$' >/dev/null || /usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1 &
'''
    write(bspwm, text, True)
    write(home / '.config/bspwm/picom-desktop.conf', '''backend = "glx";
vsync = true;
shadow = true;
shadow-radius = 15;
shadow-opacity = 0.5;
corner-radius = 10;
fading = true;
fade-in-step = 0.08;
fade-out-step = 0.08;
shadow-exclude = ["window_type = 'dock'", "window_type = 'desktop'"];
''')
    bar = home / '.config/polybar/config.ini'
    text = bar.read_text(encoding='utf-8')
    text = re.sub(r'(?m)^modules-center\s*=.*$', 'modules-center = memory space space cpu', text)
    text = re.sub(r'(?m)^modules-right\s*=.*$', 'modules-right = pulseaudio xkeyboard round-left time round-right space ethernet wlan powermenu', text)
    text = re.sub(r'(?m)^font-2\s*=.*$', 'font-2 = Symbols Nerd Font:size=13;3', text)
    write(bar, text)
    modules = home / '.config/polybar/modules.ini'
    write(modules, modules.read_text(encoding='utf-8') + '''
[module/pulseaudio]
type = internal/pulseaudio
format-volume = <label-volume>
label-volume = VOL %percentage%%
label-muted = MUTED
click-right = pavucontrol

[module/ethernet]
type = internal/network
interface = ${env:ETH_IFACE:eth0}
interface-type = wired
label-connected = LAN %local_ip%
label-disconnected =
''')
    launch = home / '.config/polybar/launch.sh'
    text = launch.read_text(encoding='utf-8')
    text = text.replace('# Run on the desired monitor', "export ETH_IFACE=$(ip -o route show default | awk '{print $5; exit}')\n# Run on the desired monitor")
    write(launch, text, True)
    # Basic lock uses the official i3lock package, with no AUR dependency.
    write(home / 'bin/screen-lock', '''#!/usr/bin/env bash
set -eu
pkill -STOP -x sxhkd || true
trap 'pkill -CONT -x sxhkd || true' EXIT
i3lock -n -c 1e1e2e
''', True)
    write(home / 'bin/powermenu', '''#!/usr/bin/env bash
choice=$(printf 'Lock\\nLogout\\nSuspend\\nReboot\\nShutdown\\n' | rofi -dmenu -p Power)
case "$choice" in
  Lock) "$HOME/bin/screen-lock" ;;
  Logout) bspc quit ;;
  Suspend) i3lock -c 1e1e2e && systemctl suspend ;;
  Reboot) systemctl reboot ;;
  Shutdown) systemctl poweroff ;;
esac
''', True)
    write(home / 'bin/clipboard-menu', '#!/usr/bin/env bash\nexport CM_LAUNCHER=rofi\nexec clipmenu\n', True)
    write(home / 'bin/wifimenu', '#!/usr/bin/env bash\nexec alacritty -e nmtui\n', True)
    write(home / '.xinitrc', '''#!/bin/sh
if [ -d /etc/X11/xinit/xinitrc.d ]; then
    for f in /etc/X11/xinit/xinitrc.d/?*.sh; do
        [ -r "$f" ] && . "$f"
    done
    unset f
fi
export PATH="$HOME/bin:$PATH"
setxkbmap -layout us,ru -option grp:alt_shift_toggle
exec bspwm
''', True)
    for path in (home / 'bin').rglob('*'):
        if path.is_file() and not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o100)
    print(f'Done. Backups: {backup}')
    print('Run startx from a local tty as your normal user.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run this script in the installed Arch Linux system.')
    if os.geteuid() == 0:
        parser.error('Run as your normal user, without sudo.')
    install(args.repository.expanduser().resolve(), Path.home())


if __name__ == '__main__':
    main()
