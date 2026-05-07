import subprocess
import uuid

import pytest

from warp_routing.helper_template import HELPER_SCRIPT


LOG_PREFIX = "[docker-wait-test]"


def info(message):
    print(f"{LOG_PREFIX} [info] {message}")


def step(message):
    print(f"{LOG_PREFIX} [step] {message}")


def warn(message):
    print(f"{LOG_PREFIX} [warn] {message}")


def _docker_available():
    completed = subprocess.run(
        ["docker", "info"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


if not _docker_available():
    pytest.skip("docker daemon is not available", allow_module_level=True)


pytestmark = pytest.mark.docker_integration

_NAMESPACE = {}
exec(HELPER_SCRIPT, _NAMESPACE)
inspect_container = _NAMESPACE["inspect_container"]
wait_for_container_ipv4 = _NAMESPACE["wait_for_container_ipv4"]


def _run(args, check=True):
    info(f"run command: {' '.join(args)}")
    completed = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{completed.stderr}")
    return completed


@pytest.fixture
def running_container():
    name = f"warp-routing-test-{uuid.uuid4().hex[:8]}"
    step(f"create integration container: {name}")
    _run(["docker", "run", "-d", "--name", name, "alpine:3.20", "sleep", "300"])
    try:
        yield name
    finally:
        step(f"cleanup integration container: {name}")
        _run(["docker", "rm", "-f", name], check=False)


def test_inspect_container_returns_running_and_ipv4(running_container):
    step(f"inspect_container() for {running_container}")
    running, health, ip = inspect_container(running_container)
    info(f"inspect result: running={running} health={health} ip={ip}")

    assert running is True
    assert health in {"none", "healthy", "starting", "unhealthy"}
    assert ip is not None
    assert ip.count(".") == 3


def test_wait_for_container_ipv4_returns_ip(running_container):
    step(f"wait_for_container_ipv4() for {running_container}")
    ip = wait_for_container_ipv4(running_container, timeout=20, interval=1, require_healthy=False)
    info(f"wait_for_container_ipv4() returned ip={ip}")

    assert ip is not None
    assert ip.count(".") == 3


def test_wait_for_container_ipv4_fails_for_missing_container():
    missing_name = f"warp-routing-missing-{uuid.uuid4().hex[:8]}"
    step(f"validate error path for missing container {missing_name}")
    warn(f"expect failure for missing container: {missing_name}")

    with pytest.raises(SystemExit) as exc:
        wait_for_container_ipv4(missing_name, timeout=3, interval=1, require_healthy=False)

    assert exc.value.code == 1
