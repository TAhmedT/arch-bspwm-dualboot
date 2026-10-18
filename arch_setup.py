#!/usr/bin/env python3
"""Interactive Arch installer for one explicitly confirmed ADATA desktop disk."""
from pathlib import Path
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
STATE = Path('/etc/arch-dualboot.json')
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
MODEL = 'ADATA LEGEND 900'
WINDOWS_MODEL = 'Samsung SSD 980 1TB'
DOTFILES_COMMIT = 'f1ca6599546ee5b912d0816f6b25e60ef7e6aa1a'
THEME_URL = 'https://github.com/catppuccin/refind.git'
BASE_PACKAGES = '''base linux-lts linux-lts-headers linux-firmware amd-ucode base-devel networkmanager
wpa_supplicant sudo nano micro git python refind efibootmgr dosfstools e2fsprogs
man-db man-pages openssl sbsigntools mokutil systemd-ukify gnupg zstd
dkms nvidia-open-dkms nvidia-utils'''.split()
ZFS_PACKAGES = ['zfs-dkms', 'zfs-utils']
DESKTOP_PACKAGES = '''xorg-server xorg-xinit xorg-xrandr xorg-xset xorg-xsetroot
xorg-xrdb xorg-setxkbmap xterm bspwm sxhkd alacritty fish polybar rofi dunst picom
feh thunar gvfs gvfs-mtp tumbler ffmpegthumbnailer thunar-archive-plugin xarchiver
unzip 7zip pipewire pipewire-audio pipewire-alsa pipewire-pulse wireplumber
pavucontrol pamixer alsa-utils network-manager-applet polkit-gnome firefox
flameshot i3lock clipmenu xclip xdotool libnotify ttf-jetbrains-mono-nerd
ttf-nerd-fonts-symbols noto-fonts noto-fonts-emoji papirus-icon-theme
gtk-engine-murrine lxappearance bat lsd micro calcurse gpick mpv zathura
zathura-pdf-mupdf nvidia-settings'''.split()


def run(args, *, capture=False, data=None, check=True):
    print('+', ' '.join(str(arg) for arg in args), flush=True)
    result = subprocess.run([str(a) for a in args], input=data, text=True,
                            capture_output=capture, check=check)
    return result.stdout.strip() if capture else result.returncode


def write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
    path.chmod(mode)


def children(node):
    yield node
    for child in node.get('children', []):
        yield from children(child)


def mounted(node):
    return any(p for item in children(node) for p in (item.get('mountpoints') or []) if p)


def validate_target(disk):
    if disk.get('type') != 'disk' or str(disk.get('model', '')).strip() != MODEL:
        raise RuntimeError('Разрешён только целый ADATA LEGEND 900.')
    if not 900_000_000_000 <= int(disk['size']) <= 1_100_000_000_000:
        raise RuntimeError('Объём ADATA не соответствует проверенному накопителю.')
    if disk.get('rm') or disk.get('ro') or disk.get('tran') == 'usb':
        raise RuntimeError('Съёмный, USB или read-only диск не подходит.')
    if not re.fullmatch(r'/dev/(nvme\d+n\d+|sd[a-z]+)', disk.get('name', '')):
        raise RuntimeError('Неожиданный путь диска.')
    if mounted(disk):
        raise RuntimeError('На ADATA есть смонтированные разделы. Авторазмонтирование запрещено.')
    if any(item.get('partlabel') in ('ARCH_EFI', 'ARCH_ROOT', 'ARCH_ZFS') for item in children(disk)):
        raise RuntimeError('Похоже, Arch уже установлен. Повторное стирание заблокировано; используйте repair.')
    for item in children(disk):
        if item.get('fstype') == 'zfs_member':
            raise RuntimeError('На ADATA уже есть ZFS. Повторное стирание заблокировано.')
        if item.get('type') not in ('disk', 'part'):
            raise RuntimeError('Сложная разметка/RAID/LVM: требуется ручная проверка.')


def windows_esp(disks):
    matches = []
    for disk in disks:
        if disk.get('type') == 'disk' and str(disk.get('model', '')).strip() == WINDOWS_MODEL:
            for item in children(disk):
                if (item.get('parttype') or '').lower() == ESP_TYPE:
                    matches.append(item)
    if len(matches) != 1:
        raise RuntimeError('Не найден единственный EFI Windows на Samsung 980. Форматирование запрещено.')
    if not matches[0].get('partuuid'):
        raise RuntimeError('У EFI Windows нет PARTUUID.')
    return matches[0]


