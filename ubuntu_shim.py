"""Fetch only shim/MokManager data from Ubuntu, never execute Debian maintainer scripts.

Authenticity: pinned Ubuntu archive signing fingerprint -> signed InRelease ->
SHA256 of Packages.xz -> SHA256 of .deb. No AUR, no apt-key, no firmware writes.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
import io
import json
import lzma
import shutil
import subprocess
import tarfile
import urllib.request

ARCHIVE = 'https://archive.ubuntu.com/ubuntu/'
SIGNER = 'F6ECB3762474EDA9D21B7022871920D1991BC93C'
SUITES = ('resolute', 'resolute-updates', 'resolute-security')


def fetch(url, limit=32 * 1024 * 1024):
    if not url.startswith(ARCHIVE) or '..' in url.split('/'):
        raise RuntimeError('Unexpected download origin/path')
    with urllib.request.urlopen(url, timeout=90) as response:
        if not response.geturl().startswith(ARCHIVE):
            raise RuntimeError('Download redirected outside Ubuntu archive')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('Download exceeds size limit')
    return data


def stanzas(text):
    for block in text.strip().split('\n\n'):
        result = {}
        current = None
        for line in block.splitlines():
            if line.startswith((' ', '\t')) and current:
                result[current] += '\n' + line[1:]
            elif ': ' in line or line.endswith(':'):
                current, value = line.split(':', 1)
                result[current] = value.lstrip()
        yield result


def checked(data, expected, size=None):
    if sha256(data).hexdigest() != expected or (size is not None and len(data) != size):
        raise RuntimeError('Ubuntu metadata/package checksum mismatch')
    return data


def ar_members(blob):
    if not blob.startswith(b'!<arch>\n'):
        raise RuntimeError('Not a Debian ar archive')
    offset = 8
    while offset < len(blob):
        header = blob[offset:offset + 60]
        if len(header) != 60 or header[58:60] != b'`\n':
            raise RuntimeError('Invalid ar header')
        length = int(header[48:58])
        name = header[:16].decode('ascii').strip().rstrip('/')
        start = offset + 60
        end = start + length
        if end > len(blob):
            raise RuntimeError('Truncated ar member')
        yield name, blob[start:end]
        offset = end + (length % 2)


def extract_efi(blob):
    data = next((b for n, b in ar_members(blob) if n.startswith('data.tar')), None)
    if data is None:
        raise RuntimeError('Debian package has no data archive')
    # Target Arch Python supports zstd; old Python can use zstd from the system.
    if data.startswith(b'\x28\xb5\x2f\xfd'):
        data = subprocess.run(['zstd', '-dc'], input=data, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        entries = {m.name.removeprefix('./'): m for m in archive.getmembers()}

        def read(name):
            for _ in range(8):
                member = entries.get(name)
                if member is None:
                    return None
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    name = str(link if link.is_absolute() else PurePosixPath(name).parent / link).lstrip('/')
                    if '..' in PurePosixPath(name).parts:
                        raise RuntimeError('Unexpected package symlink')
                    continue
                if not member.isfile() or not 1024 <= member.size <= 8 * 1024 * 1024:
                    raise RuntimeError('Invalid EFI member')
                return archive.extractfile(member).read()
            raise RuntimeError('Package symlink loop')

        shim = read('usr/lib/shim/shimx64.efi.signed.latest')
        if shim is None:
            shim = read('usr/lib/shim/shimx64.efi.signed')
        manager = read('usr/lib/shim/mmx64.efi')
        if shim is None or manager is None or not shim.startswith(b'MZ') or not manager.startswith(b'MZ'):
            raise RuntimeError('Expected signed shim/MokManager files not found')
        return shim, manager


def newer(left, right):
    if shutil.which('vercmp'):
        result = subprocess.run(['vercmp', left, right], check=True, capture_output=True, text=True)
        return int(result.stdout.strip()) > 0
    return subprocess.run(['dpkg', '--compare-versions', left, 'gt', right]).returncode == 0


def download_verified(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    keyring = directory / 'ubuntu-archive-keyring.gpg'
    keyring.write_bytes(fetch(ARCHIVE + 'project/ubuntu-archive-keyring.gpg', 1024 * 1024))
    candidates = []
    for suite in SUITES:
        print('Checking signed Ubuntu metadata:', suite, flush=True)
        signed = directory / (suite + '.InRelease')
        release_path = directory / (suite + '.Release')
        signed.write_bytes(fetch(ARCHIVE + f'dists/{suite}/InRelease', 1024 * 1024))
        result = subprocess.run(
            ['gpgv', '--status-fd=1', '--keyring', str(keyring.resolve()),
             '--output', str(release_path), str(signed)],
            check=True, capture_output=True, text=True)
        valid = [line.split()[2] for line in result.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
        if SIGNER not in valid:
            raise RuntimeError('Unexpected Ubuntu archive signer; review required')
        release = next(stanzas(release_path.read_text()))
        if release.get('Codename') != 'resolute' or release.get('Suite') != suite:
            raise RuntimeError('Wrong Ubuntu release identity')
        if 'Valid-Until' in release and parsedate_to_datetime(release['Valid-Until']) < datetime.now(timezone.utc):
            raise RuntimeError('Ubuntu metadata expired; check time/network')
        index = 'main/binary-amd64/Packages.xz'
        checksums = {row.split()[2]: row.split()[:2] for row in release['SHA256'].splitlines() if len(row.split()) == 3}
        digest, size = checksums[index]
        compressed = checked(fetch(ARCHIVE + f'dists/{suite}/' + index), digest, int(size))
        packages = lzma.decompress(compressed).decode()
        for package in stanzas(packages):
            if package.get('Package') == 'shim-signed' and package.get('Architecture') == 'amd64':
                candidates.append(package)
    if not candidates:
        raise RuntimeError('shim-signed not found in verified Ubuntu metadata')
    selected = candidates[0]
    for candidate in candidates[1:]:
        if newer(candidate['Version'], selected['Version']):
            selected = candidate
    filename = selected['Filename']
    if not filename.startswith('pool/main/s/shim-signed/') or '..' in PurePosixPath(filename).parts:
        raise RuntimeError('Unexpected shim package path')
    package = checked(fetch(ARCHIVE + filename), selected['SHA256'], int(selected['Size']))
    shim, manager = extract_efi(package)
    (directory / 'shimx64.efi').write_bytes(shim)
    (directory / 'mmx64.efi').write_bytes(manager)
    manifest = {
        'version': selected['Version'], 'url': ARCHIVE + filename,
        'package_sha256': selected['SHA256'], 'archive_signer': SIGNER,
        'shim_sha256': sha256(shim).hexdigest(), 'manager_sha256': sha256(manager).hexdigest(),
    }
    (directory / 'shim-source.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Verified Ubuntu shim:', selected['Version'])
    return directory
