"""Lifecycle of one real local game-server process per requested session."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


@dataclass
class MatchInstance:
    key: str
    player: str
    mode: int
    directory: Path
    process: subprocess.Popen
    port: int


class MatchManager:
    def __init__(self, directory, base_port=3730, startup_timeout=5):
        if not 0 <= base_port <= 65535 or not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError('Invalid game-server port or startup timeout')
        self.directory = Path(directory)
        self.base_port = base_port
        self.startup_timeout = startup_timeout
        self.instances = {}
        self.lock = threading.RLock()

    @staticmethod
    def _stop(instance):
        process = instance.process
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def request(self, key, player, mode, activity='queue'):
        if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode < 1 << 64:
            raise ValueError('Invalid game mode')
        key = str(key)
        with self.lock:
            current = self.instances.get(key)
            if current is not None and current.mode == mode and current.process.poll() is None:
                return current
            self.cancel(key)
            port = self.base_port
            used = {i.port for i in self.instances.values() if i.process.poll() is None}
            while port and port in used:
                port += 1
            if port > 65535:
                raise RuntimeError('No game-server ports available')
            directory = self.directory / uuid.uuid4().hex
            directory.mkdir(parents=True)
            command = [sys.executable, '-B', '-u', str(Path(__file__).with_name('game_instance.py')),
                       '--directory', str(directory.resolve()), '--host', '127.0.0.1',
                       '--port', str(port), '--player', player, '--mode', hex(mode),
                       '--activity', activity, '--control-stdin']
            with (directory / 'process.log').open('wb') as log:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            instance = MatchInstance(key, player, mode, directory, process, port)
            deadline = time.monotonic() + self.startup_timeout
            try:
                while time.monotonic() < deadline:
                    state_path = directory / 'state.json'
                    if state_path.is_file():
                        state = json.loads(state_path.read_text(encoding='utf-8'))
                        if state['state'] == 'listening' and process.poll() is None:
                            instance.port = state['port']
                            self.instances[key] = instance
                            return instance
                        if state['state'] == 'failed':
                            raise RuntimeError('Game-server startup failed: ' + state.get('error', 'unknown error'))
                    if process.poll() is not None:
                        raise RuntimeError(f'Game server exited during startup: {process.returncode}; {directory}')
                    time.sleep(0.02)
                raise RuntimeError(f'Game-server startup timed out; {directory}')
            except BaseException:
                self._stop(instance)
                raise

    def cancel(self, key, mode=None):
        with self.lock:
            instance = self.instances.get(str(key))
            if instance is not None and mode is not None and instance.mode != mode:
                return
            if instance is not None:
                self.instances.pop(str(key))
                self._stop(instance)

    def snapshot(self):
        with self.lock:
            result = []
            for instance in self.instances.values():
                try:
                    state = json.loads((instance.directory / 'state.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    state = {'pid': instance.process.pid, 'player': instance.player,
                             'port': instance.port, 'mode': hex(instance.mode), 'state': 'unknown',
                             'protocol_ready': False}
                if instance.process.poll() is not None:
                    state['state'] = 'stopped' if instance.process.returncode == 0 else 'failed'
                result.append(state)
            return result

    def close(self):
        with self.lock:
            for key in list(self.instances):
                self.cancel(key)
