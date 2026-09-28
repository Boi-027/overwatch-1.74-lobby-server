"""An owned game-server process and UDP capture endpoint.

This is the first server-runtime stage. It does not fabricate the still-unknown
Overwatch game handshake or claim to simulate a playable world.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def write_state(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def run(directory, host, port, player, mode, control_stdin=False, activity='queue'):
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'state.json'
    stop = threading.Event()
    if control_stdin:
        def parent_lifetime():
            # Closing the manager's pipe (including parent crash) owns cleanup.
            sys.stdin.buffer.read()
            stop.set()
        threading.Thread(target=parent_lifetime, daemon=True).start()
    state = {'id': directory.name, 'pid': os.getpid(), 'player': player,
             'mode': f'0x{mode:016X}', 'host': host, 'port': port,
             'activity': activity, 'state': 'starting', 'protocol_ready': False, 'packets_received': 0,
             'bytes_received': 0, 'started_at': time.time(), 'last_packet_at': None}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
        except OSError as error:
            state.update(state='failed', error=str(error))
            write_state(state_path, state)
            return 1
        state.update(port=sock.getsockname()[1], state='listening')
        write_state(state_path, state)
        sock.settimeout(0.2)
        with (directory / 'packets.jsonl').open('a', encoding='utf-8', buffering=1) as log:
            while not stop.is_set():
                try:
                    data, peer = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                state['packets_received'] += 1
                state['bytes_received'] += len(data)
                state['last_packet_at'] = time.time()
                log.write(json.dumps({'time': state['last_packet_at'], 'peer': list(peer),
                                      'bytes': len(data), 'hex': data.hex()}) + '\n')
                write_state(state_path, state)
    state.update(state='stopped', stopped_at=time.time())
    write_state(state_path, state)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--player', required=True)
    parser.add_argument('--mode', type=lambda s: int(s, 0), required=True)
    parser.add_argument('--control-stdin', action='store_true')
    parser.add_argument('--activity', choices=('queue', 'practice'), default='queue')
    args = parser.parse_args()
    return run(args.directory, args.host, args.port, args.player, args.mode, args.control_stdin, args.activity)


if __name__ == '__main__':
    raise SystemExit(main())
