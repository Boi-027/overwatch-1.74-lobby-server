"""One-shot, manually triggered Practice Range state-acknowledgment probe.

Requires the server's research injector to filter BOTH conn and player and
honor expires_at. State 4 can lock the UI as searching; restore observed idle
state 0 by default. This script never launches the game or sends a handoff.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'server'))
from jam_codec import Schemas

SCOPE = 'state ack only; no game handoff/playable claim'
PRACTICE_VALUE = {'+0x78': 2, '+0x80': '', '+0xA8': 4}


def finite_timeout(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('Timeout must be a number.') from None
    if not math.isfinite(value) or not 0 < value <= 3600:
        raise argparse.ArgumentTypeError('Timeout: 0 to 3600 seconds, excluding 0.')
    return value


def uint64(text):
    try:
        value = int(str(text), 0)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('Token must be an integer (0x... allowed).') from None
    if not 0 <= value < (1 << 64):
        raise argparse.ArgumentTypeError('Token must fit in an unsigned 64-bit integer.')
    return value


def build_probe(schemas, token):
    token = uint64(token)
    value = schemas.empty(0x1CFB43CD, 53000)
    value['+0x78']['+0x60'] = 4
    value['+0xE8'] = token
    return {'crc': '1CFB43CD', 'msg': 53000, 'value': value}


def publish_exclusive(path, item):
    """Publish one complete JSON line atomically, failing if the target exists.

    A hard link on the same filesystem publishes already-written bytes without
    the overwrite behavior of os.replace (or POSIX os.rename).
    """
    path = Path(path)
    data = (json.dumps(item, ensure_ascii=False) + '\n').encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix='.practice-probe-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stat = os.fstat(stream.fileno())
        os.link(temporary, path)  # Atomic, exclusive publication; never replace.
        return (stat.st_dev, stat.st_ino), data
    finally:
        temporary.unlink(missing_ok=True)


def _is_request(item, player):
    return (isinstance(item, dict) and item.get('player') == player
            and item.get('crc') == 'A6E53896' and item.get('msg') == 24000
            and item.get('value') == PRACTICE_VALUE
            and type(item['value']['+0x78']) is int
            and type(item['value']['+0xA8']) is int
            and type(item.get('conn')) is int and item['conn'] > 0)


def _ack(item, player, conn):
    if not (isinstance(item, dict) and item.get('player') == player
            and type(item.get('conn')) is int and item['conn'] == conn
            and item.get('crc') == '888716D3'
            and item.get('msg') == 52903):
        return None
    value = item.get('value')
    flag = value.get('+0x78') if isinstance(value, dict) else None
    return flag if type(flag) is bool else None


def _cleanup_own(path, published):
    if published is None:
        return False
    identity, data = published
    try:
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) == identity and path.read_bytes() == data:
            path.unlink()
            return True
    except FileNotFoundError:
        pass  # The server has already consumed the request.
    return False


def _restore_idle(path, schemas, conn, player, clock, sleep, emit):
    """Queue the known idle record, without replacing another research packet."""
    reset = build_probe(schemas, 0)
    reset['value']['+0x78']['+0x60'] = 0
    reset.update(conn=conn, player=player, expires_at=time.time() + 30)
    deadline = clock() + 5
    while True:
        try:
            identity, data = publish_exclusive(path, reset)
            break
        except FileExistsError:
            if clock() >= deadline:
                emit('Could not restore state: inject.jsonl is busy with another request. Manual check needed.')
                return {'restore_status': 'blocked', 'reset': reset}
            sleep(0.05)
    emit('State-reset published. Waiting for the server to read the packet.')
    # Never remove this reset: it may be waiting for the server's next poll.
    while True:
        try:
            stat = path.stat()
            if (stat.st_dev, stat.st_ino) != identity or path.read_bytes() != data:
                emit('The reset packet was replaced by another request; restore not confirmed.')
                return {'restore_status': 'replaced', 'reset': reset}
        except FileNotFoundError:
            emit('Reset file read/deleted. Check in-game whether the search lock was released.')
            return {'restore_status': 'consumed', 'reset': reset}
        if clock() >= deadline:
            emit('Reset packet left for the server for 30 seconds. Restore not confirmed yet.')
            return {'restore_status': 'pending', 'reset': reset}
        sleep(0.05)


def run_probe(repo, player, timeout=60, token=0, *, schemas=None,
              clock=time.monotonic, sleep=time.sleep, emit=print, keep_state=False):
    """Follow only new complete log lines; return (exit_code, saved report)."""
    timeout = finite_timeout(timeout)
    token = uint64(token)
    if not isinstance(player, str) or not player.strip():
        raise ValueError('Provide a non-empty player name.')
    repo = Path(repo)
    schemas = schemas or Schemas()
    packet = build_probe(schemas, token)
    raw = schemas.encode(0x1CFB43CD, 53000, packet['value'])
    log_path, inject_path = repo / 'client_msgs.log', repo / 'inject.jsonl'
    started = clock()
    report = {'scope': SCOPE, 'player': player, 'token': token, 'timeout_seconds': timeout,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'starting',
              'request': None, 'ack_record': None, 'ack': None, 'wire_size': len(raw),
              'wire_hex': raw.hex(), 'ignored_lines': 0, 'keep_state': keep_state,
              'restore_status': 'not_needed'}
    published = None
    conn = None
    exit_code = 1
    try:
        with log_path.open('rb') as stream:
            original = os.fstat(stream.fileno())
            stream.seek(0, os.SEEK_END)
            report['start_offset'] = stream.tell()
            emit(f'Waiting for a new request from player {player}. In-game, manually click "Training -> Practice Range".')
            emit('Only the state confirmation is checked; no game-server connection is sent.')
            emit('Search state will be kept with --keep-state.' if keep_state else
                 'After the probe a state reset will be sent; cleanup may take another 5 seconds.')
            while clock() - started < timeout:
                current = log_path.stat()
                if ((current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
                        or current.st_size < stream.tell()):
                    report['status'] = 'log_changed'
                    emit('The log was replaced or truncated. Probe stopped; run it again.')
                    break
                offset = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    stream.seek(offset)
                    sleep(0.05)
                    continue
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    report['ignored_lines'] += 1
                    continue
                if conn is None:
                    if not _is_request(item, player):
                        report['ignored_lines'] += 1
                        continue
                    conn = item['conn']
                    report['request'] = item
                    packet.update(conn=conn, player=player, expires_at=time.time() + 5)
                    try:
                        published = publish_exclusive(inject_path, packet)
                    except FileExistsError:
                        report['status'] = 'inject_busy'
                        emit('inject.jsonl already exists. Another request was kept; packet not sent.')
                        break
                    report['status'] = 'waiting_ack'
                    report['injected'] = packet
                    report['published_after_seconds'] = clock() - started
                    emit(f'New request received: connection {conn}. Packet 53000 published; waiting for 52903.')
                else:
                    ack = _ack(item, player, conn)
                    if ack is None:
                        report['ignored_lines'] += 1
                        continue
                    report['ack_record'] = item
                    report['ack'] = ack
                    report['status'] = 'acknowledged' if ack else 'negative_ack'
                    exit_code = 0 if ack else 2
                    emit(f'52903 acknowledgement: {str(ack).lower()}. This is a state check, not a match start.')
                    break
            else:
                report['status'] = 'ack_timeout' if published else 'request_timeout'
                emit('Timed out: ' + ('no 52903 response.' if published else 'no new practice-range request.'))
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        exit_code = 130
        emit('Probe stopped by the user.')
    except OSError as exc:
        report['status'] = 'io_error'
        report['error'] = str(exc)
        emit(f'File operation error: {exc}')
    finally:
        try:
            report['removed_unconsumed_packet'] = _cleanup_own(inject_path, published)
            if published is not None:
                if keep_state:
                    report['restore_status'] = 'kept_by_request'
                else:
                    report.update(_restore_idle(inject_path, schemas, conn, player, clock, sleep, emit))
                    if report['restore_status'] != 'consumed':
                        exit_code = 1
        except OSError as exc:
            report['cleanup_error'] = str(exc)
            report['restore_status'] = 'error'
            emit(f'State reset not confirmed: {exc}')
            exit_code = 1
        except KeyboardInterrupt:
            report['restore_status'] = 'interrupted'
            emit('Waiting for reset interrupted. Check the search lock; the published reset was not deleted.')
            exit_code = 130
        report['elapsed_seconds'] = clock() - started
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        directory = repo / 'logs' / 'probes'
        directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex + '.json'
        destination = directory / name
        report['report_path'] = str(destination)
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        emit(f'Report: {destination}')
    return exit_code, report


def main(argv=None):
    # Windows redirected output may use a legacy code page that cannot encode
    # the Russian instructions and arrow in the manual interaction prompt.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    parser = argparse.ArgumentParser(description='Manual state-confirmation probe for the practice range.')
    parser.add_argument('--player', required=True, help='Exact player name from client_msgs.log')
    parser.add_argument('--timeout', type=finite_timeout, default=60, help='Overall timeout in seconds (default 60)')
    parser.add_argument('--token', type=uint64, default=0, help='Verified 53000 comparison token, default 0')
    parser.add_argument('--keep-state', action='store_true',
                        help='Do not reset state to 0: only for a coordinated next probe; may leave search locked')
    args = parser.parse_args(argv)
    if not args.player.strip():
        parser.error('--player cannot be empty')
    try:
        return run_probe(REPO_ROOT, args.player, args.timeout, args.token, keep_state=args.keep_state)[0]
    except (OSError, ValueError) as exc:
        print(f'Probe failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
