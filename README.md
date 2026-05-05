# WARP Container Routing

Route selected Docker container egress through a host-level Cloudflare WARP interface without changing the host default route.

The container keeps accepting inbound traffic through the VPS public IP, while its internet-bound egress is marked with `iptables`, routed through a dedicated policy routing table, and sent through WARP.

## Files

- `warp_container_routing.py` - CLI entry point.
- `warp_routing/` - package with Docker, network, WARP, routing and check logic.
- `scripts/warp_test_proxy_env.sh` - optional temporary Squid proxy test environment.
- `docs/WALKTHROUGH_ru.md` - detailed Russian walkthrough.

## Requirements

- Linux with `systemd`;
- root access;
- Docker;
- `python3`;
- `iproute2`;
- `iptables`;
- for WARP bootstrap: `curl`, `wireguard-tools`, GitHub and Cloudflare access.

## Quick Start

```bash
chmod +x warp_container_routing.py

sudo ./warp_container_routing.py
sudo ./warp_container_routing.py warp install --profile wgcf
sudo ./warp_container_routing.py route enable --container my-container --warp-if wgcf
sudo ./warp_container_routing.py route check --container my-container
sudo ./warp_container_routing.py route check
```

Disable routing and remove WARP:

```bash
sudo ./warp_container_routing.py route disable --container my-container
sudo ./warp_container_routing.py warp uninstall --profile wgcf
```

## Commands

Run the script without arguments to open a simple numbered interactive menu:

```bash
sudo ./warp_container_routing.py
```

The explicit CLI commands remain available for automation and repeatable runs.

```bash
sudo ./warp_container_routing.py warp install --profile wgcf
sudo ./warp_container_routing.py warp uninstall --profile wgcf
sudo ./warp_container_routing.py warp uninstall --profile wgcf --force

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

sudo ./warp_container_routing.py status
```

`status` shows host networking, configured routes and orphaned routes for Docker containers that no longer exist.

If configured container routes exist, `warp uninstall` lists them and asks for confirmation before disabling them. Use `--force` to disable all configured routes without prompting.

`route enable` checks host `ip rule`, routing tables and iptables for external `TABLE`, `PRIO`, `MARK`, `CHAIN` and `SRC` conflicts before creating a route. ID conflicts are avoided by trying another salt; `SRC` conflicts fail with a clear error. After adding a route, `ROUTES`/`EXCLUDES` are rebuilt and reloaded for all configured containers.

`route disable --container <name>` disables one route. `route disable` without `--container` lists all configured routes and asks for confirmation; `--force` disables all of them without prompting.

## Test Proxy

```bash
sudo bash scripts/warp_test_proxy_env.sh up
source scripts/.warp-test-proxy/proxy.env

sudo ./warp_container_routing.py route enable --container "${CONTAINER_NAME}" --warp-if wgcf
sudo ./warp_container_routing.py route check --container "${CONTAINER_NAME}"
sudo bash scripts/warp_test_proxy_env.sh test

sudo ./warp_container_routing.py route disable --container "${CONTAINER_NAME}"
sudo bash scripts/warp_test_proxy_env.sh down
```

## Installed Host Files

- `/etc/warp-container-routing/*.env`
- `/etc/warp-container-routing/*.env.state`
- `/etc/systemd/system/warp-container-routing@.service`
- `/usr/local/sbin/warp-container-routing-helper`

When WARP is bootstrapped by the CLI:

- `/etc/wireguard/wgcf.conf`
- `/etc/wireguard/wgcf-account.toml`
- `/usr/local/bin/wgcf`

## Notes

- IPv4 routing is supported. Check IPv6 separately if your containers use it.
- The host default route is not changed.
- The service stores `CONTAINER_NAME`; on service start/reload it resolves the current Docker IPv4 address dynamically.
- If a container was removed without `route disable`, run `route remove-orphaned`.

## Support

This project is shared as-is. Issues and pull requests are welcome.
