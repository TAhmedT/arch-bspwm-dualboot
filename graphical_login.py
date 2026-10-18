"""LightDM login and light-locker integration for an existing BSPWM desktop."""
from datetime import datetime
from pathlib import Path
import argparse
import os
import re
import shutil
import sys

from arch_setup import HERE, load_state, root_required, run, write
from keybindings import LAYOUT_COMMAND, alt_hotkeys, configure_user as configure_hotkeys

PACKAGES = ['lightdm', 'lightdm-gtk-greeter', 'light-locker', 'adw-gtk-theme',
            'accountsservice', 'librsvg']

LIGHTDM = '''[LightDM]

[Seat:*]
greeter-session=lightdm-gtk-greeter
user-session=arch-bspwm
session-wrapper=/etc/lightdm/Xsession
greeter-setup-script=/usr/local/libexec/arch-greeter-layout
greeter-hide-users=false
greeter-show-manual-login=true
greeter-allow-guest=false
allow-guest=false
autologin-user=
'''

GREETER = '''[greeter]
background=/usr/share/backgrounds/arch-dualboot/login.svg
user-background=false
theme-name=adw-gtk3-dark
icon-theme-name=Papirus-Dark
font-name=Noto Sans 12
default-user-image=/usr/share/pixmaps/arch-dualboot-user.svg
hide-user-image=false
round-user-image=true
position=50% 50%
panel-position=top
indicators=~spacer;~clock;~spacer;~layout;~session;~power
keyboard-layouts=us;ru
clock-format=%a, %d %b  %H:%M
screensaver-timeout=0
'''

LAYOUT = '#!/bin/sh\nexec /usr/bin/' + LAYOUT_COMMAND + '\n'

SESSION = '''#!/bin/sh
if [ -d /etc/X11/xinit/xinitrc.d ]; then
    for file in /etc/X11/xinit/xinitrc.d/?*.sh; do
        [ -r "$file" ] && . "$file"
    done
    unset file
fi
export PATH="$HOME/bin:$PATH"
export XDG_CURRENT_DESKTOP=bspwm
export XDG_SESSION_DESKTOP=bspwm
export XDG_SESSION_TYPE=x11
''' + LAYOUT_COMMAND + '''
dbus-update-activation-environment --systemd DISPLAY XAUTHORITY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE
if [ -n "${XDG_SESSION_PATH:-}" ]; then
    light-locker --lock-on-suspend --no-late-locking &
fi
exec bspwm
'''

DESKTOP = '''[Desktop Entry]
Name=Arch BSPWM
Comment=BSPWM with graphical login and screen locking
Exec=/usr/local/bin/arch-bspwm-session
TryExec=bspwm
Type=Application
DesktopNames=bspwm;
'''

SCREEN_LOCK = '''#!/bin/sh
set -eu
if [ -z "${XDG_SESSION_PATH:-}" ] || ! pgrep -u "$(id -u)" -x light-locker >/dev/null; then
    message='Войдите через графический экран LightDM после перезагрузки, затем повторите блокировку.'
    notify-send 'Блокировка экрана' "$message" || true
    printf '%s\\n' "$message" >&2
    exit 1
fi
exec light-locker-command --lock
'''

POWER_MENU = '''#!/usr/bin/env bash
choice=$(printf 'Lock\\nLogout\\nSuspend\\nReboot\\nShutdown\\n' | rofi -dmenu -p Power)
case "$choice" in
  Lock) "$HOME/bin/screen-lock" ;;
  Logout) bspc quit ;;
  Suspend) "$HOME/bin/screen-lock" && systemctl suspend ;;
  Reboot) systemctl reboot ;;
  Shutdown) systemctl poweroff ;;
esac
'''


def configure_user(home, *, backup=True):
    home = Path(home)
    keys = home / '.config/sxhkd/sxhkdrc'
    _validate_destinations((keys, home / 'bin/screen-lock', home / 'bin/powermenu'))
    text = keys.read_text() if keys.exists() else ''
    text = alt_hotkeys(text)
    # Preserve the upstream brace-expanded binding and all other shortcuts.
    text = text.replace('sh $HOME/bin/screen-lock', '"$HOME/bin/screen-lock"')
    text = re.sub(r'(?m)^alt\s*\+\s*shift\s*\+\s*l[ \t]*\n(?:[ \t]+[^\n]*(?:\n|$))+', '', text)
    if 'bin/screen-lock' not in text:
        text = text.rstrip() + '\n\nalt + shift + l\n    "$HOME/bin/screen-lock"\n'
    updates = {home / 'bin/screen-lock': (SCREEN_LOCK, 0o755),
               home / 'bin/powermenu': (POWER_MENU, 0o755), keys: (text, 0o644)}
    _install_files(updates, home / 'dotfiles-backups', home, backup=backup)


