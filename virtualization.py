"""Install a local KVM/libvirt workstation. No guests are created."""
from pathlib import Path
import grp
import re
from arch_setup import run, write, load_state, root_required
from zfs_support import POOL, VM, ISO

PACKAGES = '''qemu-desktop libvirt virt-manager virt-install dnsmasq
edk2-ovmf swtpm iptables-nft nftables'''.split()


def setup_host():
    root_required()
    state = load_state()
    if Path('/run/archiso').is_dir():
        raise RuntimeError('Виртуализация настраивается в установленном Arch.')
    run(['modprobe', 'kvm_amd'])
    if not Path('/dev/kvm').exists():
        raise RuntimeError('Нет /dev/kvm. В ASUS UEFI включите Advanced → CPU Configuration → SVM Mode.')
    for dataset, path in ((VM, '/var/lib/libvirt/images'), (ISO, '/srv/iso')):
        if run(['findmnt', '-n', '-o', 'SOURCE', '--target', path], capture=True) != dataset:
            raise RuntimeError('Не смонтирован отдельный dataset для ' + path)
    run(['pacman', '-Syu', '--needed', *PACKAGES])
    write('/etc/modules-load.d/kvm-amd.conf', 'kvm_amd\n')
    # /home stays private: ISO files intended for guests go in /srv/iso.
    images = Path('/var/lib/libvirt/images')
    images.chmod(0o755)
    iso = Path('/srv/iso')
    import os
    os.chown(iso, 0, grp.getgrnam('kvm').gr_gid)
    iso.chmod(0o2775)
    run(['usermod', '-aG', 'libvirt,kvm', state['username']])
    # Keep modular and monolithic daemons from competing for the same socket.
    # Arch's default monolithic service uses polkit for local authorization.
    for name in ('virtqemud', 'virtnetworkd', 'virtstoraged', 'virtnodedevd', 'virtnwfilterd', 'virtsecretd', 'virtinterfaced'):
        for suffix in ('.socket', '-ro.socket', '-admin.socket', '.service'):
            if run(['systemctl', 'is-active', '--quiet', name + suffix], check=False) == 0:
                raise RuntimeError('Уже активна модульная служба ' + name + suffix + '; используйте существующую настройку libvirt.')
    # NAT firewall managed by libvirt, with no global firewall flush/disable.
    config = Path('/etc/libvirt/network.conf')
    content = config.read_text() if config.exists() else ''
    if not re.search(r'(?m)^firewall_backend\s*=', content):
        write(config, content.rstrip() + '\nfirewall_backend = "nftables"\n')
    run(['systemctl', 'enable', '--now', 'libvirtd.service'])
    uri = ['virsh', '--connect', 'qemu:///system']
    pools = run([*uri, 'pool-list', '--all', '--name'], capture=True).splitlines()
    if 'default' not in pools:
        run([*uri, 'pool-define-as', 'default', 'dir', '--target', images])
    else:
        # Never silently repoint an existing storage pool.
        import xml.etree.ElementTree as ET
        xml = run([*uri, 'pool-dumpxml', 'default'], capture=True)
        if ET.fromstring(xml).findtext('./target/path') != str(images):
            raise RuntimeError('У существующего пула libvirt default другой путь. Ничего не перепривязываю.')
    active = run([*uri, 'pool-list', '--name'], capture=True).splitlines()
    if 'default' not in active:
        run([*uri, 'pool-start', 'default'])
    run([*uri, 'pool-autostart', 'default'])
    networks = run([*uri, 'net-list', '--all', '--name'], capture=True).splitlines()
    if 'default' not in networks:
        # Use the packaged libvirt NAT network; no host bridge or NIC takeover.
        default_xml = Path('/etc/libvirt/qemu/networks/default.xml')
        if not default_xml.is_file():
            raise RuntimeError('Не найден стандартный XML сети libvirt. Создайте NAT в virt-manager.')
        run([*uri, 'net-define', default_xml])
    active = run([*uri, 'net-list', '--name'], capture=True).splitlines()
    if 'default' not in active:
        run([*uri, 'net-start', 'default'])
    run([*uri, 'net-autostart', 'default'])
    run(['virt-host-validate', 'qemu'], check=False)
    run(['zfs', 'list', '-o', 'name,used,available,quota,refreservation', '-r', POOL])
    print('KVM/libvirt готов. Выйдите из сеанса и войдите снова, затем запустите virt-manager.')
    print('Подключение: QEMU/KVM system. ВМ не создавались. Инструкция: VIRTUALIZATION.md.')


if __name__ == '__main__':
    setup_host()
