"""ZFS-specific preflight and pool operations; never select a disk by number."""
from pathlib import Path
import os
import re
import sys
from arch_setup import run, write

FINGERPRINT = '3A9917BF0DED5C13F69AC68FABEC0A1208037BE9'
REPOSITORY = '''\n[archzfs]
SigLevel = Required
Server = https://github.com/archzfs/archzfs/releases/download/experimental
'''
POOL = 'archpool'
ROOT = POOL + '/ROOT/arch'
HOME = POOL + '/home'
VM = POOL + '/vm'
ISO = POOL + '/iso'
COMPATIBILITY = 'openzfs-2.2-linux'


def trust(root):
    root = Path(root)
    def command(args, **kwargs):
        return run((['arch-chroot', root] if root != Path('/') else []) + args, **kwargs)
    command(['pacman-key', '--init'])
    command(['pacman-key', '--populate', 'archlinux'])
    command(['pacman-key', '--keyserver', 'hkps://keyserver.ubuntu.com', '--recv-keys', FINGERPRINT])
    listing = command(['gpg', '--homedir', '/etc/pacman.d/gnupg', '--batch', '--with-colons',
                       '--fingerprint', FINGERPRINT], capture=True)
    fingerprints = [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')]
    if not fingerprints or fingerprints[0] != FINGERPRINT:
        raise RuntimeError('Неверный отпечаток ключа ArchZFS; доверие не добавлено.')
    command(['pacman-key', '--lsign-key', FINGERPRINT])
    config = root / 'etc/pacman.conf'
    text = config.read_text()
    if re.search(r'(?m)^\[archzfs\]', text):
        # Refuse inherited unsafe or unknown settings, rather than silently using them.
        section = re.search(r'(?ms)^\[archzfs\]\n(.*?)(?=^\[|\Z)', text).group(1)
        if section.strip() != REPOSITORY.split('[archzfs]\n', 1)[1].strip():
            raise RuntimeError('Существующая секция archzfs отличается от проверенного профиля.')
    else:
        write(config, text.rstrip() + '\n' + REPOSITORY)


def check_live():
    if not Path('/run/archiso').is_dir():
        raise RuntimeError('Нужна Arch Live USB.')
    run(['modprobe', 'zfs'])
    run(['zfs', 'version'])  # also checks that the loaded module answers userspace
    if not Path('/dev/zfs').exists():
        raise RuntimeError('ZFS module не создал /dev/zfs. Разметка запрещена.')
    if not (Path('/usr/share/zfs/compatibility.d') / COMPATIBILITY).is_file():
        raise RuntimeError('Нет ожидаемого профиля совместимости ZFS. Разметка запрещена.')
    # Existing imported pools must be explicitly dealt with by their owner.
    imported = run(['zpool', 'list', '-H', '-o', 'name'], capture=True)
    if imported:
        raise RuntimeError('В Live-среде уже импортирован ZFS pool. Автоматическая установка запрещена.')


def create_pool(partition, root):
    # Caller has already validated and confirmed the ADATA disk and fresh partition map.
    # No -f: an unexpected existing signature must stop pool creation.
    root = Path(root)
    run(['zpool', 'create', '-o', 'ashift=12', '-o', 'autotrim=on',
         '-o', 'compatibility=' + COMPATIBILITY, '-R', root,
         '-O', 'mountpoint=none', '-O', 'canmount=off', '-O', 'compression=lz4',
         '-O', 'atime=off', '-O', 'xattr=sa', '-O', 'acltype=posixacl',
         '-O', 'normalization=formD', POOL, partition])
    run(['zfs', 'create', '-o', 'canmount=off', POOL + '/ROOT'])
    run(['zfs', 'create', '-o', 'canmount=noauto', '-o', 'mountpoint=legacy', '-o', 'quota=96G', ROOT])
    run(['zfs', 'create', '-o', 'canmount=on', '-o', 'mountpoint=legacy', '-o', 'quota=160G', HOME])
    run(['zfs', 'create', '-o', 'canmount=on', '-o', 'mountpoint=legacy',
         '-o', 'quota=512G', '-o', 'recordsize=64K', VM])
    run(['zfs', 'create', '-o', 'canmount=on', '-o', 'mountpoint=legacy',
         '-o', 'quota=64G', '-o', 'recordsize=1M', ISO])
    # This is an accounting reservation, not a file filled with zeros.
    run(['zfs', 'create', '-o', 'canmount=off', '-o', 'mountpoint=none',
         '-o', 'refreservation=96G', POOL + '/reserve'])
    run(['zpool', 'set', 'bootfs=' + ROOT, POOL])
    mount_datasets(root)


def mount_datasets(root):
    root = Path(root)
    run(['mount', '-t', 'zfs', ROOT, root])
    run(['mount', '--mkdir', '-t', 'zfs', HOME, root / 'home'])
    run(['mount', '--mkdir', '-t', 'zfs', VM, root / 'var/lib/libvirt/images'])
    run(['mount', '--mkdir', '-t', 'zfs', ISO, root / 'srv/iso'])


def fstab(esp_uuid):
    if not re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', esp_uuid):
        raise ValueError('Invalid FAT UUID')
    return (f'{ROOT} / zfs defaults 0 0\n{HOME} /home zfs defaults 0 0\n'
            f'{VM} /var/lib/libvirt/images zfs defaults 0 0\n'
            f'{ISO} /srv/iso zfs defaults 0 0\n'
            f'UUID={esp_uuid} /boot vfat defaults,umask=0077 0 2\n')


def kernel_hooks(text):
    if len(re.findall(r'(?m)^HOOKS=\([^\n]*\)', text)) != 1:
        raise RuntimeError('Не распознаны mkinitcpio HOOKS.')
    # Arch's busybox zfs hook cannot be combined with the systemd initramfs hook.
    hooks = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block zfs filesystems'
    text = re.sub(r'(?m)^HOOKS=\([^\n]*\)', 'HOOKS=(' + hooks + ')', text)
    # zfs install hook does not embed hostid itself. Always include it explicitly.
    text += '\nFILES+=(/etc/hostid)\n'
    return text


def configure(root):
    root = Path(root)
    run(['arch-chroot', root, 'zgenhostid', '-f', '-o', '/etc/hostid'])
    config = root / 'etc/mkinitcpio.conf'
    write(config, kernel_hooks(config.read_text()))
    write(root / 'etc/modprobe.d/zfs.conf', 'options zfs zfs_arc_max=8589934592\n')
    # Root is imported in initramfs; /home is legacy-mounted by fstab.
    run(['arch-chroot', root, 'systemctl', 'enable', 'zfs-zed.service', 'zfs-scrub-monthly@' + POOL + '.timer'])


if __name__ == '__main__':
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('Linux root required')
    if sys.argv[1:] == ['check-live']:
        check_live()
    elif len(sys.argv) == 3 and sys.argv[1] == 'trust':
        trust(Path(sys.argv[2]))
    else:
        raise SystemExit('Usage: zfs_support.py trust ROOT | check-live')
