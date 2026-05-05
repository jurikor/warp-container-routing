#!/usr/bin/env bash
set -euo pipefail

# Optional helper: start/stop a temporary Squid proxy container with Basic Auth.
# This is intentionally NOT part of warp_container_routing.py.

usage() {
  cat <<'EOF'
Usage:
  sudo bash scripts/warp_test_proxy_env.sh up
  sudo bash scripts/warp_test_proxy_env.sh test
  sudo bash scripts/warp_test_proxy_env.sh down

Notes:
  - Requires docker, openssl and python3 on the host.
  - up generates container name, credentials and localhost port.
  - Generated values are saved to scripts/.warp-test-proxy/proxy.env by default.
  - test reads that env-file and checks proxy connectivity without WARP routing.
  - down reads that env-file and removes the generated container, network and files.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run as root"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${WARP_TEST_PROXY_STATE_DIR:-${SCRIPT_DIR}/.warp-test-proxy}"
ENV_FILE="${WARP_TEST_PROXY_ENV_FILE:-${STATE_DIR}/proxy.env}"
IMAGE="${WARP_TEST_PROXY_IMAGE:-ubuntu/squid:latest}"
TEST_URL="${WARP_TEST_PROXY_TEST_URL:-https://api.myip.com}"
TRACE_URL="${WARP_TEST_PROXY_TRACE_URL:-https://cloudflare.com/cdn-cgi/trace}"

squid_conf() {
  cat <<'EOF'
auth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/passwd
auth_param basic realm WARP test proxy
acl authenticated proxy_auth REQUIRED
http_access allow authenticated
http_access deny all
http_port 3128
access_log stdio:/var/log/squid/access.log
cache_log /var/log/squid/cache.log
coredump_dir /var/spool/squid
EOF
}

cmd_up() {
  [[ "$#" -eq 0 ]] || die "up does not accept arguments"

  require_root
  require_cmd docker
  require_cmd openssl
  require_cmd python3

  [[ ! -f "${ENV_FILE}" ]] || die "env-file already exists: ${ENV_FILE}; run down first"
  mkdir -p "${STATE_DIR}"
  chmod 700 "${STATE_DIR}"

  local suffix name user password port network tmpdir
  suffix="$(openssl rand -hex 4)"
  name="warp-test-proxy-${suffix}"
  user="proxy_${suffix}"
  password="$(openssl rand -hex 16)"
  port="$(python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
  network="${name}-net"
  tmpdir="${STATE_DIR}/runtime"

  mkdir -p "${tmpdir}"
  chmod 700 "${tmpdir}"

  local passwd_hash
  passwd_hash="$(openssl passwd -apr1 "${password}")"
  printf '%s:%s\n' "${user}" "${passwd_hash}" > "${tmpdir}/passwd"
  chmod 644 "${tmpdir}/passwd"

  squid_conf > "${tmpdir}/squid.conf"
  chmod 644 "${tmpdir}/squid.conf"

  docker network create "${network}" >/dev/null 2>&1 || true
  docker rm -f "${name}" >/dev/null 2>&1 || true

  docker run -d \
    --name "${name}" \
    --network "${network}" \
    -p "127.0.0.1:${port}:3128" \
    -v "${tmpdir}/squid.conf:/etc/squid/squid.conf:ro" \
    -v "${tmpdir}/passwd:/etc/squid/passwd:ro" \
    "${IMAGE}" >/dev/null

  write_env_file "${ENV_FILE}" \
    "CONTAINER_NAME=${name}" \
    "NETWORK_NAME=${network}" \
    "PROXY_USER=${user}" \
    "PROXY_PASSWORD=${password}" \
    "PROXY_PORT=${port}" \
    "PROXY_URL=http://${user}:${password}@127.0.0.1:${port}" \
    "IMAGE=${IMAGE}" \
    "TMPDIR=${tmpdir}"

  printf 'Env file: %s\n' "${ENV_FILE}"
  printf 'Test proxy container is running: %s\n' "${name}"
  printf 'Proxy URL: http://%s:%s@127.0.0.1:%s\n' "${user}" "${password}" "${port}"
  printf 'Enable routing:\n  sudo ./warp_container_routing.py route enable --container %s --warp-if wgcf\n' "${name}"
  printf 'Check proxy only:\n  sudo bash scripts/warp_test_proxy_env.sh test\n'
  printf 'Cleanup:\n  sudo bash scripts/warp_test_proxy_env.sh down\n'
}

load_env() {
  [[ -f "${ENV_FILE}" ]] || die "env-file not found: ${ENV_FILE}"
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  [[ -n "${CONTAINER_NAME:-}" && -n "${PROXY_URL:-}" && -n "${PROXY_PORT:-}" ]] || die "env-file is incomplete: ${ENV_FILE}"
}

cmd_test() {
  [[ "$#" -eq 0 ]] || die "test does not accept arguments"

  require_cmd docker
  require_cmd curl
  load_env

  if ! docker ps --filter "name=^/${CONTAINER_NAME}$" --filter "status=running" --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    die "container is not running: ${CONTAINER_NAME}"
  fi

  printf 'Container: %s\n' "${CONTAINER_NAME}"
  printf 'Proxy URL: %s\n' "${PROXY_URL}"
  printf 'Test URL: %s\n' "${TEST_URL}"
  printf 'Trace URL: %s\n' "${TRACE_URL}"

  local attempt ok=0
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS -x "${PROXY_URL}" "${TEST_URL}"; then
      printf '\n'
      ok=1
      break
    fi
    sleep 1
  done

  if [[ "${ok}" != "1" ]]; then
    printf 'Proxy test: failed\n' >&2
    return 1
  fi

  printf '\nCloudflare trace:\n'
  curl -fsS -x "${PROXY_URL}" "${TRACE_URL}"
  printf '\nProxy test: ok\n'
  return 0
}

cmd_down() {
  [[ "$#" -eq 0 ]] || die "down does not accept arguments"

  require_root
  require_cmd docker

  load_env
  [[ -n "${NETWORK_NAME:-}" && -n "${TMPDIR:-}" ]] || die "env-file is incomplete: ${ENV_FILE}"

  docker rm -fv "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
  rm -rf "${TMPDIR}" || true
  rm -f "${ENV_FILE}" || true
  rmdir "${STATE_DIR}" >/dev/null 2>&1 || true

  printf 'Test proxy removed: %s\n' "${CONTAINER_NAME}"
}

write_env_file() {
  local path="$1"
  shift
  mkdir -p "$(dirname -- "${path}")"
  : > "${path}"
  chmod 600 "${path}"
  local entry key value
  for entry in "$@"; do
    key="${entry%%=*}"
    value="${entry#*=}"
    printf 'export %s=' "${key}" >> "${path}"
    printf '%q\n' "${value}" >> "${path}"
  done
}

main() {
  local action="${1:-}"
  shift || true
  case "${action}" in
    -h|--help|help) usage; return 0 ;;
    up) cmd_up "$@" ;;
    test) cmd_test "$@" ;;
    down) cmd_down "$@" ;;
    *) usage; die "expected up|test|down" ;;
  esac
}

main "$@"
