"""Launch a verified local retail session without owning existing helpers."""
import argparse
from dataclasses import dataclass
from datetime import datetime
import importlib
import importlib.util
import json
import math
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME_PATHS = (
    ROOT / "game" / "Overwatch" / "_retail_" / "Overwatch.exe",
    ROOT / "game" / "_retail_" / "Overwatch.exe",
    ROOT / "game" / "Overwatch.exe",
)


class LaunchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Service:
    name: str
    ports: tuple
    command: list
    cwd: Path


def timeout_seconds(value):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("Timeout must be a finite number of seconds") from None
    if not math.isfinite(seconds) or not 0.001 <= seconds < 0xFFFFFFFF / 1000:
        raise argparse.ArgumentTypeError("Timeout must be finite, at least 0.001 seconds, and below 4294967.295 seconds")
    return seconds


def select_game(game_exe=None, candidates=DEFAULT_GAME_PATHS):
    choices = [Path(game_exe)] if game_exe is not None else candidates
    for path in choices:
        if path.is_file():
            return path.resolve()
    raise LaunchError("Overwatch.exe not found. Unpack the game or pass --game-exe with its full path.")


def validate_prerequisites(root, game_exe=None):
    game = select_game(game_exe)
    dll = root / "relay" / "owwfd_relay.dll"
    if not dll.is_file():
        raise LaunchError(f"Relay DLL missing: {dll}. Build it with relay\\build.bat first.")
    if struct.calcsize("P") != 8:
        raise LaunchError("Use 64-bit Python for the x64 Overwatch client.")
    for module in ("google.protobuf", "websockets"):
        try:
            importlib.import_module(module)
        except ImportError as error:
            raise LaunchError(f"Python dependency unavailable: {module}: {error}. Install protobuf and websockets for {sys.executable}.") from error
    for relative in ("server/lobbyserv.py", "server/bnet/main.py", "relay/inject.py"):
        if not (root / relative).is_file():
            raise LaunchError(f"Required launcher component missing: {root / relative}")
    return game


