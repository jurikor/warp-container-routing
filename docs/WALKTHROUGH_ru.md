# Walkthrough: `warp_container_routing.py`

Этот документ описывает актуальный Python-инструмент для маршрутизации исходящего трафика Docker-контейнеров через host-level Cloudflare WARP.

Основной инструмент проекта:

- `warp_container_routing.py` - CLI;
- `warp_routing/` - пакет с внутренними модулями;
- `scripts/warp_test_proxy_env.sh` - отдельное тестовое окружение.

## Идея

Инструмент делает две независимые вещи:

1. Поднимает или удаляет host-level WARP через `wgcf`.
2. Настраивает policy routing для конкретного Docker-контейнера.

Default route самого VPS не меняется. Через WARP уходит только трафик контейнера, который попал под `iptables` mark.

Упрощённая схема:

```text
Docker container
    |
    | packets from container IP
    v
iptables mangle mark
    |
    v
ip rule fwmark -> dedicated routing table
    |
    v
default route via wgcf
    |
    v
Cloudflare WARP
```

## Компоненты

### `warp_container_routing.py`

Тонкая CLI-точка входа. Она парсит аргументы и вызывает классы из библиотеки.

Основные группы команд:

- `warp`
- `route`
- `status`

### `warp_routing/`

Содержит основную логику, разделённую по зонам ответственности:

- `core.py` - `CommandRunner`, ошибки и проверки зависимостей;
- `docker.py` - `DockerInspector`;
- `network.py` - определение WAN, bridge и WARP-интерфейсов;
- `warp.py` - установка/удаление WARP;
- `routing.py` - enable/reload/disable/list/remove-orphaned;
- `check.py` - проверка правил и внешнего egress;
- `interactive.py` - простой интерактивный режим без зависимостей;
- `helper_template.py` - embedded helper и systemd unit template;
- `status.py` - вывод статуса.

### `warp-container-routing-helper`

Генерируется в:

```text
/usr/local/sbin/warp-container-routing-helper
```

Его запускает systemd service. Helper применяет и снимает маршруты, `ip rule`, `iptables mangle` и NAT.

## Интерактивный режим

Если запустить CLI без аргументов, откроется простое нумерованное меню:

```bash
sudo ./warp_container_routing.py
```

Меню позволяет выполнить status, install/uninstall WARP, enable/reload/check/disable маршрутов и remove-orphaned. Это thin wrapper над теми же менеджерами, которые используют обычные CLI-команды.

## Status

```bash
sudo ./warp_container_routing.py status
```

Status показывает WAN, WireGuard/WARP links, настроенные маршруты и orphaned routes. Orphaned route - это env-файл, у которого `CONTAINER_NAME` больше не находится через Docker.

## WARP lifecycle

### Установка

```bash
sudo ./warp_container_routing.py warp install --profile wgcf
```

Команда:

1. Проверяет root и системные зависимости.
2. Устанавливает недостающие пакеты через `apt`, `dnf` или `yum`.
3. Скачивает `wgcf`, если его нет.
4. Создаёт `/etc/wireguard/wgcf-account.toml`.
5. Создаёт `/etc/wireguard/wgcf.conf`.
6. Удаляет `DNS = ...`.
7. Добавляет `Table = off`.
8. Фиксирует IPv4 WARP endpoint.
9. Запускает `wg-quick@wgcf.service`.

`Table = off` важен: `wg-quick` не меняет default route хоста.

### Удаление

```bash
sudo ./warp_container_routing.py warp uninstall --profile wgcf
sudo ./warp_container_routing.py warp uninstall --profile wgcf --force
```

Команда:

- если есть настроенные container routes, показывает список контейнеров и просит подтверждение;
- с `--force` отключает все container routes без вопроса;
- останавливает `wg-quick@wgcf.service`;
- отключает service;
- удаляет interface `wgcf`, если он остался;
- удаляет `/etc/wireguard/wgcf.conf`;
- удаляет `/etc/wireguard/wgcf-account.toml`;
- удаляет `/usr/local/bin/wgcf`.

## Route lifecycle

### Enable

```bash
sudo ./warp_container_routing.py route enable --container my-container --warp-if wgcf
```

Команда:

1. Получает metadata контейнера через `docker inspect`.
2. Проверяет, что контейнер запущен и имеет IPv4.
3. Находит WARP-интерфейс.
4. Находит WAN route.
5. Находит Docker bridge routes.
6. Проверяет, что `SRC` контейнера ещё не встречается в host iptables rules.
7. Генерирует `MARK`, `PRIO`, `TABLE`, `CHAIN` и проверяет их на конфликты с env-файлами инструмента, host `ip rule`, routing tables и iptables chains/rules.
8. При конфликте `MARK`, `PRIO`, `TABLE` или `CHAIN` пробует другой salt; если свободный набор не найден, завершает команду ошибкой.
9. Создаёт env-файл:

```text
/etc/warp-container-routing/<instance>.env
```

10. Устанавливает helper и systemd template.
11. Включает и запускает:

```text
warp-container-routing@<instance>.service
```

Если после добавления маршрута настроено больше одного контейнера, CLI автоматически пересобирает `ROUTES`/`EXCLUDES` во всех env-файлах и выполняет `reload` всех service instance. Так старые контейнеры узнают о новых Docker bridge-сетях.

### Что хранится в env

Пример:

