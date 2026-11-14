"""Save and review local OpenRouter example runs."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time


RUNS_ROOT = Path(__file__).resolve().parents[1] / "logs" / "openrouter-examples"
FREE_MODEL = "openrouter/free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _save(path, content):
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def _save_manifest(record_dir, entry):
    _save(record_dir / "runs.json", (json.dumps([entry], indent=2) + "\n").encode("utf-8"))


def start_run(example):
    RUNS_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record_dir = Path(tempfile.mkdtemp(prefix=f"{stamp}-", dir=RUNS_ROOT))
    entry = {
        "example": example["id"],
        "title": example["title"],
        "started_utc": _now(),
        "exit_code": None,
        "workspace": "workspace",
    }
    (record_dir / "calls.jsonl").touch()
    _save_manifest(record_dir, entry)
    return record_dir, entry


def finish_run(record_dir, entry, exit_code, draft_path):
    entry = dict(entry, finished_utc=_now(), exit_code=exit_code)
    try:
        if draft_path is not None and draft_path.is_file() and draft_path.with_suffix(".json").is_file():
            provenance = draft_path.with_suffix(".json").read_bytes()
            draft = json.loads(provenance)
            if draft.get("schema") == "looping-box.execution-draft.v1":
                number = entry["example"]
                _save(record_dir / f"{number}-draft.md", draft_path.read_bytes())
                _save(record_dir / f"{number}-draft.json", provenance)
                entry.update(
                    draft=f"{number}-draft.md",
                    draft_provenance=f"{number}-draft.json",
                    models=sorted({item["model"] for item in draft["items"] if "model" in item}),
                    held_for_review=draft["held_for_review"],
                )
    finally:
        _save_manifest(record_dir, entry)


def _append(path, event):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")
        file.flush()
        os.fsync(file.fileno())


def _number(value):
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return value
    return None


def _response_metadata(raw, model):
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}

    key = os.environ.get("OPENROUTER_API_KEY")

    def safe_text(value):
        return model._redact(value, key) if isinstance(value, str) else None

    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return {
        "response_id": safe_text(payload.get("id")),
        "returned_model": safe_text(payload.get("model")),
        "provider": safe_text(payload.get("provider")),
        "prompt_tokens": _number(usage.get("prompt_tokens")),
        "completion_tokens": _number(usage.get("completion_tokens")),
        "reported_cost": _number(usage.get("cost")),
        "provider_error_code": safe_text(code) if isinstance(code, str) else _number(code),
    }


def run_recorded(record_dir, example):
    from looping_box import cli, model

    log = record_dir / "calls.jsonl"
    transport = model._transport

    def traced_transport(url, headers, body, timeout):
        request = json.loads(body)
        if url != ENDPOINT or request.get("model") != FREE_MODEL or "models" in request:
            raise ValueError("Example logging requires OpenRouter's free router")
        call = {"example": example, "role": "execution_engine", "endpoint": url, "requested_model": FREE_MODEL}
        _append(log, dict(call, event="request_started", at_utc=_now()))
        started = time.monotonic()
        try:
            raw = transport(url, headers, body, timeout)
        except Exception as error:
            _append(log, dict(
                call,
                event="request_failed",
                at_utc=_now(),
                elapsed_seconds=round(time.monotonic() - started, 3),
                http_status=_number(getattr(error, "code", None)),
                error_type=type(error).__name__,
            ))
            raise
        _append(log, dict(
            call,
            event="response_received",
            at_utc=_now(),
            elapsed_seconds=round(time.monotonic() - started, 3),
            **_response_metadata(raw, model),
        ))
        return raw

    model._transport = traced_transport
    try:
        return cli.main(["run"])
    finally:
        model._transport = transport


def _read_calls(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], False

    calls = []
    complete = True
    for line in lines:
        try:
            call = json.loads(line)
        except ValueError:
            complete = False
            continue
        if isinstance(call, dict):
            calls.append(call)
        else:
            complete = False
    return calls, complete


def _cost(calls, complete, exit_code):
    requests = sum(call.get("event") == "request_started" for call in calls)
    costs = []
    for call in calls:
        cost = _number(call.get("reported_cost"))
        if call.get("event") == "response_received" and cost is not None:
            costs.append(Decimal(str(cost)))
    if costs:
        total = sum(costs)
        amount = f"${total:f}" if total else "$0.00"
        if not complete or len(costs) != requests:
            return f"{amount} reported so far"
        return amount
    if complete and not calls and exit_code is not None:
        return "no request"
    return "cost unavailable"


def _saved_draft(record_dir, entry):
    name = entry.get("draft")
    if not isinstance(name, str) or not name:
        return None
    path = (record_dir / name).resolve()
    return path if path.is_relative_to(record_dir) and path.is_file() else None


def show_runs(number=None):
    if number is not None:
        number = str(number).zfill(2)
    records = []
    root = RUNS_ROOT.resolve()
    for manifest in root.glob("*/runs.json"):
        record_dir = manifest.parent.resolve()
        if record_dir.parent != root or manifest.resolve().parent != record_dir:
            continue
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"Could not read saved run {manifest}", file=sys.stderr)
            continue
        if not isinstance(entries, list):
            continue
        calls, complete = _read_calls(record_dir / "calls.jsonl")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("started_utc"), str):
                continue
            example = entry.get("example")
            if number is None or number == example:
                records.append((record_dir, entry, [call for call in calls if call.get("example") == example], complete))
    if not records:
        print("No matching local OpenRouter example runs.")
        return 0

    records.sort(key=lambda record: record[1]["started_utc"], reverse=True)
    for record_dir, entry, calls, complete in records[:1] if number is not None else records:
        exit_code = entry.get("exit_code")
        if exit_code is None:
            status = "in progress or interrupted"
        else:
            status = f"exit {exit_code}" if exit_code else "complete"
        models = sorted({call["returned_model"] for call in calls if isinstance(call.get("returned_model"), str)})
        if not models and isinstance(entry.get("models"), list):
            models = [name for name in entry["models"] if isinstance(name, str)]
        print(f"{entry['started_utc']}  {entry.get('example', '')}  {entry.get('title', '')}  ({status})")
        print(f"  Model {', '.join(models) or 'unavailable'} · {_cost(calls, complete, exit_code)}")
        draft = _saved_draft(record_dir, entry)
        if draft is not None:
            print(f"  Draft {draft}")
            if number is not None:
                try:
                    print(f"\n{draft.read_text(encoding='utf-8').rstrip()}")
                except (OSError, ValueError):
                    print(f"Could not read saved draft {draft}", file=sys.stderr)
        else:
            print(f"  Record {record_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Run OpenRouter examples through examples.py --free.")
    directory = Path(sys.argv[1]).resolve()
    if directory.parent != RUNS_ROOT.resolve() or not (directory / "runs.json").is_file():
        raise SystemExit("Invalid local example run directory.")
    raise SystemExit(run_recorded(directory, sys.argv[2]))
