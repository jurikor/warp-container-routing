import subprocess
import uuid

import pytest

from warp_routing.core import CommandRunner
from warp_routing.docker import DockerInspector


LOG_PREFIX = "[docker-inspector-test]"


def info(message):
    print(f"{LOG_PREFIX} [info] {message}")


def step(message):
    print(f"{LOG_PREFIX} [step] {message}")


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
def docker_inspector():
    return DockerInspector(CommandRunner())


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


def test_inspect_returns_container_metadata(docker_inspector, running_container):
    step(f"inspect container metadata for {running_container}")
    info_obj = docker_inspector.inspect(running_container)
    info(
        "inspect result: "
        f"name={info_obj.name} id={info_obj.container_id} "
        f"running={info_obj.running} ip={info_obj.ipv4} pid={info_obj.pid}"
    )

    assert info_obj.name == running_container
    assert info_obj.container_id
    assert info_obj.running is True
    assert info_obj.ipv4.count(".") == 3
    assert isinstance(info_obj.networks, list)


def test_has_command_reports_existing_and_missing(docker_inspector, running_container):
    step(f"check commands in container {running_container}")
    has_sh = docker_inspector.has_command(running_container, "sh")
    has_missing = docker_inspector.has_command(
        running_container, "warp-container-routing-definitely-missing-cmd"
    )
    info(f"has_command('sh')={has_sh}")
    info(f"has_command('missing')={has_missing}")

    assert has_sh is True
    assert has_missing is False


def test_exists_reports_true_for_running_false_for_missing(
    docker_inspector, running_container
):
    missing_name = f"warp-routing-missing-{uuid.uuid4().hex[:8]}"
    step(
        "check exists() for running and missing containers: "
        f"{running_container} / {missing_name}"
    )
    exists_running = docker_inspector.exists(running_container)
    exists_missing = docker_inspector.exists(missing_name)
    info(f"exists('{running_container}')={exists_running}")
    info(f"exists('{missing_name}')={exists_missing}")

    assert exists_running is True
    assert exists_missing is False


def test_running_containers_includes_test_container(docker_inspector, running_container):
    step(f"list running containers and verify presence of {running_container}")
    items = docker_inspector.running_containers()
    names = [item["name"] for item in items]
    info(f"running_containers count={len(items)}")
    info(f"first containers={names[:5]}")

    assert running_container in names
