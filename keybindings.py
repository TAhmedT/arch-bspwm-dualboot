"""Migrate BSPWM shortcuts to Alt without replacing other desktop settings."""
from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import sys

from arch_setup import HERE, run, write

LAYOUT_OPTION = 'grp:toggle'
LAYOUT_COMMAND = "setxkbmap -layout us,ru -option '' -option " + LAYOUT_OPTION


def alt_hotkeys(text):
    # The upstream commands toggle layouts on modifier key presses; remove them
    # now that XKB handles Right Alt, otherwise Alt+Shift shortcuts still switch.
    text = re.sub(r'(?m)^(?:Alt_L[ \t]*\+[ \t]*shift|Shift_L[ \t]*\+[ \t]*alt)[ \t]*\n'
                  r'(?:[ \t]+[^\n]*(?:\n|$))+', '', text)
    result = []
    for line in text.splitlines(keepends=True):
        if line and not line[0].isspace() and not line.startswith('#'):
            if re.search(r'\b(super|mod4)\b', line):
                # Preserve the extra modifier in Win+Alt directions without
                # colliding with Alt+Shift+L (lock) or Alt+Ctrl+H/J/K/L (resize).
                line = re.sub(r'\balt\b', 'ctrl + shift', line)
                line = re.sub(r'\b(super|mod4)\b', 'alt', line)
                # Keep the existing Alt+Tab window cycle distinct from Win+Tab's
                # previous-desktop action in the upstream paired binding.
                line = line.replace('{grave,Tab}', '{grave,ctrl + Tab}')
                # Upstream's keyboard resize binding is missing this plus.
                line = re.sub(r'\b(control|ctrl)[ \t]+(?=\{)', r'\1 + ', line)
                # A hotkey has one keysym: Escape cannot also be a modifier.
                line = re.sub(r'^alt[ \t]*\+[ \t]*Escape[ \t]*\+[ \t]*r(?=[ \t]*$)',
                              'alt + Escape', line.rstrip('\n')) + ('\n' if line.endswith('\n') else '')
        result.append(line)
    return ''.join(result)


def alt_bspwm(text):
    text = re.sub(r'(?ms)^# BEGIN arch-dualboot keyboard\n.*?^# END arch-dualboot keyboard\n', '', text)
    text, count = re.subn(r'(?m)^([ \t]*bspc[ \t]+config[ \t]+pointer_modifier[ \t]+)\S+',
                         r'\g<1>mod1', text)
    if not count:
        text = text.rstrip() + '\nbspc config pointer_modifier mod1\n'
    block = '# BEGIN arch-dualboot keyboard\n' + LAYOUT_COMMAND + '\n# END arch-dualboot keyboard\n'
    if text.startswith('#!') and '\n' in text:
        first, rest = text.split('\n', 1)
        return first + '\n' + block + rest
    return block + text


def configure_user(home, *, backup=True):
    home = Path(home)
    keys = home / '.config/sxhkd/sxhkdrc'
    bspwm = home / '.config/bspwm/bspwmrc'
    paths = [keys, bspwm]
    xinit = home / '.xinitrc'
    if xinit.exists() or xinit.is_symlink():
        paths.append(xinit)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f'Нужен обычный файл настроек: {path}')
    updates = {keys: alt_hotkeys(keys.read_text()), bspwm: alt_bspwm(bspwm.read_text())}
    if xinit in paths:
        updates[xinit] = re.sub(r'(?m)^setxkbmap[^\n]*grp:alt_shift_toggle[^\n]*$',
                               LAYOUT_COMMAND, xinit.read_text())
    changed = {path: content for path, content in updates.items() if path.read_text() != content}
    if backup and changed:
        saved = home / 'dotfiles-backups' / ('hotkeys-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        for path in changed:
            target = saved / path.relative_to(home)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        print('Резервная копия:', saved)
    for path, content in changed.items():
        write(path, content, path.stat().st_mode & 0o777)


def setup():
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('Запускайте hotkeys из своего Linux-пользователя, БЕЗ sudo.')
    configure_user(Path.home())
    if Path('/usr/local/bin/arch-bspwm-session').is_file():
        # Update the greeter's layout too, without reinstalling packages or
        # restarting LightDM/the active desktop.
        run(['sudo', 'python3', HERE / 'graphical_login.py', '--keyboard'])
    if os.environ.get('DISPLAY'):
        run(['setxkbmap', '-layout', 'us,ru', '-option', '', '-option', LAYOUT_OPTION])
        run(['bspc', 'config', 'pointer_modifier', 'mod1'])
        run(['pkill', '-USR1', '-u', str(os.geteuid()), '-x', 'sxhkd'], check=False)
    print('Бинды: Alt. Раскладка US/RU: правый Alt. В TTY изменения применятся при следующем входе в BSPWM.')
