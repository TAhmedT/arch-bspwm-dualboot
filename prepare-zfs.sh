#!/usr/bin/env bash
# Runs only in the disposable Arch ISO. Never opens a target disk for writing.
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
[[ $EUID == 0 && -d /run/archiso && -d /sys/firmware/efi/efivars ]] || {
  echo 'prepare-zfs разрешён только от root в Arch Live USB (UEFI).'; exit 1;
}
python3 -B -c 'from arch_setup import secureboot_enabled; assert not secureboot_enabled(), "Временно отключите Secure Boot, сохранив заводские ключи"'
kernel=$(uname -r)
[[ "$kernel" =~ ^([0-9]+\.[0-9]+\.[0-9]+)-arch([0-9]+)-([0-9]+)$ ]] || {
  echo 'Нужен официальный Arch ISO со стандартным ядром linux.'; exit 1;
}
# Arch release 7.2.8-arch1-1 -> package version 7.2.8.arch1-1.
headers_version="${BASH_REMATCH[1]}.arch${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
timedatectl set-ntp true
# DKMS builds live in the RAM overlay. Do not place them on any Windows SSD.
[[ "$(findmnt -n -o FSTYPE --target /run/archiso/cowspace)" == tmpfs ]] || {
  echo 'Не распознан RAM overlay Arch ISO. Остановка без разметки.'; exit 1;
}
mem_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
(( mem_kib >= 24 * 1024 * 1024 )) || { echo 'Этот профиль подготовки рассчитан на ваши 32 ГБ RAM.'; exit 1; }
mount -o remount,size=12G /run/archiso/cowspace
pacman-key --init
pacman -Sy --needed --noconfirm archlinux-keyring
python3 -B zfs_support.py trust /
# Only this disposable ISO gets a partial upgrade: keep its running kernel.
# Re-enter Python after the upgrade so no old interpreter remains in memory.
pacman -Syu --needed --noconfirm --ignore linux,linux-headers \
  base-devel dkms zfs-dkms zfs-utils curl python git arch-install-scripts parted dosfstools
if [[ ! -f "/usr/lib/modules/$kernel/build/Makefile" ]]; then
  scratch=$(mktemp -d /run/arch-zfs-headers.XXXXXX)
  trap 'rm -rf -- "$scratch"' EXIT
  filename="linux-headers-${headers_version}-x86_64.pkg.tar.zst"
  url="https://archive.archlinux.org/packages/l/linux-headers/$filename"
  curl --fail --location --retry 2 "$url" -o "$scratch/$filename"
  curl --fail --location --retry 2 "$url.sig" -o "$scratch/$filename.sig"
  pacman-key --verify "$scratch/$filename.sig" "$scratch/$filename"
  pacman -U --needed --noconfirm "$scratch/$filename"
fi
dkms autoinstall -k "$kernel"
modprobe zfs
zfs version
python3 -B zfs_support.py check-live
echo 'ZFS в Live-среде готов. Диски не размечались. Теперь выберите install в меню.'
exec bash install.sh
