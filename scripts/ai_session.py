"""Local cross-tool work claims. Advisory only; stdlib, no services or secrets."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_id(value):
    if not ID.fullmatch(value):
        raise ValueError("ID must use letters, numbers, dot, underscore or dash")
    return value


def scope_path(root, value):
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts or not value.strip():
        raise ValueError("scope must be a non-empty workspace-relative path")
    target = (root / raw).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("scope resolves outside workspace")
    return target.relative_to(root.resolve()).as_posix().casefold() or "."


def overlaps(a, b):
    return a == "." or b == "." or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def save(path, data):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def guard(root):
    base = root / ".ai"
    base.mkdir(exist_ok=True)
    lock = base / "registry.lock"
    deadline = time.monotonic() + 10
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, json.dumps({"pid": os.getpid(), "created": now()}).encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("registry.lock busy; inspect its PID before explicit recovery; never auto-delete")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink()


def records(root):
    base = root / ".ai" / "sessions"
    if not base.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(base.glob("*.json"))]


def get_record(root, sid):
    p = root / ".ai" / "sessions" / (validate_id(sid) + ".json")
    if not p.exists():
        raise ValueError("unknown session ID")
    return p, json.loads(p.read_text(encoding="utf-8"))


def start(root, *, task, tool, model, scopes):
    validate_id(task)
    scopes = sorted(set(scope_path(root, s) for s in scopes))
    with guard(root):
        for other in records(root):
            if other["status"] != "running":
                continue
            if other["task"] == task or any(overlaps(a, b) for a in scopes for b in other["scopes"]):
                raise ValueError(f"claim conflict with {other['session_id']} ({other['tool']}; task={other['task']})")
        sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        entry = {"session_id": sid, "task": task, "tool": tool, "model": model,
                 "scopes": scopes, "status": "running", "started_at": now(),
                 "updated_at": now(), "summary": "", "handoff": None, "history": []}
        p = root / ".ai" / "sessions" / (sid + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        save(p, entry)
    return entry


def update(root, sid, *, result=None, summary, handoff=None):
    if not summary.strip():
        raise ValueError("summary is required")
    if result and not handoff:
        raise ValueError("closing a session requires an existing handoff file")
    if handoff:
        rel = Path(handoff)
        actual = (root / rel).resolve()
        if rel.is_absolute() or not actual.is_relative_to(root.resolve()) or not actual.is_file():
            raise ValueError("handoff must be an existing workspace-relative file")
        handoff = actual.relative_to(root.resolve()).as_posix()
    with guard(root):
        p, entry = get_record(root, sid)
        if entry["status"] != "running":
            raise ValueError("session is already closed; start a new session for new work")
        event = {"at": now(), "summary": summary, "result": result or "checkpoint", "handoff": handoff}
        entry["history"].append(event)
        entry.update(updated_at=event["at"], summary=summary)
        if handoff:
            entry["handoff"] = handoff
        if result:
            entry["status"] = result
            entry["ended_at"] = event["at"]
        save(p, entry)
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", type=Path, default=ROOT, help="test/alternate checkout root")
    subs = ap.add_subparsers(dest="command", required=True)
    s = subs.add_parser("start")
    s.add_argument("--task", required=True)
    s.add_argument("--tool", required=True)
    s.add_argument("--model", required=True, help="actual model or unknown; do not guess")
    s.add_argument("--scope", action="append", required=True)
    subs.add_parser("status")
    for command in ("checkpoint", "end"):
        s = subs.add_parser(command)
        s.add_argument("--session", required=True)
        s.add_argument("--summary", required=True)
        s.add_argument("--handoff", required=(command == "end"))
        if command == "end":
            s.add_argument("--result", choices=("completed", "handed_off", "cancelled"), required=True)
    s = subs.add_parser("recover")
    s.add_argument("--session", required=True)
    s.add_argument("--reason", required=True, help="explicitly checked original executor has stopped")
    s.add_argument("--handoff", required=True)
    args = ap.parse_args(argv)
    root = args.workspace.resolve()
    if not root.is_dir():
        ap.error("workspace must exist")
    try:
        if args.command == "start":
            data = start(root, task=args.task, tool=args.tool, model=args.model, scopes=args.scope)
        elif args.command == "status":
            data = records(root)
        elif args.command == "recover":
            data = update(root, args.session, result="handed_off", summary="Explicit recovery: " + args.reason,
                          handoff=args.handoff)
        else:
            data = update(root, args.session, result=getattr(args, "result", None),
                          summary=args.summary, handoff=args.handoff)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