def _validate_destinations(paths):
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f'Не перезаписываю ссылку или каталог: {path}')


def _install_files(updates, backups, relative_to, *, backup=True):
    # Validate all destinations before modifying any of them.
    _validate_destinations(updates)
    changed = {path: value for path, value in updates.items()
               if not path.exists() or path.read_text() != value[0]
               or path.stat().st_mode & 0o777 != value[1]}
    if backup and any(path.exists() for path in changed):
        directory = backups / ('gui-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        for path in changed:
            if path.exists():
                saved = directory / path.relative_to(relative_to)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
        print('Резервная копия:', directory)
    for path, (content, mode) in changed.items():
        write(path, content, mode)


def install_system_files(root):
    root = Path(root)
    updates = {root / path: (text, mode) for path, text, mode in (
        ('etc/lightdm/lightdm.conf', LIGHTDM, 0o644),
        ('etc/lightdm/lightdm-gtk-greeter.conf', GREETER, 0o644),
        ('usr/local/libexec/arch-greeter-layout', LAYOUT, 0o755),
        ('usr/local/bin/arch-bspwm-session', SESSION, 0o755),
        ('usr/share/xsessions/arch-bspwm.desktop', DESKTOP, 0o644),
        ('usr/share/backgrounds/arch-dualboot/login.svg',
         (HERE / 'assets/login-background.svg').read_text(), 0o644),
        ('usr/share/pixmaps/arch-dualboot-user.svg',
         (HERE / 'assets/login-avatar.svg').read_text(), 0o644),
    )}
    _install_files(updates, root / 'var/backups/arch-dualboot', root)


def configure_system():
    root_required()
    load_state()
    if Path('/run/archiso').is_dir():
        raise RuntimeError('Графический вход настраивается в установленной системе.')
    manager = Path('/etc/systemd/system/display-manager.service')
    if manager.is_symlink() and manager.resolve().name != 'lightdm.service':
        raise RuntimeError('Уже включён другой менеджер входа: ' + str(manager.resolve()))
    if not Path('/etc/lightdm/Xsession').is_file():
        raise RuntimeError('Не найден /etc/lightdm/Xsession; сначала установите пакет lightdm.')
    install_system_files(Path('/'))
    run(['lightdm', '--show-config'])
    # Enable for the next boot. Starting/restarting it now could close the desktop.
    run(['systemctl', 'enable', 'lightdm.service'])
    run(['systemctl', 'set-default', 'graphical.target'])


def configure_system_keyboard():
    root_required()
    load_state()
    if Path('/run/archiso').is_dir():
        raise RuntimeError('Раскладка настраивается в установленной системе.')
    paths = {'usr/local/libexec/arch-greeter-layout': LAYOUT,
             'usr/local/bin/arch-bspwm-session': SESSION}
    if not all(Path('/' + path).is_file() for path in paths):
        raise RuntimeError('Сначала выполните gui для настройки LightDM.')
    updates = {Path('/' + path): (text, 0o755) for path, text in paths.items()}
    _install_files(updates, Path('/var/backups/arch-dualboot'), Path('/'))


def setup(*, install_packages=True):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('Запускайте gui из своего Linux-пользователя, БЕЗ sudo.')
    if not Path('/etc/arch-release').is_file() or Path('/run/archiso').is_dir():
        raise RuntimeError('Этот этап выполняется в установленном Arch.')
    if not (Path.home() / '.config/bspwm/bspwmrc').is_file():
        raise RuntimeError('Сначала выполните desktop для настройки BSPWM.')
    _validate_destinations(Path.home() / name for name in (
        'bin/screen-lock', 'bin/powermenu', '.config/sxhkd/sxhkdrc'))
    if install_packages:
        run(['sudo', 'pacman', '-Syu', '--needed', *PACKAGES])
    run(['sudo', 'python3', HERE / 'graphical_login.py', '--system'])
    configure_user(Path.home())
    configure_hotkeys(Path.home())
    print('Графический вход готов. При следующей загрузке выберите сеанс Arch BSPWM.')
    print('Alt+Shift+L откроет экран входа с паролем после входа через LightDM. Раскладка: правый Alt.')
    print('Сохраните работу и перезагрузитесь, когда будет удобно.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--system', action='store_true')
    group.add_argument('--keyboard', action='store_true')
    args = parser.parse_args()
    if args.system:
        configure_system()
    elif args.keyboard:
        configure_system_keyboard()
    else:
        setup()