def load_injector(root):
    spec = importlib.util.spec_from_file_location("retail_relay_inject", root / "relay" / "inject.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reject_existing_game(injector):
    try:
        pid = injector.find_pid()
    except RuntimeError as error:
        if str(error) == "process Overwatch.exe not found":
            return
        raise LaunchError(f"Cannot launch another client: {error}. Close existing Overwatch clients first.") from error
    raise LaunchError(f"Overwatch.exe is already running (PID {pid}). Close it before starting a new retail session.")


def services_for(root):
    return [
        Service("lobby", (3724, 3725), [sys.executable, "-u", str(root / "server" / "lobbyserv.py")], root),
        Service("bnet", (1119, 21119, 6969), [sys.executable, "-u", "-m", "bnet.main"], root / "server"),
    ]


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return True
    except OSError:
        return False


def inspect_services(services):
    records = []
    for service in services:
        reachable = [port for port in service.ports if port_open(port)]
        if reachable and len(reachable) != len(service.ports):
            missing = [port for port in service.ports if port not in reachable]
            raise LaunchError(f"Service {service.name} is partial/unhealthy: reachable {reachable}, unavailable {missing}. Close or repair the existing helper before retrying.")
        records.append({"name": service.name, "ports": list(service.ports), "state": "reused" if reachable else "missing"})
    return records


def wait_for_ports(ports, timeout, process=None):
    deadline = time.monotonic() + timeout
    while True:
        if process is not None and process.poll() is not None:
            raise LaunchError(f"Helper PID {process.pid} exited with code {process.poll()} before its ports were ready.")
        if all(port_open(port) for port in ports):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaunchError(f"Timed out waiting for local ports {list(ports)}.")
        time.sleep(min(0.05, remaining))


def wait_for_loader(game, injector, timeout):
    """Wait until the spawned PID exposes the remote LoadLibraryW owner module."""
    deadline = time.monotonic() + timeout
    last_error = "loader modules are not initialized"
    while True:
        if game.poll() is not None:
            raise LaunchError(f"Overwatch PID {game.pid} exited with code {game.poll()} before injection.")
        try:
            if injector._remote_load_library(game.pid):
                return
        except (OSError, RuntimeError) as error:
            last_error = str(error)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaunchError(f"Loader readiness timed out for Overwatch PID {game.pid}: {last_error}")
        time.sleep(min(0.025, remaining))


class ServiceManager:
    def __init__(self, services, log_dir):
        self.services = services
        self.log_dir = log_dir
        self.records = []
        self.owned = []

    def ensure(self, timeout):
        self.records[:] = inspect_services(self.services)
        try:
            for service, record in zip(self.services, self.records):
                if record["state"] == "reused":
                    print(f"Reusing {service.name} on ports {list(service.ports)}")
                    continue
                log_path = self.log_dir / f"{service.name}.log"
                with log_path.open("ab") as log:
                    process = subprocess.Popen(service.command, cwd=service.cwd, stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
                self.owned.append(process)
                record.update({"state": "owned", "pid": process.pid, "log": str(log_path)})
                print(f"Started {service.name}, PID {process.pid}; waiting for {list(service.ports)}")
                wait_for_ports(service.ports, timeout, process)
            wait_for_ports([port for service in self.services for port in service.ports], timeout)
        except Exception:
            self.stop_owned()
            raise

    def stop_owned(self):
        for process in reversed(self.owned):
            record = next(record for record in self.records if record.get("pid") == process.pid)
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                record["stopped"] = True
            except (OSError, subprocess.SubprocessError) as error:
                record["cleanup_error"] = str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Check files, Python dependencies and local service ports without launching anything")
    parser.add_argument("--game-exe", type=Path, help="Full path to Overwatch.exe")
    parser.add_argument("--timeout", type=timeout_seconds, default=30.0, help="Timeout in seconds for each readiness stage and verified injection (default: 30)")
    args = parser.parse_args(argv)
    manager = game = log_dir = None
    manifest = {"started_at": datetime.now().isoformat(), "python": sys.executable, "services": [], "status": "checking"}
    try:
        game_path = validate_prerequisites(ROOT, args.game_exe)
        injector = load_injector(ROOT)
        services = services_for(ROOT)
        states = inspect_services(services)
        if args.check_only:
            print(f"Prerequisites verified: {game_path}")
            for state in states:
                print(f"{state['name']}: {state['state']} on {state['ports']}")
            try:
                reject_existing_game(injector)
            except LaunchError as error:
                print(f"Warning: {error}")
            print("Check complete; no helpers or game were started. Reachable ports do not identify helper versions.")
            return 0
        reject_existing_game(injector)
        log_dir = ROOT / "logs" / f"launch-{datetime.now():%Y%m%d-%H%M%S-%f}"
        log_dir.mkdir(parents=True)
        manifest.update({"game_exe": str(game_path), "relay_dll": str(ROOT / "relay" / "owwfd_relay.dll"), "timeout_seconds": args.timeout})
        manager = ServiceManager(services, log_dir)
        manifest["services"] = manager.records
        manager.ensure(args.timeout)
        reject_existing_game(injector)
        command = [str(game_path), "--BNetServer=127.0.0.1:1119", "--console"]
        with (log_dir / "game.log").open("ab") as log:
            game = subprocess.Popen(command, cwd=game_path.parent, stdout=log, stderr=subprocess.STDOUT)
        manifest["game"] = {"pid": game.pid, "command": command, "owned": True, "left_running": True}
        wait_for_loader(game, injector, args.timeout)
        base = injector.inject(str(ROOT / "relay" / "owwfd_relay.dll"), game.pid, int(args.timeout * 1000))
        if not base:
            raise LaunchError("Injector did not return a verified module base.")
        if game.poll() is not None:
            raise LaunchError(f"Overwatch PID {game.pid} exited immediately after injection (code {game.poll()}).")
        manifest.update({"status": "verified", "relay_base": f"0x{base:X}"})
        print(f"Relay verified in Overwatch PID {game.pid} at 0x{base:X}. Services and game remain running.")
        print(f"Logs and ownership manifest: {log_dir}")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        detail = str(error) or "Interrupted by user"
        manifest.update({"status": "failed", "error": detail})
        if manager is not None:
            manager.stop_owned()
        if game is not None:
            manifest["game"]["left_running"] = game.poll() is None
        print(f"Retail launch failed: {detail}", file=sys.stderr)
        if game is not None and game.poll() is None:
            print(f"Overwatch PID {game.pid} was left running. Close it before retrying.", file=sys.stderr)
        if log_dir is not None:
            print(f"Diagnostics: {log_dir}", file=sys.stderr)
        return 1
    finally:
        if log_dir is not None:
            manifest["finished_at"] = datetime.now().isoformat()
            (log_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