def inventory():
    output = run(['lsblk', '--json', '--bytes', '--paths', '-o',
                  'NAME,TYPE,SIZE,MODEL,TRAN,RM,RO,FSTYPE,PARTTYPE,PARTLABEL,UUID,PARTUUID,MOUNTPOINTS'], capture=True)
    return json.loads(output)['blockdevices']


def secureboot_enabled():
    paths = list(Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'))
    if len(paths) != 1:
        raise RuntimeError('Не удалось определить состояние Secure Boot.')
    data = paths[0].read_bytes()
    if len(data) < 5:
        raise RuntimeError('Некорректная UEFI-переменная SecureBoot.')
    return bool(data[4])


def root_required():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Этот этап запускается только в Linux от root (sudo).')


def inspect():
    root_required()
    run(['lsblk', '-o', 'NAME,PATH,SIZE,MODEL,FSTYPE,PARTLABEL,MOUNTPOINTS'])
    print('Secure Boot:', 'ON' if secureboot_enabled() else 'OFF')
    run(['efibootmgr', '-v'], check=False)
    print('Режим inspect ничего не меняет.')


def username_ok(value):
    return bool(re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', value)) and value not in ('root', 'nobody')


def select_install(disks, input_fn=input):
    candidates = [d for d in disks if str(d.get('model', '')).strip() == MODEL]
    if len(candidates) != 1:
        raise RuntimeError('Нужен ровно один ADATA LEGEND 900. Не выбираю диск по номеру.')
    target = candidates[0]
    validate_target(target)
    win = windows_esp(disks)
    if win['name'] in {n['name'] for n in children(target)}:
        raise RuntimeError('EFI Windows находится на целевом диске: остановка.')
    print('\nБУДЕТ ПОЛНОСТЬЮ СТЁРТ:', target['name'], MODEL, round(target['size'] / 2**30, 1), 'GiB')
    print('Windows EFI (только чтение):', win['name'])
    if input_fn('Введите полный путь ADATA ещё раз: ').strip() != target['name']:
        raise RuntimeError('Подтверждение пути не совпало.')
    if input_fn('Введите ERASE ADATA LEGEND 900: ').strip() != 'ERASE ADATA LEGEND 900':
        raise RuntimeError('Стирание не подтверждено.')
    return target, win


@contextlib.contextmanager
def readonly_windows(partition):
    with tempfile.TemporaryDirectory(prefix='windows-esp-') as temp:
        run(['mount', '-o', 'ro,nosuid,nodev,noexec', partition, temp])
        try:
            loader = Path(temp) / 'EFI/Microsoft/Boot/bootmgfw.efi'
            if not loader.is_file():
                raise RuntimeError('На выбранном EFI нет Windows Boot Manager.')
            yield loader
        finally:
            run(['umount', temp])


def confirm_windows(win):
    # Do not attempt to mount an already-mounted Windows partition.
    if any(win.get('mountpoints') or []):
        raise RuntimeError('EFI Windows уже смонтирован; сначала проверьте и размонтируйте его вручную.')
    with readonly_windows(win['name']):
        pass


def template_refind(win_guid):
    uuid.UUID(win_guid)
    return f'''timeout 8
default_selection +
resolution max
scanfor manual
showtools about,mok_tool,reboot,shutdown,firmware

menuentry "Arch Linux" {{
    icon /EFI/refind/icons/os_arch.png
    loader /EFI/Linux/arch-linux.efi
}}

menuentry "Arch Linux - previous signed image" {{
    icon /EFI/refind/icons/os_arch.png
    loader /EFI/Linux/arch-linux-previous.efi
}}

menuentry "Windows" {{
    icon /EFI/refind/icons/os_win.png
    volume {win_guid}
    loader /EFI/Microsoft/Boot/bootmgfw.efi
}}

include themes/catppuccin/mocha.conf
showtools about,mok_tool,reboot,shutdown,firmware
'''


UPDATER = r'''#!/usr/bin/env bash
set -euo pipefail
umask 077
exec 9>/run/arch-secureboot-update.lock
flock 9
mountpoint -q /boot || { echo '/boot is not mounted'; exit 1; }
expected=$(cat /etc/arch-dualboot-esp-uuid)
actual=$(findmnt -n -o UUID --target /boot)
[[ "$actual" == "$expected" ]] || { echo 'Wrong EFI partition mounted at /boot'; exit 1; }
key=/etc/secureboot/MOK.key
cert=/etc/secureboot/MOK.pem
test -s "$key" && test -s "$cert"
test -s /boot/vmlinuz-linux-lts && test -s /boot/initramfs-linux-lts.img
# Fail before replacing the boot image if DKMS or initramfs creation failed.
mapfile -t kernels < <(for p in /usr/lib/modules/*/pkgbase; do
  [[ "$(cat "$p")" == linux-lts ]] && basename "$(dirname "$p")"
done)
[[ ${#kernels[@]} == 1 ]] || { echo 'Expected exactly one linux-lts module tree'; exit 1; }
kver=${kernels[0]}
modinfo -k "$kver" zfs >/dev/null
modinfo -k "$kver" nvidia >/dev/null
lsinitcpio /boot/initramfs-linux-lts.img | grep -E "usr/lib/modules/$kver/.*/zfs.ko(\\.|$)" >/dev/null
lsinitcpio /boot/initramfs-linux-lts.img | grep -E '(^|/)etc/hostid$' >/dev/null
mkdir -p /boot/EFI/Linux /boot/EFI/refind
tmp_uki=/boot/EFI/Linux/.arch-linux.new
tmp_refind=/boot/EFI/refind/.refind.new
trap 'rm -f -- "$tmp_uki" "$tmp_refind"' EXIT
ukify build --linux=/boot/vmlinuz-linux-lts --initrd=/boot/initramfs-linux-lts.img \
  --cmdline=@/etc/kernel/cmdline --os-release=@/etc/os-release \
  --secureboot-private-key="$key" --secureboot-certificate="$cert" \
  --sign-kernel --output="$tmp_uki"
sbverify --cert "$cert" "$tmp_uki"
objdump -h "$tmp_uki" | grep -q '\.sbat'
objdump -h /usr/share/refind/refind_x64.efi | grep -q '\.sbat'
sbsign --key "$key" --cert "$cert" --output "$tmp_refind" /usr/share/refind/refind_x64.efi
sbverify --cert "$cert" "$tmp_refind"
if [[ -s /boot/EFI/Linux/arch-linux.efi ]]; then
  sbverify --cert "$cert" /boot/EFI/Linux/arch-linux.efi
  cp /boot/EFI/Linux/arch-linux.efi /boot/EFI/Linux/arch-linux-previous.efi
else
  cp "$tmp_uki" /boot/EFI/Linux/arch-linux-previous.efi
fi
mv -f -- "$tmp_uki" /boot/EFI/Linux/arch-linux.efi
# shim expects grubx64.efi; this is signed rEFInd, not GRUB.
mv -f -- "$tmp_refind" /boot/EFI/refind/grubx64.efi
sync
echo 'Signed UKI and rEFInd updated successfully.'
'''


def install_boot_assets(root, state, shim_dir):
    boot = root / 'boot'
    refind = boot / 'EFI/refind'
    refind.mkdir(parents=True, exist_ok=True)
    for filename in ('shimx64.efi', 'mmx64.efi'):
        shutil.copy2(shim_dir / filename, refind / filename)
    shutil.copy2(shim_dir / 'shim-source.json', root / 'etc/shim-source.json')
    shutil.copytree(root / 'usr/share/refind/icons', refind / 'icons', dirs_exist_ok=True)
    theme = refind / 'themes/catppuccin'
    if not theme.exists():
        cached_theme = shim_dir.parent / 'theme'
        if not cached_theme.is_dir():
            raise RuntimeError('Тема не подготовлена до разметки.')
        shutil.copytree(cached_theme, theme, ignore=shutil.ignore_patterns('.git'))
    write(refind / 'refind.conf', template_refind(state['windows_guid']))
    write(root / 'etc/arch-dualboot-esp-uuid', state['esp_uuid'] + '\n')
    from zfs_support import ROOT
    write(root / 'etc/kernel/cmdline', f'root=ZFS={ROOT} rw zfs_import_dir=/dev/disk/by-partuuid zfs_boot_only=1\n')
    write(root / 'usr/local/sbin/arch-secureboot-update', UPDATER, 0o700)
    write(root / 'usr/local/sbin/arch-esp-check', '''#!/usr/bin/env bash
set -euo pipefail
mountpoint -q /boot
[[ "$(findmnt -n -o UUID --target /boot)" == "$(cat /etc/arch-dualboot-esp-uuid)" ]] || { echo 'Wrong or unmounted /boot; upgrade stopped'; exit 1; }
''', 0o700)
    write(root / 'usr/local/sbin/arch-secureboot-rebuild', '''#!/usr/bin/env bash
set -euo pipefail
/usr/local/sbin/arch-esp-check
for p in /usr/lib/modules/*/pkgbase; do
  [[ "$(cat "$p")" == linux-lts ]] || continue
  dkms autoinstall -k "$(basename "$(dirname "$p")")"
done
mkinitcpio -P
''', 0o700)
    write(root / 'etc/pacman.d/hooks/05-arch-esp-check.hook', '''[Trigger]
Operation = Install
Operation = Upgrade
Type = Package
Target = linux-lts
Target = linux-lts-headers
Target = zfs-dkms
Target = zfs-utils
Target = nvidia-open-dkms
Target = nvidia-utils
Target = refind
Target = systemd
Target = systemd-ukify

[Action]
Description = Checking Arch EFI partition before boot-related updates
When = PreTransaction
Exec = /usr/local/sbin/arch-esp-check
AbortOnFail
''')
    write(root / 'etc/initcpio/post/95-arch-secureboot', '''#!/usr/bin/env bash
set -euo pipefail
[[ "${2:-}" == /boot/initramfs-linux-lts.img ]] || exit 0
exec /usr/local/sbin/arch-secureboot-update
''', 0o755)
    write(root / 'etc/pacman.d/hooks/99-arch-secureboot.hook', '''[Trigger]
Operation = Install
Operation = Upgrade
Type = Package
Target = refind
Target = systemd
Target = systemd-ukify
Target = linux-lts
Target = linux-lts-headers
Target = zfs-dkms
Target = zfs-utils
Target = nvidia-open-dkms
Target = nvidia-utils

[Action]
Description = Refreshing signed rEFInd and Arch UKI
When = PostTransaction
Exec = /usr/local/sbin/arch-secureboot-rebuild
''')


def chroot(root, args, **kwargs):
    return run(['arch-chroot', root, *args], **kwargs)


def create_keys(root):
    directory = root / 'etc/secureboot'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    if (directory / 'MOK.key').exists() or (directory / 'MOK.pem').exists():
        if not all((directory / name).is_file() for name in ('MOK.key', 'MOK.pem', 'MOK.cer')):
            raise RuntimeError('Неполный набор MOK. Ничего не перезаписываю.')
        return
    chroot(root, ['openssl', 'req', '-new', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes',
                  '-days', '3650', '-subj', '/CN=Arch local boot signing/',
                  '-addext', 'basicConstraints=critical,CA:FALSE',
                  '-addext', 'keyUsage=critical,digitalSignature',
                  '-addext', 'extendedKeyUsage=codeSigning',
                  '-keyout', '/etc/secureboot/MOK.key', '-out', '/etc/secureboot/MOK.pem'])
    (directory / 'MOK.key').chmod(0o600)
    chroot(root, ['openssl', 'x509', '-in', '/etc/secureboot/MOK.pem', '-outform', 'DER', '-out', '/etc/secureboot/MOK.cer'])


def prepare_secureboot(root, state, shim_dir):
    create_keys(root)
    install_boot_assets(root, state, shim_dir)
    certdir = root / 'boot/EFI/refind/keys'
    certdir.mkdir(exist_ok=True)
    shutil.copy2(root / 'etc/secureboot/MOK.cer', certdir / 'MOK.cer')
    from zfs_support import configure
    configure(root)
    chroot(root, ['mkinitcpio', '-P'])
    chroot(root, ['/usr/local/sbin/arch-secureboot-update'])
    chroot(root, ['sbverify', '--list', '/boot/EFI/refind/shimx64.efi'])
    chroot(root, ['sbverify', '--list', '/boot/EFI/refind/mmx64.efi'])
    # Never alter PK/KEK/db, TPM, or existing Windows Boot Manager entries.
    chroot(root, ['efibootmgr', '--create', '--disk', state['disk'], '--part', '1',
                  '--label', 'Arch rEFInd (shim)', '--loader', r'\EFI\refind\shimx64.efi'])
    chroot(root, ['openssl', 'x509', '-in', '/etc/secureboot/MOK.pem', '-noout', '-fingerprint', '-sha256'])
    print('\nСейчас mokutil попросит ВРЕМЕННЫЙ пароль подтверждения MOK. Запомните его.')
    chroot(root, ['mokutil', '--import', '/etc/secureboot/MOK.cer'])


def install():
    root_required()
    if not Path('/run/archiso').is_dir() or not Path('/sys/firmware/efi/efivars').is_dir():
        raise RuntimeError('Установка разрешена только из официальной Arch Live USB в UEFI.')
    if secureboot_enabled():
        raise RuntimeError('Для этого пути установки временно отключите Secure Boot, сохранив заводские keys.')
    from zfs_support import check_live, create_pool, trust, fstab, POOL, ROOT
    check_live()  # Must succeed BEFORE any partitioning or destructive confirmation.
    mountpoint = Path('/mnt/arch-dualboot')
    mountpoint.mkdir(parents=True, exist_ok=True)
    if run(['mountpoint', '-q', mountpoint], check=False) == 0 or any(mountpoint.iterdir()):
        raise RuntimeError('Точка /mnt/arch-dualboot занята. Разметка запрещена.')
    for cmd in ('pacstrap', 'arch-chroot', 'parted', 'mkfs.fat', 'gpgv', 'git', 'zpool', 'zfs'):
        if not shutil.which(cmd):
            raise RuntimeError(f'Нет команды {cmd}. Нужен полный официальный Arch ISO.')
    user = input('Имя Linux-пользователя [ahmed]: ').strip() or 'ahmed'
    if not username_ok(user):
        raise RuntimeError('Недопустимое имя пользователя.')
    zone = input('Часовой пояс [Europe/Moscow]: ').strip() or 'Europe/Moscow'
    if not re.fullmatch(r'[A-Za-z0-9_+\-/]+', zone) or '..' in zone.split('/'):
        raise RuntimeError('Некорректный часовой пояс.')
    run(['timedatectl', 'set-ntp', 'true'])
    run(['ping', '-c', '2', '-W', '5', 'archlinux.org'])
    run(['pacman', '-Si', *BASE_PACKAGES, *ZFS_PACKAGES], capture=True)
    print('До разметки проверяю подписанные метаданные и скачиваю shim.')
    from ubuntu_shim import download_verified
    with tempfile.TemporaryDirectory(prefix='arch-install-') as scratch:
        shim_dir = download_verified(Path(scratch) / 'shim')
        run(['git', 'clone', '--depth', '1', THEME_URL, Path(scratch) / 'theme'])
        disks = inventory()
        run(['lsblk', '-o', 'NAME,PATH,SIZE,MODEL,FSTYPE,MOUNTPOINTS'])
        # Validate Windows loader before the destructive confirmation.
        win = windows_esp(disks)
        confirm_windows(win)
        target, win = select_install(disks)
        # Re-read immediately before erasing; reject mount/topology changes.
        fresh = inventory()
        current = next((d for d in fresh if d['name'] == target['name']), None)
        if current != target or windows_esp(fresh) != win:
            raise RuntimeError('Состояние дисков изменилось; начинайте проверку заново.')
        validate_target(current)
        disk = target['name']
        run(['parted', disk, '--script', 'mklabel', 'gpt'])
        run(['parted', disk, '--script', 'mkpart', 'ARCH_EFI', 'fat32', '1MiB', '2049MiB'])
        run(['parted', disk, '--script', 'set', '1', 'esp', 'on'])
        run(['parted', disk, '--script', 'mkpart', 'ARCH_ZFS', '2049MiB', '100%'])
        run(['partprobe', disk])
        run(['udevadm', 'settle'])
        fresh = next(d for d in inventory() if d['name'] == disk)
        parts = {n.get('partlabel'): n for n in children(fresh) if n['type'] == 'part'}
        if set(parts) != {'ARCH_EFI', 'ARCH_ZFS'}:
            raise RuntimeError('Неожиданная разметка после parted; форматирование остановлено.')
        esp, rootpart = parts['ARCH_EFI']['name'], parts['ARCH_ZFS']['name']
        run(['mkfs.fat', '-F', '32', '-n', 'ARCH_EFI', esp])
        # Only remove old signatures from the explicitly confirmed NEW ADATA partition.
        run(['wipefs', '--all', rootpart])
        create_pool(rootpart, mountpoint)
        run(['mount', '--mkdir', '-o', 'umask=0077', esp, mountpoint / 'boot'])
        print('Если последующий этап завершится ошибкой, НЕ запускайте install заново: используйте repair.')
        # pacstrap -K starts with Arch's keys only. Do not ask its pacman to
        # authenticate the ArchZFS database until the release key is installed.
        live_config = Path('/etc/pacman.conf').read_text()
        official_config = re.sub(r'(?ms)^\[archzfs\]\n.*?(?=^\[|\Z)', '', live_config)
        config_path = Path(scratch) / 'pacstrap.conf'
        write(config_path, official_config)
        run(['pacstrap', '-C', config_path, '-K', mountpoint, *BASE_PACKAGES])
        trust(mountpoint)
        chroot(mountpoint, ['pacman', '-Syu', '--needed', '--noconfirm', *ZFS_PACKAGES])
        esp_uuid = run(['blkid', '-s', 'UUID', '-o', 'value', esp], capture=True)
        write(mountpoint / 'etc/fstab', fstab(esp_uuid))
        if not (mountpoint / 'usr/share/zoneinfo' / zone).is_file():
            raise RuntimeError('Часовой пояс не найден в установленной системе.')
        state = {
            'schema': 2, 'username': user, 'timezone': zone, 'disk': disk,
            'filesystem': 'zfs', 'pool': POOL, 'root_dataset': ROOT,
            'pool_guid': run(['zpool', 'get', '-H', '-o', 'value', 'guid', POOL], capture=True),
            'esp_uuid': esp_uuid,
            'windows_guid': win['partuuid'],
        }
        write(mountpoint / 'etc/arch-dualboot.json', json.dumps(state, indent=2) + '\n', 0o600)
        configure_system(mountpoint, state)
        saved = mountpoint / 'opt/arch-bspwm-dualboot'
        shutil.copytree(HERE, saved, ignore=shutil.ignore_patterns('.git', '__pycache__'), dirs_exist_ok=True)
        prepare_secureboot(mountpoint, state, shim_dir)
        run(['zfs', 'snapshot', '-r', POOL + '@installed'])
        chroot(mountpoint, ['efibootmgr', '-v'])
        run(['sync'])
        run(['umount', '-R', mountpoint])
        run(['zpool', 'export', POOL])
    print('\nГОТОВО. Прочитайте раздел «Первый запуск» README. Перезагрузка только по вашей команде.')
    print('На синем экране MokManager подтвердите ваш сертификат. Затем включите Secure Boot и проверьте обе ОС.')


def configure_system(root, state):
    chroot(root, ['ln', '-sf', '/usr/share/zoneinfo/' + state['timezone'], '/etc/localtime'])
    chroot(root, ['hwclock', '--systohc', '--utc'])
    write(root / 'etc/hostname', 'arch-desktop\n')
    locale = root / 'etc/locale.gen'
    text = locale.read_text()
    for name in ('en_US.UTF-8 UTF-8', 'ru_RU.UTF-8 UTF-8'):
        text = re.sub(r'(?m)^#\s*' + re.escape(name) + r'\s*$', name, text)
    write(locale, text)
    chroot(root, ['locale-gen'])
    write(root / 'etc/locale.conf', 'LANG=en_US.UTF-8\n')
    chroot(root, ['useradd', '-m', '-G', 'wheel', '-s', '/bin/bash', state['username']])
    write(root / 'etc/sudoers.d/10-wheel', '%wheel ALL=(ALL:ALL) ALL\n', 0o440)
    chroot(root, ['visudo', '-c'])
    print('Задайте пароль root (ввод не отображается):')
    chroot(root, ['passwd'])
    print('Задайте пароль пользователя (ввод не отображается):')
    chroot(root, ['passwd', state['username']])
    chroot(root, ['systemctl', 'enable', 'NetworkManager', 'systemd-timesyncd'])


def load_state():
    if not STATE.is_file():
        raise RuntimeError('Не найдена /etc/arch-dualboot.json. Этот этап — только в установленном Arch.')
    state = json.loads(STATE.read_text())
    if state.get('schema') != 2 or state.get('filesystem') != 'zfs' or not username_ok(state.get('username', '')):
        raise RuntimeError('Повреждён state-файл.')
    return state


def desktop():
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('Оформление запускается из обычного пользователя, БЕЗ sudo.')
    # State is root-only; current user chooses only their own home directory.
    if not Path('/etc/arch-release').is_file() or Path('/run/archiso').is_dir():
        raise RuntimeError('Этот этап выполняется в установленном Arch.')
    run(['sudo', 'pacman', '-Syu'])
    run(['sudo', 'pacman', '-S', '--needed', *DESKTOP_PACKAGES])
    repo = Path.home() / 'bspwm-dotfiles'
    if not repo.exists():
        run(['git', 'clone', 'https://github.com/Zproger/bspwm-dotfiles.git', repo])
    remote = run(['git', '-C', repo, 'remote', 'get-url', 'origin'], capture=True)
    if remote.rstrip('/') not in ('https://github.com/Zproger/bspwm-dotfiles.git', 'https://github.com/Zproger/bspwm-dotfiles'):
        raise RuntimeError('В ~/bspwm-dotfiles другой origin; ничего не переключаю.')
    if run(['git', '-C', repo, 'status', '--porcelain'], capture=True):
        raise RuntimeError('В bspwm-dotfiles есть ваши изменения; сохраните их перед адаптацией.')
    run(['git', '-C', repo, 'checkout', '--detach', DOTFILES_COMMIT])
    run(['python3', HERE / 'adapt_bspwm.py', repo])
    run(['systemctl', '--user', 'enable', '--now', 'pipewire.socket', 'pipewire-pulse.socket', 'wireplumber.service'])
    run(['nvidia-smi'], check=False)
    print('\nОкружение готово. Из локальной консоли выполните startx. Хоткеи — в README.')


def verify():
    root_required()
    state = load_state()
    failures = []
    if not secureboot_enabled():
        failures.append('Secure Boot пока OFF; для конечной проверки включите его в UEFI.')
    for args in (
        ['mokutil', '--test-key', '/etc/secureboot/MOK.cer'],
        ['sbverify', '--cert', '/etc/secureboot/MOK.pem', '/boot/EFI/refind/grubx64.efi'],
        ['sbverify', '--cert', '/etc/secureboot/MOK.pem', '/boot/EFI/Linux/arch-linux.efi'],
    ):
        if run(args, check=False) != 0:
            failures.append('Не пройдена проверка: ' + args[0])
    actual = run(['findmnt', '-n', '-o', 'UUID', '--target', '/boot'], capture=True)
    if actual != state['esp_uuid']:
        failures.append('Неверный EFI-раздел на /boot.')
    from zfs_support import POOL, ROOT, HOME
    for target, source in (('/', ROOT), ('/home', HOME)):
        if run(['findmnt', '-n', '-o', 'SOURCE', '--target', target], capture=True) != source:
            failures.append('Неверный dataset для ' + target)
    if run(['zpool', 'get', '-H', '-o', 'value', 'guid', POOL], capture=True) != state['pool_guid']:
        failures.append('Не совпадает GUID ZFS pool.')
    if run(['zpool', 'get', '-H', '-o', 'value', 'health', POOL], capture=True) != 'ONLINE':
        failures.append('ZFS pool не ONLINE.')
    run(['zpool', 'status', '-x', POOL])
    run(['zfs', 'list', '-r', POOL])
    run(['mokutil', '--sb-state'], check=False)
    run(['timedatectl'], check=False)
    run(['nvidia-smi'], check=False)
    if failures:
        raise RuntimeError('\n'.join(failures))
    print('PASS: Secure Boot ON, MOK зарегистрирован, подписи UKI/rEFInd верны, EFI и ZFS соответствуют установке.')
    print('Проверка VALORANT выполняется отдельно реальным запуском игры в Windows.')


def enroll():
    root_required()
    load_state()
    run(['openssl', 'x509', '-in', '/etc/secureboot/MOK.pem', '-noout', '-fingerprint', '-sha256'])
    run(['mokutil', '--import', '/etc/secureboot/MOK.cer'])


def refresh_shim():
    root_required()
    state = load_state()
    if run(['findmnt', '-n', '-o', 'UUID', '--target', '/boot'], capture=True) != state['esp_uuid']:
        raise RuntimeError('Неверный EFI-раздел на /boot.')
    from ubuntu_shim import download_verified
    with tempfile.TemporaryDirectory(prefix='shim-refresh-') as temp:
        directory = download_verified(Path(temp))
        for filename in ('shimx64.efi', 'mmx64.efi'):
            dest = Path('/boot/EFI/refind') / filename
            run(['sbverify', '--list', directory / filename])
            shutil.copy2(dest, dest.with_suffix('.efi.previous'))
            shutil.copy2(directory / filename, dest.with_suffix('.efi.new'))
            os.replace(dest.with_suffix('.efi.new'), dest)
        shutil.copy2(directory / 'shim-source.json', '/etc/shim-source.json')
    print('shim/MokManager обновлены. Старые версии сохранены как .efi.previous.')


def repair():
    root_required()
    if not Path('/run/archiso').is_dir():
        raise RuntimeError('Repair запускается только из Arch Live USB. Диски не форматируются.')
    from zfs_support import check_live, mount_datasets, POOL
    check_live()
    disks = inventory()
    candidates = [d for d in disks if str(d.get('model', '')).strip() == MODEL]
    if len(candidates) != 1:
        raise RuntimeError('Нужен единственный ADATA.')
    disk = candidates[0]
    if mounted(disk):
        raise RuntimeError('ADATA уже смонтирован. Сначала проверьте текущие точки монтирования.')
    parts = {n.get('partlabel'): n for n in children(disk) if n['type'] == 'part'}
    if set(parts) != {'ARCH_EFI', 'ARCH_ZFS'}:
        raise RuntimeError('Ожидаемые разделы Arch не найдены.')
    mountpoint = Path('/mnt/arch-dualboot')
    mountpoint.mkdir(exist_ok=True, parents=True)
    if run(['mountpoint', '-q', mountpoint], check=False) == 0 or any(mountpoint.iterdir()):
        raise RuntimeError('Точка восстановления занята.')
    # Restrict discovery to the one confirmed ADATA ZFS partition, never import -a.
    run(['zpool', 'import', '-N', '-R', mountpoint, '-d', parts['ARCH_ZFS']['name'], POOL])
    mount_datasets(mountpoint)
    run(['mount', '--mkdir', '-o', 'umask=0077', parts['ARCH_EFI']['name'], mountpoint / 'boot'])
    print('Открываю root shell установленного Arch. Ничего не форматируется.')
    print('Если установка прервалась до создания /etc/arch-dualboot.json, нужен разбор ошибки; install заново запрещён.')
    try:
        chroot(mountpoint, ['/bin/bash'], check=False)
    finally:
        run(['umount', '-R', mountpoint])
        run(['zpool', 'export', POOL])


def prepare_zfs():
    root_required()
    os.execv('/bin/bash', ['bash', str(HERE / 'prepare-zfs.sh')])


def virtualization():
    root_required()
    load_state()
    from virtualization import setup_host
    setup_host()


def storage():
    from storage import menu
    menu()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = {'inspect': inspect, 'install': install, 'desktop': desktop,
               'verify': verify, 'enroll': enroll, 'refresh-shim': refresh_shim, 'repair': repair,
               'prepare-zfs': prepare_zfs, 'virtualization': virtualization, 'storage': storage}
    parser.add_argument('action', nargs='?', choices=list(actions))
    action = parser.parse_args().action
    if action is None:
        print('1 inspect — просмотр\n2 install — установка ZFS, СТИРАЕТ ADATA\n3 desktop — оформление (без sudo)\n4 verify — проверка Secure Boot + ZFS\n5 enroll — повторить запрос MOK\n6 refresh-shim — обновить shim\n7 repair — recovery shell с Live USB\n8 prepare-zfs — подготовить ZFS в Live USB, без разметки\n9 virtualization — KVM/libvirt и хранилище ВМ на ZFS\n10 storage — квоты, datasets и zvols для ВМ')
        selected = input('Номер этапа: ').strip()
        action = dict(zip(map(str, range(1, len(actions) + 1)), actions)).get(selected)
        if action is None:
            raise RuntimeError('Этап не выбран. Изменений нет.')
    actions[action]()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print('\nОСТАНОВКА:', error, file=sys.stderr)
        print('Не повторяйте форматирование. Сохраните текст ошибки; для установленного диска используйте repair.', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nПрервано пользователем. Автоматическая перезагрузка/повторная разметка не выполняются.', file=sys.stderr)
        sys.exit(130)
