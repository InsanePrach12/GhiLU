"""
Local validation: POSTs every case.input from the public sample-cases JSON
to a running local /optimize-energy and prints a pass/fail summary.

Usage:
    uvicorn app.main:app --reload &
    python scripts/run_public_samples.py path/to/public_sample_cases.json
"""
from __future__ import annotations

import json
import sys

import requests

BASE_URL = "http://127.0.0.1:8000"


def main(path: str) -> None:
    with open(path) as f:
        data = json.load(f)

    cases = data.get("cases", data.get("case_list", []))
    passed = 0
    for case in cases:
        cid = case.get("case_id") or case.get("id") or "?"
        resp = requests.post(f"{BASE_URL}/optimize-energy", json=case["input"], timeout=30)
        ok = resp.status_code == 200
        if ok:
            passed += 1
        print(f"[{'PASS' if ok else 'FAIL'}] case {cid}: HTTP {resp.status_code}")
        if not ok:
            print("  ", resp.text[:300])

    print(f"\n{passed}/{len(cases)} cases returned a valid 200 response.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "public_sample_cases.json")
