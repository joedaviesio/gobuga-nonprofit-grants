"""Shared pytest fixtures.

`backend` gives integration tests a live FastAPI backend at API_URL
(default http://localhost:8102 — the project's registered backend port):

- If something healthy is already listening there (e.g. the operator's dev
  server), it is reused as-is.
- Otherwise a fresh uvicorn process is booted for the session against a
  throwaway GOBUGA_DATA_DIR, so test orgs never land in the repo's `orgs/`
  or `platform/`. The startup sweep is disabled so no paid sweep is
  triggered by the empty data dir.

Set GOBUGA_TEST_KEEP_DATA=1 to keep the temp data dir (and backend.log) for
post-mortems.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = os.environ.get("API_URL", "http://localhost:8102")
BOOT_TIMEOUT_S = 30


def _healthy(url: str, timeout: float = 1.0) -> bool:
    try:
        return requests.get(f"{url}/api/health", timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def _tail(path: str, lines: int = 40) -> str:
    try:
        with open(path) as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return "(no log)"


@pytest.fixture(scope="session")
def backend():
    """Base URL of a live backend; boots one if nothing is listening."""
    if _healthy(API_URL):
        print(f"\n[backend] reusing server at {API_URL}")
        yield API_URL
        return

    if "API_URL" in os.environ:
        pytest.fail(f"API_URL={API_URL} is set but not responding at /api/health")

    port = urlparse(API_URL).port or 8102
    data_dir = tempfile.mkdtemp(prefix="gobuga-test-")

    # Give the server the committed pools so search endpoints have data.
    src_cycles = os.path.join(PROJECT_ROOT, "platform", "cycles")
    if os.path.isdir(src_cycles):
        shutil.copytree(src_cycles, os.path.join(data_dir, "platform", "cycles"))

    env = {
        **os.environ,
        "GOBUGA_DATA_DIR": data_dir,
        "GOBUGA_COUNTRY": os.environ.get("GOBUGA_COUNTRY", "nz"),
        "STARTUP_SWEEP_DISABLED": "1",
    }
    log_path = os.path.join(data_dir, "backend.log")
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    deadline = time.time() + BOOT_TIMEOUT_S
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if _healthy(API_URL):
            ready = True
            break
        time.sleep(0.25)

    if not ready:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        pytest.fail(
            f"backend did not become healthy on {API_URL} within {BOOT_TIMEOUT_S}s\n"
            f"--- {log_path} ---\n{_tail(log_path)}"
        )

    print(f"\n[backend] booted pid={proc.pid} on {API_URL}, data_dir={data_dir}")
    try:
        yield API_URL
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if os.environ.get("GOBUGA_TEST_KEEP_DATA") == "1":
            print(f"\n[backend] kept data dir: {data_dir}")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)
