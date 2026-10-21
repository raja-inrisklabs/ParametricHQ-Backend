"""Show the CDS jobs for every key in .env.

Run from the backend folder:

    py -3.14 cds_jobs.py
    py -3.14 cds_jobs.py e662afd1-f7b3-4f1d-aadf-1e3f3e3f97c7
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
JOBS_URL = "https://cds.climate.copernicus.eu/api/retrieve/v1/jobs"
STATUSES = ("accepted", "running", "successful", "failed", "rejected")


def load_keys() -> list[tuple[str, str]]:
    keys = []
    seen = set()
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name != "CDSAPI_KEY" and not name.startswith("CDSAPI_KEY_"):
            continue
        if value and value not in seen:
            seen.add(value)
            keys.append((name, value))
    return keys


def get_json(url: str, key: str):
    request = urllib.request.Request(
        url,
        headers={"PRIVATE-TOKEN": key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        return exc.code, body


def job_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for name in ("jobs", "items", "results"):
            rows = payload.get(name)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    return []


def job_id(row: dict) -> str:
    return str(row.get("jobID") or row.get("job_id") or row.get("id") or "")


def label(name: str, key: str) -> str:
    return f"{name} (…{key[-4:]})"


def list_jobs(name: str, key: str) -> list[dict]:
    query = urllib.parse.urlencode(
        [("limit", "100"), ("sortby", "-created")] + [("status", status) for status in STATUSES]
    )
    status, payload = get_json(f"{JOBS_URL}?{query}", key)
    if status != 200:
        print(f"{label(name, key)}: list failed ({status}) {payload}")
        return []
    return job_rows(payload)


def print_key(name: str, key: str, rows: list[dict]) -> None:
    counts = Counter(str(row.get("status") or "unknown") for row in rows)
    summary = ", ".join(f"{status}={counts[status]}" for status in sorted(counts)) or "no jobs"
    print(f"\n{label(name, key)}: {summary}")
    for row in rows[:8]:
        created = row.get("created") or row.get("created_at") or ""
        process = row.get("processID") or row.get("processId") or ""
        print(f"  {job_id(row)}  {row.get('status')}  {created}  {process}")


def find_job(keys: list[tuple[str, str]], target: str) -> None:
    print(f"\nLooking for {target}")
    for name, key in keys:
        status, payload = get_json(f"{JOBS_URL}/{target}", key)
        if status == 200 and isinstance(payload, dict):
            print(f"Found on {label(name, key)}")
            print(f"  status: {payload.get('status')}")
            print(f"  created: {payload.get('created') or payload.get('created_at')}")
            print(f"  process: {payload.get('processID') or payload.get('processId')}")
            message = payload.get("message") or payload.get("detail") or payload.get("statusMessage")
            if message:
                print(f"  message: {message}")
            result_status, result_body = get_json(f"{JOBS_URL}/{target}/results", key)
            print(f"  results: {result_status} {result_body if result_status != 200 else 'available'}")
            return
        if status not in (401, 403, 404):
            print(f"{label(name, key)}: {status} {payload}")
    print("That job was not found on any configured key.")


def main() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}")
    keys = load_keys()
    if not keys:
        raise SystemExit("No CDSAPI_KEY values found.")
    target = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    print(f"{len(keys)} keys")
    listed = []
    for name, key in keys:
        rows = list_jobs(name, key)
        listed.append((name, key, rows))
        print_key(name, key, rows)
    if target:
        owned = [
            (name, key)
            for name, key, rows in listed
            if any(job_id(row) == target for row in rows)
        ]
        if owned:
            print(f"\n{target} is listed on {label(*owned[0])}")
        find_job(keys, target)


if __name__ == "__main__":
    main()
