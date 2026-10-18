"""Interactive limits, VM datasets and zvols inside the installed Arch pool."""
from pathlib import Path
import re
from arch_setup import run, root_required, load_state
from zfs_support import POOL, ROOT, HOME, VM, ISO


def gib(value, minimum=1):
    if not re.fullmatch(r'[1-9][0-9]{0,3}', value) or not minimum <= int(value) <= 4096:
        raise ValueError('Размер задаётся целым числом ГиБ.')
    return int(value)


def child_name(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', value):
        raise ValueError('Имя: латинская буква, далее маленькие буквы, цифры, дефис; до 31 символа.')
    return value


def check_pool():
    root_required()
    state = load_state()
    if run(['zpool', 'get', '-H', '-o', 'value', 'guid', POOL], capture=True) != state['pool_guid']:
        raise RuntimeError('GUID pool не совпадает с установленным Arch.')
    if run(['findmnt', '-n', '-o', 'SOURCE', '--target', '/var/lib/libvirt/images'], capture=True) != VM:
        raise RuntimeError('Dataset ВМ не смонтирован в ожидаемом каталоге.')


def status():
    run(['zpool', 'list', POOL])
    run(['zfs', 'list', '-r', '-t', 'filesystem,volume', '-o',
         'name,type,used,available,quota,refreservation,mountpoint', POOL])


def resize_quota():
    datasets = {'1': ROOT, '2': HOME, '3': VM, '4': ISO}
    print('1 / — система\n2 /home\n3 ВМ (включая дочерние datasets и zvols)\n4 ISO')
    dataset = datasets.get(input('Область: ').strip())
    if not dataset:
        raise ValueError('Область не выбрана.')
    size = gib(input('Новый максимальный размер, ГиБ: ').strip())
    used = int(run(['zfs', 'get', '-Hp', '-o', 'value', 'used', dataset], capture=True))
    if size * 2**30 < used:
        raise RuntimeError('Лимит меньше уже занятого места. Сначала освободите место вручную.')
    print(f'Лимит {dataset} станет {size} ГиБ. Это верхняя граница, не гарантия свободного места.')
    if input('Введите SET: ').strip() != 'SET':
        raise RuntimeError('Отменено.')
    run(['zfs', 'set', f'quota={size}G', dataset])


def create_vm_storage(block=False):
    name = child_name(input('Имя области ВМ, например lab01: ').strip())
    size = gib(input('Размер/лимит, ГиБ: ').strip())
    dataset = VM + '/' + name
    existing = run(['zfs', 'list', '-H', '-o', 'name', '-r', VM], capture=True).splitlines()
    if dataset in existing:
        raise RuntimeError('Такое хранилище уже существует. Перезапись запрещена.')
    if block:
        print(f'Будет создан zvol {dataset}, {size} ГиБ с резервированием места.')
        if input('Введите CREATE: ').strip() != 'CREATE':
            raise RuntimeError('Отменено.')
        run(['zfs', 'create', '-V', f'{size}G', '-o', 'volblocksize=16K', dataset])
        print('Блочный диск для подключения в virt-manager: /dev/zvol/' + dataset)
        print('Он не размечен и не отформатирован. Не выполняйте mkfs на диске хоста.')
    else:
        path = Path('/var/lib/libvirt/images') / name
        if path.exists():
            raise RuntimeError('Каталог уже существует: монтирование поверх файлов запрещено.')
        print(f'Будет создан {dataset}: каталог {path}, лимит {size} ГиБ.')
        if input('Введите CREATE: ').strip() != 'CREATE':
            raise RuntimeError('Отменено.')
        # A dedicated dataset per VM makes offline snapshots independently manageable.
        run(['zfs', 'create', '-o', 'canmount=on', '-o', 'mountpoint=legacy', '-o', f'quota={size}G', dataset])
        run(['mount', '--mkdir', '-t', 'zfs', dataset, path])
        with Path('/etc/fstab').open('a') as handle:
            handle.write(f'{dataset} {path} zfs defaults 0 0\n')
        path.chmod(0o755)
        print('Хранилище готово. Размещайте raw/qcow2 файлы этой ВМ в ' + str(path))


def menu():
    check_pool()
    status()
    print('1 только статус\n2 изменить квоту\n3 создать отдельный dataset для ВМ\n4 создать блочный zvol для ВМ')
    choice = input('Действие: ').strip()
    if choice == '2':
        resize_quota()
    elif choice in ('3', '4'):
        create_vm_storage(block=choice == '4')
    elif choice != '1':
        raise RuntimeError('Действие не выбрано. Изменений нет.')


if __name__ == '__main__':
    menu()
