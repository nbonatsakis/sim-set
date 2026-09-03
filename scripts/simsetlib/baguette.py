"""Start and open the baguette farm view. Baguette itself is an external dependency."""
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8421
INSTALL_HINT = ("baguette not found on PATH. Until the name-filter change is merged upstream, build the fork:\n"
                "  git clone https://github.com/nbonatsakis/baguette ~/dev/forks/baguette\n"
                "  cd ~/dev/forks/baguette && git checkout farm-name-filter && swift build -c release\n"
                "  ln -sf ~/dev/forks/baguette/.build/release/baguette ~/.local/bin/baguette\n"
                "After it merges: brew install baguette")


def farm_url(port, set_id=None):
    base = f"http://127.0.0.1:{port}/farm"
    return base + ("?q=" + urllib.parse.quote(f"[{set_id}]") if set_id else "")


def is_running(port, opener=urllib.request.urlopen):
    try:
        opener(f"http://127.0.0.1:{port}/simulators.json", timeout=1)
        return True
    except Exception:
        return False


def supports_query_filter(port, opener=urllib.request.urlopen):
    try:
        with opener(f"http://127.0.0.1:{port}/farm/farm-filter.js", timeout=2) as response:
            return b"searchFromQuery" in response.read()
    except Exception:
        return False


def ensure_running(port, home, which=shutil.which, popen=subprocess.Popen, running=is_running, sleep=time.sleep):
    if running(port):
        return "running"
    binary = which("baguette")
    if not binary:
        return "missing"
    process = popen([binary, "serve", "--port", str(port)], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "baguette.pid").write_text(f"{process.pid}\n")
    for _ in range(40):
        sleep(0.25)
        if running(port):
            return "started"
    return "timeout"


def open_url(url, run=subprocess.run):
    run(["open", url], check=False)
