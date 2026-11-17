"""TOP flow control: bounded metadata, persistent process-shared request budget.

Only timestamps/counters and hashed identities are saved. No business payload,
signature, Secret, or original AppKey is persisted. The lock file is stable;
JSON replacement is atomic. Production shares the existing exchange mount.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import threading
import time

from connector_sdk import RequestDeferred


def safe_text(value, limit=500):
    value = str(value or "")[:2000]
    value = re.sub(r"(?i)(app_secret|session|sign|trace_codes?|patient\w*)[=:\s]+[^\s,;]+", r"\1=[redacted]", value)
    return re.sub(r"\d{16,}", "[redacted]", value)[:limit]


def platform_error(error):
    return {k: safe_text(error.get(k), 500 if k in ('msg', 'sub_msg') else 160)
            for k in ('code', 'msg', 'sub_code', 'sub_msg', 'request_id')
            if error.get(k) is not None}


def retry_seconds(value, *, now=None):
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(str(value))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            seconds = stamp.timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return None
    return max(1, seconds) if math.isfinite(seconds) and seconds >= 0 else None


def rate_limit_info(status, body=b'', retry_after=None):
    error = {}
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and isinstance(parsed.get('error_response'), dict):
            error = platform_error(parsed['error_response'])
    except (ValueError, TypeError, UnicodeError):
        pass
    if status != 429 and error.get('code') != '7':
        return None
    delay = retry_seconds(retry_after)
    match = re.search(r'ban will last for\s+(\d+(?:\.\d+)?)\s+more seconds', error.get('sub_msg', ''), re.I)
    if match:
        delay = max(delay or 0, float(match.group(1)))
    sub = error.get('sub_code', '')
    scope = ('origin' if status == 429 else 'app' if sub == 'accesscontrol.limited-by-app-access-count'
             else 'app_method' if sub == 'accesscontrol.limited-by-app-api-access-count'
             else 'method')
    return {'source': 'http429' if status == 429 else 'top7', 'scope': scope,
            'retry_after': delay, **error}


class SharedRequestPacer:
    """Short file transactions; long cooldowns defer jobs instead of sleeping."""

    def __init__(self, directory, origin, *, clock=None, sleep=None,
                 interval=1.5, query_interval=4.5, jitter=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256(origin.lower().encode()).hexdigest()
        self.path = self.directory / (key + '.json')
        self.lock_path = self.directory / (key + '.lock')
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.interval, self.query_interval = interval, query_interval
        self.jitter = jitter or (lambda: random.uniform(0, 0.1))
        self._thread_lock = threading.Lock()

    @contextmanager
    def _state(self):
        with self._thread_lock, self.lock_path.open('a+b') as lock:
            # Nonblocking acquisition: a failed peer never blocks a worker lease.
            if os.name == 'nt':
                import msvcrt
                if lock.tell() == 0:
                    lock.write(b'0'); lock.flush()
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise RequestDeferred(2, 'shared request budget busy') from None
            else:
                import fcntl
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RequestDeferred(2, 'shared request budget busy') from None
            try:
                try:
                    if self.path.exists():
                        if self.path.stat().st_size > 131072:
                            raise ValueError('oversize')
                        state = json.loads(self.path.read_text('utf8'))
                        if state.get('version') != 1 or not isinstance(state.get('slots'), dict):
                            raise ValueError('invalid')
                        for slot in state['slots'].values():
                            if not isinstance(slot, dict):
                                raise ValueError('invalid slot')
                            for name, number in slot.items():
                                if name not in ('next', 'until', 'strikes', 'last_limit') or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                                    raise ValueError('invalid timestamp')
                    else:
                        state = {'version': 1, 'slots': {}}
                    yield state
                    temporary = self.path.with_suffix('.tmp')
                    with temporary.open('w', encoding='utf8') as stream:
                        os.chmod(temporary, 0o600)
                        json.dump(state, stream); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                except (OSError, ValueError, TypeError, KeyError):
                    raise RequestDeferred(60, 'shared request budget unavailable') from None
            finally:
                if os.name == 'nt':
                    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _keys(app_key, method):
        app = hashlib.sha256(app_key.encode()).hexdigest()[:32]
        api = hashlib.sha256(method.encode()).hexdigest()[:32]
        return 'origin', 'app:' + app, 'method:' + api, 'pair:' + app + ':' + api

    def _barrier(self, state, keys, now):
        return max([now] + [float(state['slots'].get(k, {}).get('until', 0)) for k in keys])

    def preflight(self, app_key, method):
        now = self.clock()
        with self._state() as state:
            barrier = self._barrier(state, self._keys(app_key, method), now)
        if barrier > now:
            raise RequestDeferred(barrier - now)

    def acquire(self, app_key, method):
        keys = self._keys(app_key, method)
        while True:
            now = self.clock()
            with self._state() as state:
                barrier = self._barrier(state, keys, now)
                if barrier > now:
                    raise RequestDeferred(barrier - now)
                origin = state['slots'].setdefault('origin', {})
                pair = state['slots'].setdefault(keys[3], {})
                ready = max(now, float(origin.get('next', 0)), float(pair.get('next', 0)))
                wait = ready - now
                if wait <= 0:
                    origin['next'] = now + self.interval
                    pair['next'] = now + (self.query_interval if method.endswith('query.billstatus') else self.interval)
                    return
            if wait > 5:
                raise RequestDeferred(wait)
            self.sleep(wait)

    def cool_down(self, info, app_key, method):
        now = self.clock(); keys = self._keys(app_key, method)
        key = keys[{'origin': 0, 'app': 1, 'method': 2, 'app_method': 3}.get(info.get('scope'), 2)]
        with self._state() as state:
            slot = state['slots'].setdefault(key, {})
            strikes = int(slot.get('strikes', 0)) + 1 if now - float(slot.get('last_limit', 0)) < 3600 else 1
            base = 60 if info.get('source') == 'http429' else 180
            fallback = min(1800, base * 2 ** min(strikes - 1, 4))
            seconds = max(1, info.get('retry_after') or fallback)
            # A supplied platform wait is a minimum, never shortened by our cap.
            seconds += seconds * self.jitter()
            slot.update(until=max(float(slot.get('until', 0)), now + seconds), strikes=strikes, last_limit=now)
            return max(1, math.ceil(slot['until'] - now))


def shared_pacer(base_url):
    directory = os.environ.get('BLYB_DRUGTRACE_RATE_STATE_DIR', '').strip()
    return SharedRequestPacer(directory, base_url) if directory else None


def preflight_config(config, method):
    pacer = shared_pacer(str(config.get('dmz_base_url', '')))
    if pacer:
        pacer.preflight(str(config.get('app_key', '')), method)
