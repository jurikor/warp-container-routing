# WARP Container Routing

Утилита для маршрутизации исходящего трафика выбранных Docker-контейнеров через host-level Cloudflare WARP без изменения default route самого VPS.

Сценарий:

- контейнер продолжает принимать входящие подключения через обычный IP сервера;
- исходящий трафик контейнера маркируется через `iptables`;
- marked traffic уходит в отдельную routing table;
- default route этой table указывает на WARP-интерфейс;
- внешний сервис видит Cloudflare/WARP IP, а не IP VPS.

## Основные файлы

- `warp_container_routing.py` - CLI-точка входа.
- `warp_routing/` - пакет с логикой Docker/network/WARP/routing/check.
- `scripts/warp_test_proxy_env.sh` - отдельный helper для создания временного Squid proxy-контейнера с Basic Auth.
- `docs/WALKTHROUGH_ru.md` - подробный walkthrough.

## Возможности

- установить и настроить WARP-профиль через `wgcf`;
- удалить WARP-профиль;
- включить маршрутизацию указанного Docker-контейнера через WARP;
- обновить маршрутизацию после пересоздания контейнера;
- отключить маршрутизацию контейнера;
- удалить конфигурации контейнеров, которых уже нет в Docker;
- проверить `ip rule`, routing table, `iptables`, NAT и внешний IP.

## Требования

- Linux с `systemd`;
- root-доступ;
- Docker;
- `python3`;
- `iproute2`;
- `iptables`;
- для WARP bootstrap: `curl`, `wireguard-tools`, доступ к GitHub и Cloudflare.

## Быстрый старт

```bash
chmod +x warp_container_routing.py

sudo ./warp_container_routing.py
sudo ./warp_container_routing.py warp install --profile wgcf
sudo ./warp_container_routing.py route enable --container my-container --warp-if wgcf
sudo ./warp_container_routing.py route check --container my-container
sudo ./warp_container_routing.py route check
```

Отключение:

```bash
sudo ./warp_container_routing.py route disable --container my-container
sudo ./warp_container_routing.py warp uninstall --profile wgcf
```

## Команды CLI

Без аргументов скрипт запускает простой интерактивный режим с нумерованным меню:

```bash
sudo ./warp_container_routing.py
```

Все команды ниже остаются доступны для автоматизации и повторяемых сценариев.

### WARP

```bash
sudo ./warp_container_routing.py warp install --profile wgcf
sudo ./warp_container_routing.py warp uninstall --profile wgcf
sudo ./warp_container_routing.py warp uninstall --profile wgcf --force
```

`warp install` создаёт `/etc/wireguard/wgcf.conf`, добавляет `Table = off`, фиксирует IPv4 endpoint Cloudflare WARP и запускает `wg-quick@wgcf.service`.

`warp uninstall` останавливает и отключает `wg-quick@<profile>.service`, удаляет профиль, `wgcf-account.toml` и `/usr/local/bin/wgcf`. Если есть настроенные container routes, команда сначала покажет список контейнеров и попросит подтверждение. `--force` отключает все container routes без вопроса.

### Routing

```bash
sudo ./warp_container_routing.py route enable --container my-container --warp-if wgcf
sudo ./warp_container_routing.py route reload --container my-container
sudo ./warp_container_routing.py route reload
sudo ./warp_container_routing.py route check --container my-container
sudo ./warp_container_routing.py route check
sudo ./warp_container_routing.py route disable --container my-container
sudo ./warp_container_routing.py route disable
sudo ./warp_container_routing.py route disable --force
sudo ./warp_container_routing.py route list
sudo ./warp_container_routing.py route remove-orphaned
```

`route enable` создаёт env-файл и systemd instance для контейнера. Перед созданием проверяются внешние конфликты с host `ip rule`, routing tables и iptables для `TABLE`, `PRIO`, `MARK`, `CHAIN` и `SRC`; свободные IDs подбираются автоматически через salt, а конфликт по `SRC` завершается понятной ошибкой. После добавления нового маршрута `ROUTES`/`EXCLUDES` автоматически пересобираются и перезагружаются для всех настроенных контейнеров.

`route reload --container <name>` пересобирает `ROUTES`/`EXCLUDES`, пере-применяет правила для одного контейнера и заново получает его текущий IP через Docker. `route reload` без `--container` делает это для всех настроенных маршрутов.

`route check --container <name>` проверяет один контейнер, а `route check` без `--container` проверяет все настроенные маршруты.

`route disable --container <name>` отключает один контейнер. `route disable` без `--container` показывает список всех настроенных контейнеров и просит подтверждение; `--force` отключает все без вопроса.

`route remove-orphaned` удаляет routing-конфигурации для контейнеров, которых больше нет.

### Status

```bash
sudo ./warp_container_routing.py status
```

Показывает WAN, WireGuard/WARP links, список настроенных маршрутов и orphaned routes для контейнеров, которых больше нет в Docker.

## Что создаётся на хосте

- `/etc/warp-container-routing/*.env`
- `/etc/warp-container-routing/*.env.state`
- `/etc/systemd/system/warp-container-routing@.service`
- `/usr/local/sbin/warp-container-routing-helper`

Если WARP ставится через CLI:

- `/etc/wireguard/wgcf.conf`
- `/etc/wireguard/wgcf-account.toml`
- `/usr/local/bin/wgcf`

## Тестовый proxy-контейнер

Тестовый контейнер вынесен в отдельный скрипт и не является частью основного CLI.

```bash
sudo bash scripts/warp_test_proxy_env.sh up
source scripts/.warp-test-proxy/proxy.env

sudo ./warp_container_routing.py route enable --container "${CONTAINER_NAME}" --warp-if wgcf
sudo ./warp_container_routing.py route check --container "${CONTAINER_NAME}"
sudo bash scripts/warp_test_proxy_env.sh test

sudo ./warp_container_routing.py route disable --container "${CONTAINER_NAME}"
sudo bash scripts/warp_test_proxy_env.sh down
```

`test` делает запросы через proxy к `https://api.myip.com` и `https://cloudflare.com/cdn-cgi/trace`.

## Примечания

- Поддерживается IPv4 routing. IPv6 traffic нужно проверять отдельно.
- Default route хоста не меняется.
- Для контейнера сохраняется `CONTAINER_NAME`; при старте/reload service актуальный IP берётся через Docker.
- Если контейнер удалён без `route disable`, используйте `route remove-orphaned`.
- Для подробного разбора см. `docs/WALKTHROUGH_ru.md`.

## Поддержка

Проект публикуется as-is. Issues и pull requests приветствуются.
