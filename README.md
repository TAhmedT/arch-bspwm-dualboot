# Arch + Windows: BSPWM, rEFInd и Secure Boot

Пошаговая установка для **Ryzen 5 3600 / RTX 3060 Ti / 32 ГБ RAM**.
Arch занимает отдельный **ADATA LEGEND 900 (~1 ТБ, прежний E:)**.
Windows остаётся на Samsung SSD 980 1TB; Samsung 860 EVO с D: не форматируется.

**Сначала прочитайте весь раздел подготовки. `install` удаляет ВСЁ на ADATA.**
Установщик рассчитан только на эту конфигурацию и откажется выбирать другой
накопитель. Защита от ошибочного выбора не заменяет резервную копию данных.

Что получится:

- Arch с ext4, без шифрования/LVM и без запроса пароля диска.
- Отдельный EFI-раздел 2 ГиБ на ADATA, смонтированный в `/boot`.
- Графическое меню rEFInd с темой [Catppuccin Mocha](https://github.com/catppuccin/refind).
- Secure Boot отключается **временно**, затем остаётся включённым для обеих ОС.
- Заводские ключи ASUS/Microsoft сохраняются; доверие к Arch добавляется через MOK.
- BSPWM на основе [Zproger](https://github.com/Zproger/bspwm-dotfiles),
  с настройками монитора без принудительных 1920×1080, PipeWire и Ethernet.
- Windows/Linux одинаково используют UTC для аппаратных часов.

## Что автоматизировано, а что остаётся вручную

| Скрипт | Задача |
|---|---|
| `windows.ps1` | Статус Secure Boot/TPM/BitLocker, UTC, отключение Fast Startup; отдельные флаги для приостановки и возобновления BitLocker |
| `bash install.sh inspect` | Только просмотр дисков и UEFI |
| `bash install.sh install` | Проверка дисков, подтверждение, разметка ADATA, Arch, NVIDIA, rEFInd, UKI, подписи и запрос MOK |
| `bash install.sh desktop` | Пакеты рабочего стола и адаптация dotfiles с резервной копией |
| `sudo bash install.sh verify` | Проверка включённого Secure Boot, MOK, подписей и EFI-раздела |
| `sudo bash install.sh enroll` | Повторный запрос регистрации вашего MOK |
| `sudo bash install.sh refresh-shim` | Проверенное обновление shim/MokManager |
| `bash install.sh repair` | Recovery shell с Live USB, без форматирования |

Без аргументов `bash install.sh` открывает меню этих этапов.

**Руками:** записать флешку, сохранить ключ BitLocker, изменить переключатель
Secure Boot в UEFI, подтвердить стирание ADATA, задать пароли и подтвердить MOK
на экране прошивки. Пароли не передаются через аргументы команд и не сохраняются
в репозитории. Скрипт не перезагружает компьютер автоматически.

## 1. Подготовка в Windows

Скопируйте важные данные на независимый носитель. Проверьте, что **на E: нет
нужных файлов**. Сохраните эту инструкцию на телефоне или другом компьютере:
во время установки Windows и браузер будут недоступны.

Откройте PowerShell **от имени администратора**, клонируйте репозиторий на C:
или D: (не на E:):

```powershell
git clone https://github.com/TAhmedT/arch-bspwm-dualboot.git
cd arch-bspwm-dualboot
.\windows.ps1 -Prepare
```

Если локальная политика PowerShell блокирует запуск файла, разрешение только
для одного процесса, без изменения постоянной политики:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows.ps1 -Prepare
```

`-Prepare` включает UTC RTC и отключает гибернацию/Fast Startup. Обычный сон
остаётся. Часовой пояс Windows не меняется. Secure Boot и TPM скрипт не меняет.

В выводе `manage-bde` проверьте C:. **Если BitLocker активен**, сохраните recovery
key вне этого ПК, затем выполните:

```powershell
.\windows.ps1 -SuspendBitLocker
```

Скрипт попросит подтвердить, что ключ сохранён. Защита будет приостановлена до
ручного возобновления, диск останется зашифрованным. Если C: не зашифрован,
этот этап пропускается. При ошибке чтения BitLocker сначала выясните его статус.

В `tpm.msc` проверьте готовность TPM и версию спецификации 2.0. Для установки
Linux **не очищайте TPM**. Скачайте [официальный Arch ISO](https://archlinux.org/download/),
проверьте опубликованную подпись/контрольные суммы и запишите на отдельную
флешку через Rufus/Etcher. Запись флешки сотрёт её содержимое.

Полностью завершите работу Windows.

## 2. Временно отключить Secure Boot и загрузить Arch USB

На ASUS обычно вход в UEFI — `Del`, Boot Menu — `F8`; названия и клавиши могут
отличаться в зависимости от прошивки. Выключите **только Secure Boot**.
Сохраните заводские ключи. Не выбирайте `Clear/Delete All Secure Boot Keys`,
не переходите в Setup Mode и не очищайте TPM. Не меняйте SATA/RAID mode.

Загружайте флешку как **UEFI**, без Legacy/CSM. Установку удобнее проводить
через подключённый Ethernet. Официальный Arch ISO без этой временной меры
с Secure Boot не загрузится.

В консоли Live USB вы уже root. Достаточно трёх команд:

```bash
pacman -Sy --needed git python
git clone https://github.com/TAhmedT/arch-bspwm-dualboot.git
cd arch-bspwm-dualboot && bash install.sh
```

Здесь `pacman -Sy` относится к одноразовой Live-среде, не установленной системе.
В обычном Arch обновления выполняются через `pacman -Syu`.

Выберите **1 — inspect**. Убедитесь, что видны ADATA LEGEND 900, Samsung 980
с Windows и Samsung 860 EVO. Linux-номера дисков могут отличаться от Windows.
После просмотра снова выполните `bash install.sh` и выберите **2 — install**.

Если Ethernet недоступен, до запуска меню подключитесь через `iwctl`:

```text
iwctl
device list
station ИМЯ_УСТРОЙСТВА scan
station ИМЯ_УСТРОЙСТВА get-networks
station ИМЯ_УСТРОЙСТВА connect "SSID"
exit
```

## 3. Автоматическая установка

Скрипт спросит имя пользователя (по умолчанию `ahmed`) и часовой пояс
(по умолчанию `Europe/Moscow`, можно выбрать свой).

**До разметки** он скачает и проверит shim, найдёт EFI Windows на Samsung 980
и убедится, что там существует `bootmgfw.efi`. EFI Windows монтируется только
для чтения. Затем нужно ввести:

1. Полный путь найденного ADATA, например фактический `/dev/nvme…n1` из вывода.
2. Точную фразу `ERASE ADATA LEGEND 900`.

Путь не зашит в скрипт. Другой SSD, диск с активными точками монтирования,
USB-диск, неоднозначный набор дисков или уже существующие `ARCH_EFI/ARCH_ROOT`
блокируют разметку. При ошибке подтверждения форматирование не запускается.

После подтверждения скрипт создаст GPT и два раздела, установит систему и
попросит задать **три отдельных пароля**:

- пароль root Arch;
- пароль вашего пользователя;
- временный пароль MOK — он понадобится на синем экране при перезагрузке.

Для MOK используйте запоминаемый ASCII-пароль без русской раскладки. Это
не пароль диска; шифрование Linux не включается. На вводе обычных паролей
символы не отображаются — это нормально.

Установщик настроит `nvidia-open` для RTX 3060 Ti, микрокод AMD, NetworkManager,
синхронизацию времени, генерацию подписанного UKI и обновление подписей.
Рабочая копия этого репозитория сохранится в `/opt/arch-bspwm-dualboot`.

**Если скрипт завершился с ошибкой:** сохраните её текст и остановитесь.
Не запускайте `install` для повторного стирания. Уже установленный диск
защищён от повторной разметки; для диагностики есть `repair` ниже.

## 4. Первый запуск: MOK, затем Secure Boot ON

После сообщения об успешном завершении:

1. Выполните `reboot`, уберите установочную флешку.
2. В UEFI Boot Menu выберите **Arch rEFInd (shim)**. При необходимости поставьте
   эту запись первой в Boot Priority. Отдельный Windows Boot Manager сохраняется.
3. На синем экране MokManager выберите **Enroll MOK → Continue → Yes**,
   проверьте сертификат `Arch local boot signing`, введите временный MOK-пароль
   и выберите **Reboot**. Регистрируйте именно свой сертификат, а не случайный файл.
4. Войдите в UEFI и включите Secure Boot обратно, сохранив заводские ключи.
5. Снова загрузитесь через **Arch rEFInd (shim)** и выберите Arch Linux.

MokManager может не показываться при Secure Boot OFF на некоторых прошивках.
Тогда включите Secure Boot перед пунктом 3: подписанный shim должен открыть
MokManager для ожидающего подтверждения. Если он не открывается, используйте
прямой Windows Boot Manager и сохраните текст ошибки — ключи прошивки не стирайте.

После входа в Arch как обычный пользователь:

```bash
cd /opt/arch-bspwm-dualboot
sudo bash install.sh verify
```

Должен быть `PASS` для Secure Boot, зарегистрированного MOK, подписей UKI/rEFInd
и правильного EFI-раздела. Проверка также покажет время и `nvidia-smi`.
Если MOK не зарегистрирован, повторите запрос:

```bash
sudo bash install.sh enroll
```

Перезагрузитесь и подтвердите его в MokManager. Тема может скрывать подсказки;
стрелки и Enter выбирают ОС. Через 8 секунд загружается последняя выбранная ОС.

## 5. Установить рабочий стол одной командой

В установленном Arch, из своего пользователя, **без sudo перед запуском**:

```bash
cd /opt/arch-bspwm-dualboot && bash install.sh desktop
```

Скрипт сам вызовет sudo для pacman и попросит ваш Linux-пароль. Установит пакеты,
скачает проверенный commit dotfiles, сохранит существующие конфиги в
`~/dotfiles-backups/ДАТА/`, адаптирует монитор/звук/сеть и создаст `~/.xinitrc`.

Для первого запуска графики из локальной консоли:

```bash
startx
```

| Хоткей | Действие |
|---|---|
| Win+Enter | Терминал |
| Win+D | Приложения |
| Win+C | Закрыть окно |
| Win+1…9 | Рабочий стол |
| Win+Shift+1…9 | Перенести окно |
| Alt+Shift | Раскладка |
| Win+W | Случайные обои |
| Win+V | История буфера |
| Win+Shift+L | Блокировка |
| Print | Снимок экрана |
| Ctrl+Shift+R | Перезапуск BSPWM |
| Ctrl+Shift+Q | Выход из BSPWM |

Сначала используется TTY + `startx`. Графический экран входа не устанавливается.
Оболочка Alacritty — fish, системный login shell — Bash.

Переносится основное оформление. Блокировка сделана на обычном i3lock,
история буфера — clipmenu; автозапуск ноутбучных батареи/яркости, Redshift
и cursor tracker отключён. Telegram, VirtualBox, музыкальный сервер и другие
необязательные приложения автора не устанавливаются; их хоткеи требуют пакетов.
Выбор любимой частоты экрана можно уточнить через `xrandr --query`: `--auto`
использует предпочтительный режим монитора, не гарантирует максимальную частоту.

## 6. Проверить Windows и VALORANT

Перезагрузитесь, выберите Windows в rEFInd. В административном PowerShell:

```powershell
Confirm-SecureBootUEFI
```

Ожидается `True`. В `msinfo32`: режим BIOS — UEFI, Secure Boot — включён.
В `tpm.msc`: TPM готов, спецификация 2.0. Проверьте часы и запустите VALORANT.
Если вы приостанавливали BitLocker, после проверки загрузки возобновите его:

```powershell
.\windows.ps1 -ResumeBitLocker
```

Riot не гарантирует совместимость этой конкретной цепочки загрузки.
При ошибке VAN сначала запишите код и попробуйте загрузить Windows прямо
через **Windows Boot Manager из UEFI Boot Menu** для сравнения.
`STATUS_SB_POLICY` может означать проблему проверки базы Secure Boot;
поэтому сценарий не заменяет заводские PK/KEK/db. Подробнее в [SECUREBOOT.md](SECUREBOOT.md).

## Обновления

В установленном Arch:

```bash
sudo pacman -Syu
```

Перед обновлениями загрузочных пакетов проверяется правильный `/boot`.
После генерации initramfs создаётся и проверяется подписанный UKI;
новый rEFInd также подписывается. Пакеты NVIDIA/системного загрузочного ПО
дополнительно вызывают пересборку. Старый подписанный UKI сохраняется отдельным
пунктом меню, но это **не полный бэкап системы**: после удаления старых модулей
ядра его графика/драйверы могут не работать. Live USB сохраняйте для ремонта.

shim обслуживается отдельно: старый подписанный shim может попасть в dbx/SBAT.
Периодически проверяйте обновления, особенно перед обновлением UEFI/dbx:

```bash
cd /opt/arch-bspwm-dualboot && sudo bash install.sh refresh-shim
```

Загрузка проверяет цепочку подписанной метаинформации Ubuntu и SHA256 пакета;
Debian install scripts не выполняются. Происхождение записано в
`/etc/shim-source.json`. Если изменился ключ архива или формат пакета,
скрипт остановится; отключать проверки для продолжения не нужно.

Приватный ключ `/etc/secureboot/MOK.key` хранится только на установленном Arch,
с правами root. Не коммитьте и не отправляйте его. Для восстановления сохраните
защищённую офлайн-копию `/etc/secureboot`; срок сертификата — 10 лет.

## Восстановление

Если rEFInd/Arch перестал загружаться, Windows остаётся доступной через отдельный
Windows Boot Manager в UEFI. Для ремонта Arch временно отключите Secure Boot,
загрузите сохранённый Arch USB, подключитесь к сети, клонируйте репозиторий и:

```bash
bash install.sh repair
```

Скрипт найдёт существующие разделы ADATA и откроет root shell в них без разметки.
Если система установлена и ключи/скрипты на месте, можно пересобрать:

```bash
mkinitcpio -P
/usr/local/sbin/arch-secureboot-update
exit
```

Если ошибка была во время первой установки до настройки системы,
сохраните её текст для разбора; этот shell не завершает автоматически прерванный
pacstrap/создание пользователя/регистрацию MOK. Не форматируйте диск повторно.

Если проблема только с графикой: `Ctrl+Alt+F3`, войдите, проверьте `nvidia-smi`
и `journalctl -b -p err`. Для проверки без Picom закомментируйте его запуск
в `~/.config/bspwm/bspwmrc`.

## Проверки кода и границы проверки

```bash
python3 -B -m unittest -v test_arch_setup
bash -n install.sh
```

Тесты не обращаются к реальным дискам и не устанавливают пакеты. Они проверяют
отказы для неправильного/смонтированного/повторно устанавливаемого диска,
подтверждения, EFI Windows, проверки пакета shim, резервные копии конфигов
и синтаксис генерируемых shell-скриптов. Фактическую загрузку UEFI, работу
NVIDIA/монитора и Vanguard на этой плате нужно проверить при установке.

## Первоисточники

- [Arch Installation guide](https://wiki.archlinux.org/title/Installation_guide)
- [Dual boot with Windows](https://wiki.archlinux.org/title/Dual_boot_with_Windows)
- [Windows UTC](https://wiki.archlinux.org/title/System_time#UTC_in_Microsoft_Windows)
- [rEFInd Secure Boot](https://www.rodsbooks.com/refind/secureboot.html)
- [Unified kernel image](https://wiki.archlinux.org/title/Unified_kernel_image)
- [mkinitcpio post hooks](https://man.archlinux.org/man/mkinitcpio.8.en)
- [NVIDIA](https://wiki.archlinux.org/title/NVIDIA)
- [Ubuntu archive signing key](https://documentation.ubuntu.com/security/software-integrity/image-verification/)
- [Riot Secure Boot](https://support.riotgames.com/en-us/riot/client/secure-boot-guide)
- [Microsoft BitLocker](https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/bitlocker-overview-and-requirements-faq)
