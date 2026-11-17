"""A smoke test against the live API. Usage:
MEDILOOP_API_KEY=sk_live_... MEDILOOP_FACILITY=<uuid> MEDILOOP_PATIENT=<uhid> python tests/live.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from mediloop import Client, PermissionDeniedError

m = Client(os.environ.get("MEDILOOP_API_KEY", "")).for_facility(os.environ.get("MEDILOOP_FACILITY", ""))
cover = m.post("/api/v1/coverage/verify/", {"patient_uhid": os.environ.get("MEDILOOP_PATIENT", ""), "benefit_category": "consultation", "amount": 50000})
print("cover:", {"covered": cover["covered"], "insurer": (cover.get("primary") or {}).get("insurer_name"), "patient_pays": cover.get("patient_responsibility")})
n = sum(1 for _ in m.paginate("/api/v1/healthcare-billing/bills/", {"page_size": 10}))
print("bills read across pages:", n)
try:
    m.get("/api/v1/fraud/flags/")
except PermissionDeniedError as e:
    print(f"refusal carried its reason: {e.error_code} (HTTP {e.status})")
