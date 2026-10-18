"""Safe offline tests. Never format disks, enroll keys, or install packages."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import arch_setup as app
import adapt_bspwm
import ubuntu_shim


def adata():
    return {'name': '/dev/nvme9n1', 'type': 'disk', 'size': 1_024_000_000_000,
            'model': app.MODEL, 'tran': 'nvme', 'rm': False, 'ro': False,
            'mountpoints': [None], 'children': [
                {'name': '/dev/nvme9n1p1', 'type': 'part', 'mountpoints': [None]}]}


def windows():
    return {'name': '/dev/nvme0n1', 'type': 'disk', 'model': app.WINDOWS_MODEL,
            'children': [{'name': '/dev/nvme0n1p1', 'type': 'part',
                          'parttype': app.ESP_TYPE, 'partuuid': 'beaffeed-1234-4321-abcd-123456789012',
                          'mountpoints': [None]}]}


class DiskSafety(unittest.TestCase):
    def test_only_expected_disk(self):
        app.validate_target(adata())
        for change in ({'model': app.WINDOWS_MODEL}, {'type': 'part'}, {'rm': True},
                       {'ro': True}, {'tran': 'usb'}, {'size': 500_000_000_000},
                       {'name': '/dev/mapper/volume'}):
            with self.subTest(change=change):
                disk = adata()
                disk.update(change)
                with self.assertRaises(RuntimeError):
                    app.validate_target(disk)

    def test_mounted_descendant_stops(self):
        disk = adata()
        disk['children'][0]['mountpoints'] = ['/mnt']
        with self.assertRaises(RuntimeError):
            app.validate_target(disk)

    def test_existing_arch_stops(self):
        disk = adata()
        disk['children'][0]['partlabel'] = 'ARCH_ROOT'
        with self.assertRaises(RuntimeError):
            app.validate_target(disk)

    def test_lvm_stops(self):
        disk = adata()
        disk['children'][0]['children'] = [{'name': '/dev/mapper/test', 'type': 'lvm'}]
        with self.assertRaises(RuntimeError):
            app.validate_target(disk)

    def test_windows_never_candidate(self):
        with self.assertRaises(RuntimeError):
            app.select_install([windows()], lambda _: self.fail('Should not prompt'))

    def test_multiple_adata_stops_before_prompt(self):
        with self.assertRaises(RuntimeError):
            app.select_install([adata(), adata(), windows()], lambda _: self.fail('Should not prompt'))

    def test_confirmation_requires_path_and_exact_phrase(self):
        for responses in (['/dev/nvme0n1'], ['/dev/nvme9n1', 'yes']):
            answers = iter(responses)
            with self.assertRaises(RuntimeError):
                app.select_install([adata(), windows()], lambda _: next(answers))
        answers = iter(['/dev/nvme9n1', 'ERASE ADATA LEGEND 900'])
        target, win = app.select_install([adata(), windows()], lambda _: next(answers))
        self.assertEqual(target['name'], '/dev/nvme9n1')
        self.assertEqual(win['name'], '/dev/nvme0n1p1')

    def test_missing_ambiguous_windows_esp_stops(self):
        with self.assertRaises(RuntimeError):
            app.windows_esp([adata()])
        with self.assertRaises(RuntimeError):
            app.windows_esp([windows(), windows()])
        disk = windows()
        disk['children'].insert(0, {'name': '/dev/nvme0n1p2', 'type': 'part', 'parttype': None})
        self.assertEqual(app.windows_esp([disk])['name'], '/dev/nvme0n1p1')

    def test_not_live_stops_before_commands(self):
        with patch.object(app, 'root_required'), patch.object(Path, 'is_dir', return_value=False), patch.object(app, 'run') as command:
            with self.assertRaises(RuntimeError):
                app.install()
            command.assert_not_called()

    def test_username_and_uuid_injection_rejected(self):
        for value in ('root', 'nobody', 'a;reboot', 'foo/bar', 'some user', ''):
            self.assertFalse(app.username_ok(value))
        self.assertTrue(app.username_ok('ahmed'))
        with self.assertRaises(ValueError):
            app.template_refind('bad', '};reboot')


class BootFiles(unittest.TestCase):
    def test_signed_update_hooks_and_shell_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc').mkdir()
            (root / 'usr/share/refind/icons').mkdir(parents=True)
            (root / 'boot/EFI/refind/themes/catppuccin').mkdir(parents=True)
            shim = root / 'shim'
            shim.mkdir()
            for name in ('shimx64.efi', 'mmx64.efi', 'shim-source.json'):
                (shim / name).write_text('fixture')
            state = {'root_uuid': 'abaffeed-1234-4321-abcd-123456789012',
                     'windows_guid': 'beaffeed-1234-4321-abcd-123456789012', 'esp_uuid': '1234-ABCD'}
            app.install_boot_assets(root, state, shim)
            for path in [*root.glob('usr/local/sbin/*'), *root.glob('etc/initcpio/post/*')]:
                subprocess.run(['bash', '-n', str(path)], check=True)
            updater = (root / 'usr/local/sbin/arch-secureboot-update').read_text()
            self.assertLess(updater.index('sbverify --cert'), updater.index('mv -f'))
            self.assertIn('--cmdline=@/etc/kernel/cmdline', updater)
            self.assertIn('arch-linux-previous.efi', updater)
            prehook = (root / 'etc/pacman.d/hooks/05-arch-esp-check.hook').read_text()
            self.assertIn('AbortOnFail', prehook)
            conf = (root / 'boot/EFI/refind/refind.conf').read_text()
            self.assertIn('loader /EFI/Linux/arch-linux.efi', conf)
            self.assertNotIn('cryptdevice', conf)
            self.assertNotIn('/vmlinuz-linux', conf)


class DownloadSafety(unittest.TestCase):
    def test_hash_mismatch_stops(self):
        self.assertEqual(ubuntu_shim.checked(b'abc', sha256(b'abc').hexdigest(), 3), b'abc')
        with self.assertRaises(RuntimeError):
            ubuntu_shim.checked(b'abc', '0' * 64)
        with self.assertRaises(RuntimeError):
            ubuntu_shim.checked(b'abc', sha256(b'abc').hexdigest(), 4)

    def test_metadata_parser(self):
        records = list(ubuntu_shim.stanzas('Package: other\nVersion: 1\n\nPackage: shim-signed\nSHA256:\n abc 3 path\n'))
        self.assertEqual(records[1]['Package'], 'shim-signed')
        self.assertIn('abc 3 path', records[1]['SHA256'])

    def test_package_extract_does_not_extract_paths(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:xz') as archive:
            for name in ('usr/lib/shim/shimx64.efi.signed.latest', 'usr/lib/shim/mmx64.efi'):
                data = b'MZ' + b'0' * 2048
                member = tarfile.TarInfo('./' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            bad = tarfile.TarInfo('../../evil')
            bad.size = 3
            archive.addfile(bad, io.BytesIO(b'bad'))
        data = stream.getvalue()
        header = f"{'data.tar.xz/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        package = b'!<arch>\n' + header + data + (b'\n' if len(data) % 2 else b'')
        shim, manager = ubuntu_shim.extract_efi(package)
        self.assertTrue(shim.startswith(b'MZ'))
        self.assertEqual(shim, manager)
        with self.assertRaises(RuntimeError):
            ubuntu_shim.extract_efi(package[:-20])

    def test_wrong_archive_signer_stops(self):
        fake = subprocess.CompletedProcess([], 0, '[GNUPG:] VALIDSIG BADFINGERPRINT 0 0\n', '')
        with tempfile.TemporaryDirectory() as directory, patch.object(ubuntu_shim, 'fetch', return_value=b'fixture'), patch.object(subprocess, 'run', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected Ubuntu archive signer'):
                ubuntu_shim.download_verified(directory)


class DesktopFiles(unittest.TestCase):
    def test_backup_display_and_repeat_run(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo, home = base / 'repo', base / 'home'
            home.mkdir()
            for name in adapt_bspwm.CONFIGS:
                (repo / 'config' / name).mkdir(parents=True)
            for name in ('bin', 'Images', 'themes', 'local/share/icons'):
                (repo / name).mkdir(parents=True)
            for name in ('Xresources', 'gtkrc-2.0'):
                (repo / name).write_text('/home/user\n')
            (repo / 'config/bspwm/bspwmrc').write_text('xrdb merge ~/.Xresources\npicom --config test &\n')
            (repo / 'config/polybar/config.ini').write_text('modules-right = battery backlight alsa\nmodules-center = temperature\nfont-2 = old\n')
            (repo / 'config/polybar/modules.ini').write_text('[module/time]\n')
            (repo / 'config/polybar/launch.sh').write_text('#!/bin/bash\n# Run on the desired monitor\n')
            (home / '.xinitrc').write_text('user original')
            adapt_bspwm.install(repo, home)
            saved = next((home / 'dotfiles-backups').iterdir())
            self.assertEqual((saved / '.xinitrc').read_text(), 'user original')
            self.assertNotIn('1920x1080', (home / '.config/bspwm/dual_monitors.sh').read_text())
            self.assertIn('modules-right = pulseaudio', (home / '.config/polybar/config.ini').read_text())
            self.assertNotIn('/home/user', (home / '.gtkrc-2.0').read_text())
            adapt_bspwm.install(repo, home)
            self.assertEqual((home / '.config/polybar/modules.ini').read_text().count('[module/pulseaudio]'), 1)
            for path in (home / '.xinitrc', home / '.config/bspwm/dual_monitors.sh', home / '.config/bspwm/bspwmrc', home / 'bin/screen-lock', home / 'bin/powermenu'):
                subprocess.run(['bash', '-n', path], check=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
