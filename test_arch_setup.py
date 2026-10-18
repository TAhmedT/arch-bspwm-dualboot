"""Safe offline tests. Never format disks, enroll keys, or install packages."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import io
import json
import configparser
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import arch_setup as app
import adapt_bspwm
import ubuntu_shim
import zfs_support
import storage
import graphical_login as graphical
import keybindings
import xml.etree.ElementTree as ET


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
            app.template_refind('};reboot')

    def test_zfs_preflight_failure_prevents_any_partition_commands(self):
        with patch.object(app, 'root_required'), patch.object(Path, 'is_dir', return_value=True), \
             patch.object(app, 'secureboot_enabled', return_value=False), \
             patch.object(zfs_support, 'check_live', side_effect=RuntimeError('no module')), patch.object(app, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'no module'):
                app.install()
            command.assert_not_called()

    def test_existing_zfs_stops_even_without_arch_labels(self):
        disk = adata()
        disk['children'][0]['fstype'] = 'zfs_member'
        with self.assertRaises(RuntimeError):
            app.validate_target(disk)
        for label in ('ARCH_EFI', 'ARCH_ZFS'):
            disk = adata()
            disk['children'][0]['partlabel'] = label
            with self.assertRaises(RuntimeError):
                app.validate_target(disk)


class MokVerification(unittest.TestCase):
    cert = '/etc/secureboot/MOK.cer'

    def check_result(self, code, message, stderr=''):
        result = subprocess.CompletedProcess([], code, message, stderr)
        with patch.object(app.subprocess, 'run', return_value=result) as command, \
             patch('sys.stdout', new_callable=io.StringIO), \
             patch('sys.stderr', new_callable=io.StringIO):
            enrolled = app.mok_enrolled()
        self.assertEqual(command.call_args.args[0], ['mokutil', '--test-key', self.cert])
        self.assertEqual(command.call_args.kwargs['env']['LC_ALL'], 'C')
        return enrolled

    def test_enrolled_key_with_old_and_new_exit_codes(self):
        for code in (0, 1):
            with self.subTest(code=code):
                self.assertTrue(self.check_result(code, f'{self.cert} is already enrolled\n'))

    def test_missing_pending_blocked_or_different_key_never_passes(self):
        for code in (0, 1):
            for message in ('is not enrolled', 'is already in the enrollment request',
                            'is blocked in dbx', 'is blocked in MokListX', 'is already in db'):
                with self.subTest(code=code, message=message):
                    self.assertFalse(self.check_result(code, f'{self.cert} {message}\n'))
            self.assertFalse(self.check_result(code, '/other/key.cer is already enrolled\n'))

    def test_command_failure_or_unknown_output_never_passes(self):
        self.assertFalse(self.check_result(255, '', 'Failed to open certificate\n'))
        self.assertFalse(self.check_result(1, '', 'EFI variables are not supported\n'))
        self.assertFalse(self.check_result(0, 'unexpected output\n'))
        self.assertFalse(self.check_result(255, f'{self.cert} is already enrolled\n'))

    def test_verify_still_checks_mok_and_image_signatures(self):
        state = {'esp_uuid': '1234-ABCD', 'pool_guid': '42'}

        def command(args, **kwargs):
            if args[0] == 'sbverify':
                return 1 if failed_signatures else 0
            if args[0] == 'findmnt':
                return {'/boot': state['esp_uuid'], '/': zfs_support.ROOT,
                        '/home': zfs_support.HOME}[args[-1]]
            if args[:2] == ['zpool', 'get']:
                return '42' if args[-2] == 'guid' else 'ONLINE'
            return 0

        for enrolled, failed_signatures in ((True, False), (False, False), (True, True)):
            with self.subTest(enrolled=enrolled, failed_signatures=failed_signatures), \
                 patch.object(app, 'root_required'), \
                 patch.object(app, 'load_state', return_value=state), \
                 patch.object(app, 'secureboot_enabled', return_value=True), \
                 patch.object(app, 'mok_enrolled', return_value=enrolled) as mok, \
                 patch.object(app, 'run', side_effect=command) as run, \
                 patch('sys.stdout', new_callable=io.StringIO):
                if not enrolled or failed_signatures:
                    with self.assertRaisesRegex(RuntimeError, 'sbverify' if failed_signatures else 'mokutil'):
                        app.verify()
                else:
                    app.verify()
                mok.assert_called_once_with()
                self.assertEqual(sum(call.args[0][0] == 'sbverify' for call in run.call_args_list), 2)


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
            self.assertLess(updater.index('modinfo -k "$kver" zfs'), updater.index('ukify build'))
            self.assertIn('etc/hostid', updater)
            self.assertIn('root=ZFS=archpool/ROOT/arch', (root / 'etc/kernel/cmdline').read_text())
            prehook = (root / 'etc/pacman.d/hooks/05-arch-esp-check.hook').read_text()
            self.assertIn('AbortOnFail', prehook)
            conf = (root / 'boot/EFI/refind/refind.conf').read_text()
            self.assertIn('loader /EFI/Linux/arch-linux.efi', conf)
            self.assertNotIn('cryptdevice', conf)
            self.assertNotIn('/vmlinuz-linux', conf)


class ZfsStorage(unittest.TestCase):
    def test_dataset_layout_keeps_vm_and_iso_outside_root_home_quotas(self):
        with patch.object(zfs_support, 'run') as command:
            zfs_support.create_pool('/dev/nvme9n1p2', Path('/mnt/test'))
        calls = [item.args[0] for item in command.call_args_list]
        create = calls[0]
        self.assertEqual(create[-2:], ['archpool', '/dev/nvme9n1p2'])
        self.assertNotIn('-f', create)
        self.assertIn('compatibility=openzfs-2.2-linux', create)
        for name, quota in ((zfs_support.ROOT, '96G'), (zfs_support.HOME, '160G'),
                            (zfs_support.VM, '512G'), (zfs_support.ISO, '64G')):
            entry = next(args for args in calls if args[:2] == ['zfs', 'create'] and args[-1] == name)
            self.assertIn('quota=' + quota, entry)
        self.assertTrue(any('refreservation=96G' in args for args in calls))
        self.assertTrue(any(args[-1] == Path('/mnt/test/var/lib/libvirt/images') for args in calls))

    def test_fstab_uses_legacy_datasets_not_zfs_uuid(self):
        fstab = zfs_support.fstab('1234-ABCD')
        self.assertIn('archpool/ROOT/arch / zfs defaults 0 0', fstab)
        self.assertIn('archpool/vm /var/lib/libvirt/images zfs', fstab)
        self.assertIn('archpool/iso /srv/iso zfs', fstab)
        self.assertIn('umask=0077', fstab)
        with self.assertRaises(ValueError):
            zfs_support.fstab('evil\nentry')

    def test_busybox_zfs_hooks_include_hostid_and_exclude_systemd(self):
        text = zfs_support.kernel_hooks('HOOKS=(base systemd autodetect block filesystems fsck)\n')
        hooks = text.split('HOOKS=(', 1)[1].split(')', 1)[0].split()
        self.assertNotIn('systemd', hooks)
        self.assertLess(hooks.index('zfs'), hooks.index('filesystems'))
        self.assertIn('udev', hooks)
        self.assertIn('FILES+=(/etc/hostid)', text)
        with self.assertRaises(RuntimeError):
            zfs_support.kernel_hooks('# unknown format')

    def test_unexpected_release_key_never_gets_local_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(zfs_support, 'run', return_value='fpr:::::::::BAD:') as command:
                with self.assertRaises(RuntimeError):
                    zfs_support.trust(Path(directory))
            self.assertFalse(any('--lsign-key' in c.args[0] for c in command.call_args_list))

    def test_storage_names_and_sizes_cannot_escape_vm_pool(self):
        for value in ('../root', 'foo/bar', 'x;reboot', 'UPPER', ''):
            with self.assertRaises(ValueError):
                storage.child_name(value)
        self.assertEqual(storage.child_name('lab01'), 'lab01')
        for value in ('0', '-1', '99999', '1G', '5;reboot'):
            with self.assertRaises(ValueError):
                storage.gib(value)
        self.assertEqual(storage.gib('40'), 40)

    def test_cancelled_zvol_creation_never_creates_a_disk(self):
        with patch('builtins.input', side_effect=['lab01', '40', 'no']), \
             patch.object(storage, 'run', return_value='archpool/vm') as command:
            with self.assertRaises(RuntimeError):
                storage.create_vm_storage(block=True)
        self.assertFalse(any('create' in c.args[0] for c in command.call_args_list))


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


class GraphicalLogin(unittest.TestCase):
    def test_system_files_use_password_login_and_a_real_locker_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graphical.install_system_files(root)
            config = configparser.ConfigParser(interpolation=None)
            config.read(root / 'etc/lightdm/lightdm.conf')
            self.assertEqual(config['Seat:*']['autologin-user'], '')
            self.assertEqual(config['Seat:*']['allow-guest'], 'false')
            self.assertEqual(config['Seat:*']['user-session'], 'arch-bspwm')
            session = (root / 'usr/local/bin/arch-bspwm-session').read_text()
            self.assertIn('light-locker --lock-on-suspend --no-late-locking', session)
            self.assertIn('exec bspwm', session)
            greeter = configparser.ConfigParser(interpolation=None)
            greeter.read(root / 'etc/lightdm/lightdm-gtk-greeter.conf')
            self.assertIn('~layout', greeter['greeter']['indicators'])
            for key in ('background', 'default-user-image'):
                asset = root / greeter['greeter'][key].lstrip('/')
                ET.parse(asset)
                self.assertEqual(asset.stat().st_mode & 0o777, 0o644)
            for name in ('usr/local/bin/arch-bspwm-session', 'usr/local/libexec/arch-greeter-layout'):
                subprocess.run(['sh', '-n', str(root / name)], check=True)

    def test_gui_migration_preserves_desktop_and_upstream_shortcuts(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            keys = home / '.config/sxhkd/sxhkdrc'
            keys.parent.mkdir(parents=True)
            original = ('super + Return\n    alacritty\n'
                        'super + shift + {k,l}\n    {alacritty,sh $HOME/bin/screen-lock}\n')
            keys.write_text(original)
            bspwm = home / '.config/bspwm/bspwmrc'
            bspwm.parent.mkdir()
            bspwm.write_text('personal desktop settings\n')
            graphical.configure_user(home)
            self.assertEqual(bspwm.read_text(), 'personal desktop settings\n')
            self.assertIn('alt + shift + {k,l}', keys.read_text())
            self.assertEqual(keys.read_text().count('bin/screen-lock'), 1)
            saved = next((home / 'dotfiles-backups').iterdir())
            self.assertEqual((saved / '.config/sxhkd/sxhkdrc').read_text(), original)
            before = keys.read_text()
            graphical.configure_user(home)
            self.assertEqual(keys.read_text(), before)
            self.assertEqual(len(list((home / 'dotfiles-backups').iterdir())), 1)
            for name in ('screen-lock', 'powermenu'):
                subprocess.run(['sh', '-n', str(home / 'bin' / name)], check=True)

    def test_system_configuration_backup_is_preserved_on_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'etc/lightdm/lightdm.conf'
            config.parent.mkdir(parents=True)
            config.write_text('[Seat:*]\nuser-session=old-session\n')
            graphical.install_system_files(root)
            saved = next((root / 'var/backups/arch-dualboot').iterdir())
            original = saved / 'etc/lightdm/lightdm.conf'
            self.assertIn('old-session', original.read_text())
            graphical.install_system_files(root)
            self.assertEqual(len(list((root / 'var/backups/arch-dualboot').iterdir())), 1)
            self.assertIn('old-session', original.read_text())

    def test_symlink_refusal_happens_before_replacing_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'bin').mkdir()
            locker = home / 'bin/screen-lock'
            locker.write_text('original locker')
            (home / 'bin/powermenu').symlink_to(home / 'elsewhere')
            with self.assertRaises(RuntimeError):
                graphical.configure_user(home)
            self.assertEqual(locker.read_text(), 'original locker')

    def test_lock_and_suspend_fail_without_a_managed_locker(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            graphical.configure_user(home)
            stubs = home / 'stubs'
            stubs.mkdir()
            scripts = {
                'pgrep': 'exit "${LOCKER_RUNNING:-1}"',
                'notify-send': 'exit 0',
                'light-locker-command': 'printf "lock\\n" >> "$COMMAND_LOG"',
                'systemctl': 'printf "suspend\\n" >> "$COMMAND_LOG"',
                'rofi': 'printf "Suspend\\n"',
            }
            for name, body in scripts.items():
                path = stubs / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            log = home / 'commands.log'
            base = {**os.environ, 'HOME': str(home), 'PATH': f'{stubs}:{os.environ["PATH"]}',
                    'COMMAND_LOG': str(log)}
            base.pop('XDG_SESSION_PATH', None)
            for managed, running in ((False, True), (True, False), (True, True)):
                with self.subTest(managed=managed, running=running):
                    env = {**base, 'LOCKER_RUNNING': '0' if running else '1'}
                    if managed:
                        env['XDG_SESSION_PATH'] = '/org/freedesktop/DisplayManager/Session0'
                    log.unlink(missing_ok=True)
                    result = subprocess.run(['bash', str(home / 'bin/powermenu')],
                                            env=env, capture_output=True)
                    if managed and running:
                        self.assertEqual(result.returncode, 0)
                        self.assertEqual(log.read_text(), 'lock\nsuspend\n')
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(log.exists())

    def test_enabling_graphical_boot_does_not_restart_the_current_session(self):
        with patch.object(graphical, 'root_required'), patch.object(graphical, 'load_state'), \
             patch.object(Path, 'is_dir', return_value=False), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'is_symlink', return_value=False), \
             patch.object(graphical, 'install_system_files'), patch.object(graphical, 'run') as command:
            graphical.configure_system()
        calls = [call.args[0] for call in command.call_args_list]
        self.assertIn(['systemctl', 'enable', 'lightdm.service'], calls)
        self.assertIn(['systemctl', 'set-default', 'graphical.target'], calls)
        self.assertFalse(any(value in args for args in calls for value in ('--now', 'start', 'restart')))


class AltBindings(unittest.TestCase):
    def test_commands_are_preserved_and_expanded_shortcuts_do_not_collide(self):
        text = '''# super is a modifier, not a command substitution
super + Escape + r
    pkill -USR1 -x sxhkd
super + Return
    echo super mod4 alt
Alt_L + shift
    sh $HOME/bin/change_language.sh
Shift_L + alt
    sh $HOME/bin/change_language.sh
super + shift + {k,l}
    {alacritty,sh $HOME/bin/screen-lock}
super + control {h,j,k,l}
    bspc node -z {left -20 0,right 20 0,top 0 -20,bottom 0 20}
super + {_,alt + }{h,j,k,l}
    bspc node -{f,s} {west,south,north,east}
super + {_,shift + }{1-9,0}
    bspc {desktop -f,node -d} '^{1-9,10}'
alt + {_,shift + }Tab
    bspc node -f {next.local,prev.local}
super + {grave,Tab}
    bspc {node,desktop} -f last
'''
        updated = keybindings.alt_hotkeys(text)
        self.assertIn('    echo super mod4 alt\n', updated)
        self.assertIn('alt + Escape\n', updated)
        self.assertIn('alt + {grave,ctrl + Tab}\n', updated)
        self.assertNotIn('change_language.sh', updated)
        self.assertIn('alt + control + {h,j,k,l}', updated)
        self.assertIn('alt + {_,ctrl + shift + }{h,j,k,l}', updated)
        self.assertEqual(keybindings.alt_hotkeys(updated), updated)

        def expand(line):
            match = re.search(r'\{([^{}]*)\}', line)
            if match is None:
                return [line]
            values = []
            for value in match[1].split(','):
                if re.fullmatch(r'\d-\d', value):
                    values.extend(map(str, range(int(value[0]), int(value[2]) + 1)))
                else:
                    values.append('' if value == '_' else value)
            return [expanded for value in values
                    for expanded in expand(line[:match.start()] + value + line[match.end():])]

        seen = set()
        for line in updated.splitlines():
            if not line or line[0].isspace() or line.startswith('#'):
                continue
            for header in expand(line):
                tokens = [token.strip().replace('control', 'ctrl') for token in header.split('+')]
                self.assertEqual(len(tokens[:-1]), len(set(tokens[:-1])), header)
                signature = (tuple(sorted(tokens[:-1])), tokens[-1])
                self.assertNotIn(signature, seen, header)
                seen.add(signature)

    def test_migration_backs_up_configs_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            keys = home / '.config/sxhkd/sxhkdrc'
            bspwm = home / '.config/bspwm/bspwmrc'
            keys.parent.mkdir(parents=True)
            bspwm.parent.mkdir(parents=True)
            keys.write_text('super + Return\n    alacritty\n')
            bspwm.write_text('#!/bin/sh\nbspc config pointer_modifier mod4\n# personal settings\n')
            bspwm.chmod(0o755)
            (home / '.xinitrc').write_text('setxkbmap -layout us,ru -option grp:alt_shift_toggle\nexec bspwm\n')
            (home / 'personal.txt').write_text('unchanged')
            keybindings.configure_user(home)
            saved = next((home / 'dotfiles-backups').iterdir())
            self.assertIn('super + Return', (saved / '.config/sxhkd/sxhkdrc').read_text())
            self.assertIn('pointer_modifier mod1', bspwm.read_text())
            self.assertIn('# personal settings', bspwm.read_text())
            self.assertEqual(bspwm.stat().st_mode & 0o777, 0o755)
            self.assertIn('grp:toggle', (home / '.xinitrc').read_text())
            self.assertNotIn('alt_shift_toggle', (home / '.xinitrc').read_text())
            self.assertEqual((home / 'personal.txt').read_text(), 'unchanged')
            before = bspwm.read_text()
            keybindings.configure_user(home)
            self.assertEqual(bspwm.read_text(), before)
            self.assertEqual(len(list((home / 'dotfiles-backups').iterdir())), 1)
            subprocess.run(['sh', '-n', str(bspwm)], check=True)

    def test_greeter_keyboard_update_does_not_reconfigure_or_restart_lightdm(self):
        with patch.object(graphical, 'root_required'), patch.object(graphical, 'load_state'), \
             patch.object(Path, 'is_dir', return_value=False), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(graphical, '_install_files') as install, patch.object(graphical, 'run') as command:
            graphical.configure_system_keyboard()
        paths = install.call_args.args[0]
        self.assertEqual(set(paths), {Path('/usr/local/bin/arch-bspwm-session'),
                                      Path('/usr/local/libexec/arch-greeter-layout')})
        for text, mode in paths.values():
            self.assertIn('grp:toggle', text)
            self.assertNotIn('grp:alt_shift_toggle', text)
        command.assert_not_called()

    def test_live_update_reloads_only_the_users_hotkeys_without_restarting_bspwm(self):
        with patch.object(keybindings.sys, 'platform', 'linux'), \
             patch.object(keybindings.os, 'geteuid', return_value=1000), \
             patch.dict(os.environ, {'DISPLAY': ':99'}), \
             patch.object(keybindings, 'configure_user'), patch.object(Path, 'is_file', return_value=False), \
             patch.object(keybindings, 'run', return_value=0) as command:
            keybindings.setup()
        calls = [call.args[0] for call in command.call_args_list]
        self.assertIn(['bspc', 'config', 'pointer_modifier', 'mod1'], calls)
        self.assertIn(['pkill', '-USR1', '-u', '1000', '-x', 'sxhkd'], calls)
        self.assertIn(['setxkbmap', '-layout', 'us,ru', '-option', '', '-option', 'grp:toggle'], calls)
        self.assertFalse(any('restart' in args or 'wm' in args for args in calls))


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
