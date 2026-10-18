# Secure Boot: выбранная схема

Установка выполняется с временно выключенным Secure Boot. В конечной
конфигурации он включён для Windows и Arch. Шифрование Linux не используется.

```text
ASUS UEFI: Secure Boot ON, заводские PK / KEK / db
  └─ Microsoft-signed shim на EFI ADATA
      └─ rEFInd, подписанный локальным MOK
          ├─ штатный Windows Boot Manager на Samsung
          └─ UKI Arch, подписанный тем же MOK
```

UKI включает ядро, initramfs, микрокод AMD и параметры root. Подпись относится
ко всему образу. Скрипт не оставляет отдельную неподписанную initramfs как
внешний источник параметров загрузки Arch.

## Почему это сохраняет заводские ключи

MOK добавляет сертификат в механизм доверия shim через MokManager.
Это не замена Platform Key и не команда `sbctl enroll-keys`.
Скрипты не выполняют Clear Keys, Setup Mode, очистку TPM, отключение проверки
shim, выключение VBS или обход проверок Vanguard.

`grubx64.efi` на EFI ADATA в этом сценарии — **подписанный rEFInd**.
Такое имя требуется shim для второго этапа. Сам GRUB в Arch не устанавливается.
UEFI-запись указывает на `shimx64.efi`.

Прямой Windows Boot Manager остаётся отдельной записью прошивки и не заменяется.

## Проверки источника shim

`ubuntu_shim.py` загружает `shim-signed` из Ubuntu 26.04 LTS (resolute), включая
updates/security. Используются только данные `.deb`, не Debian maintainer scripts.

1. Проверяет GPG-подпись InRelease официального архива.
2. Принимает только архивный ключ с отпечатком
   `F6ECB3762474EDA9D21B7022871920D1991BC93C`.
3. Проверяет срок метаданных, если указан Valid-Until.
4. Проверяет SHA256 индекса Packages.xz из подписанного Release.
5. Проверяет SHA256 и размер пакета из этого индекса.
6. Извлекает только shim и MokManager без распаковки произвольных путей на диск.

Это подтверждает происхождение пакета. Отдельно прошивка проверяет его
Microsoft-подпись при включённом Secure Boot. Текущая база db/dbx компьютера
может не доверять определённой версии: это проверяется реальной загрузкой.
Если в ASUS есть настройка Microsoft Third Party UEFI CA, для shim она должна
быть разрешена. Доверие к Windows не всегда означает доверие к Linux shim.

## NVIDIA и lockdown

Подпись EFI/UKI и подпись модулей ядра — разные механизмы. Стандартные ядра Arch
не включают kernel lockdown автоматически при Secure Boot. Скрипт не меняет эту
политику и не выдаёт её за полное усиление защиты Linux. Поэтому отдельная
обязательная подпись модулей `nvidia-open` здесь не настраивается.
Если добавлять lockdown/module.sig_enforce, нужно отдельно настроить подписи
NVIDIA, доверие ядра к ключу и пересборку; не включайте это вслепую.

## VALORANT

Riot указывает VAN 9003 для выключенного Secure Boot и VAN 9001 для TPM 2.0.
Для STATUS_SB_POLICY Riot рекомендует заводскую базу ключей.
Сохранение заводских ключей уменьшает изменения в политике, но не является
гарантией принятия любой цепочки загрузки конкретной версией Vanguard.

После установки проверяются Windows Secure Boot State и реальный запуск игры.
Если возникает ошибка при запуске Windows из rEFInd, сравните с прямым Windows
Boot Manager через UEFI Boot Menu и сохраните точный код VAN.
Проверки античита не обходятся.

## Ссылки

- [rEFInd + shim/MOK](https://www.rodsbooks.com/refind/secureboot.html)
- [Arch UKI](https://wiki.archlinux.org/title/Unified_kernel_image)
- [Arch kernel lockdown](https://wiki.archlinux.org/title/Security#Kernel_lockdown_mode)
- [Ubuntu image/key verification](https://documentation.ubuntu.com/security/software-integrity/image-verification/)
- [Riot VAN 9003](https://support.riotgames.com/en-us/riot/client/error-van-9003)
- [Riot VAN 9001](https://support.riotgames.com/en-us/riot/client/error-van-9001)
- [Riot STATUS_SB_POLICY](https://support.riotgames.com/en-us/riot/performance/error-van-status-sb-policy)