```env
CONTAINER_NAME=my-container
CONTAINER_ID=3ac885901142
SRC=10.255.0.2/32
TABLE=52964
MARK=0x1c8375
PRIO=11277
CHAIN=WCR_C837501153C4FEE6
WARP_IF=wgcf
WAN_IF=eth0
ROUTES='31.59.105.0/24|eth0|31.59.105.65;172.17.0.0/16|docker0|172.17.0.1'
EXCLUDES='127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 31.59.105.0/24 100.64.0.0/10'
```

`CONTAINER_NAME` используется для динамического получения актуального IP при старте или reload service.

### Systemd unit

Файл:

```text
/etc/systemd/system/warp-container-routing@.service
```

Содержит:

- `ExecStart=... helper up ...`
- `ExecReload=... helper reload ...`
- `ExecStop=... helper down ...`
- `RemainAfterExit=yes`

Service не держит постоянный процесс. Он один раз применяет host-level правила и остаётся `active (exited)`.

### Helper `up`

При `up` helper:

1. Получает текущий IPv4 контейнера через Docker.
2. Находит bridge route для этого IP.
3. Ждёт появления `WARP_IF`.
4. Пересобирает routing table.
5. Добавляет `ip rule`.
6. Создаёт/очищает `iptables mangle` chain.
7. Маркирует новые соединения от текущего IP контейнера.
8. Добавляет NAT:

```bash
iptables -t nat -A POSTROUTING -s <container-ip>/32 -o <warp-if> -j MASQUERADE
```

9. Сохраняет runtime state:

```text
/etc/warp-container-routing/<instance>.env.state
```

State нужен, чтобы `down` удалял правила именно для IP, который был реально применён.

### Reload

```bash
sudo ./warp_container_routing.py route reload --container my-container
sudo ./warp_container_routing.py route reload
```

Или напрямую:

```bash
sudo systemctl reload warp-container-routing@<instance>.service
```

Reload:

1. Снимает старые правила по `.env.state`.
2. Пересобирает `SRC`, `CONTAINER_ID`, `ROUTES` и `EXCLUDES` в env-файле.
3. Заново получает текущий IP контейнера.
4. Заново применяет routing.

Это нужно после пересоздания контейнера, если его IP изменился.

С `--container` обновляется один контейнер. Без `--container` команда читает все `/etc/warp-container-routing/*.env` и обновляет каждый настроенный service instance.

### Disable

```bash
sudo ./warp_container_routing.py route disable --container my-container
sudo ./warp_container_routing.py route disable
sudo ./warp_container_routing.py route disable --force
```

Команда:

- останавливает service;
- вызывает helper `down`;
- отключает service;
- удаляет `.env`;
- удаляет `.env.state`;
- удаляет общий helper/unit, если больше нет настроенных контейнеров.

С `--container` отключается один контейнер. Без `--container` команда показывает все настроенные контейнеры и просит подтверждение. `--force` отключает все настроенные маршруты без вопроса.

### Remove orphaned

```bash
sudo ./warp_container_routing.py route remove-orphaned
```

Команда просматривает `/etc/warp-container-routing/*.env` и удаляет конфигурации, у которых `CONTAINER_NAME` больше не существует в Docker.

## Проверка

```bash
sudo ./warp_container_routing.py route check --container my-container
sudo ./warp_container_routing.py route check
```

Проверяется:

- контейнер существует;
- env-файл найден;
- WARP-интерфейс есть;
- `ip rule` установлен;
- routing table содержит default route через WARP;
- `iptables mangle` chain есть;
- NAT `MASQUERADE` есть;
- внешний HTTP-check работает.

С `--container` проверяется один контейнер. Без `--container` команда читает все `/etc/warp-container-routing/*.env` и проверяет каждый настроенный маршрут.

Если в контейнере нет `curl`/`wget`, используется fallback через host `nsenter`:

```bash
nsenter -t <container-pid> -n curl -fsSL --max-time 15 <url>
```

## Тестовый proxy

Тестовый Squid proxy создаётся отдельным скриптом:

```bash
sudo bash scripts/warp_test_proxy_env.sh up
source scripts/.warp-test-proxy/proxy.env
```

Дальше можно подключить routing:

```bash
sudo ./warp_container_routing.py route enable --container "${CONTAINER_NAME}" --warp-if wgcf
sudo ./warp_container_routing.py route check --container "${CONTAINER_NAME}"
sudo bash scripts/warp_test_proxy_env.sh test
```

`test` делает запросы через proxy:

- `https://api.myip.com`
- `https://cloudflare.com/cdn-cgi/trace`

Ожидаемый признак успешной маршрутизации через WARP:

```text
warp=on
```

Cleanup:

```bash
sudo ./warp_container_routing.py route disable --container "${CONTAINER_NAME}"
sudo bash scripts/warp_test_proxy_env.sh down
```

## Поведение при lifecycle событиях

### Перезагрузка хоста

Если service enabled, systemd запустит `warp-container-routing@<instance>.service` и helper заново применит правила. IP контейнера будет получен через Docker на момент старта.

### Restart контейнера

Если IP не изменился, правила продолжают работать.

Если контейнер пересоздан и IP изменился, выполните:

```bash
sudo ./warp_container_routing.py route reload --container my-container
```

### Удаление контейнера

Если контейнер удалили без `route disable`, выполните:

```bash
sudo ./warp_container_routing.py route remove-orphaned
```

## Ограничения

- Основной routing сейчас IPv4-only.
- WARP endpoint принудительно патчится на IPv4 endpoint, чтобы работать на VPS без IPv6 default route.
- Инструмент управляет host-level правилами. Он не меняет конфигурацию приложения внутри контейнера.
