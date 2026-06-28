BASE_URL = "https://hackathon.prod.pulsefoundry.ai"

FACILITY_IDS = [101, 102, 103]

FACILITY_NAMES = {
    101: "Facility A",
    102: "Facility B",
    103: "Facility C",
}

PAYER_CODES = {
    "MCB": "Medicare Part B",
    "MCA": "Medicare Part A",
    "MCD": "Medicaid",
    "HMO": "HMO / Managed Care",
}

TARGET_PAYER_CODE = "MCB"

MAX_RETRIES = 10

# Adaptive ingestion engine
MAX_ATTEMPTS = 8

CONCURRENCY_MIN = 4
CONCURRENCY_MAX = 256
CONCURRENCY_START = 8
RTT_TOLERANCE = 1.5
LIMITER_SMOOTHING = 0.2

HONOR_RETRY_AFTER = True
RETRY_AFTER_CAP = 5.0
BACKOFF_BASE = 0.25
BACKOFF_CAP = 4.0

REQUEST_TIMEOUT = 15.0

# ── decision engine ──────────────────────────────────────────────────────
TARGET_PAYER_CODE = "MCB"          # Medicare Part B
REQUIRED_FIELDS = ["wound_type", "location", "length_cm", "width_cm", "depth_cm", "drainage_amount"]
DIM_TOLERANCE_CM = 0.2             # 2 mm agreement window for note-vs-assessment fusion
MAX_MISSING_BEFORE_REJECT = 3      # >=3 required fields absent -> not reliably extractable

# Illustrative reimbursement by wound type (USD). Swap for real CPT/HCPCS fee schedule.
WOUND_REIMBURSEMENT = {
    "pressure_ulcer": 412, "diabetic_ulcer": 385, "venous_ulcer": 340,
    "arterial_ulcer": 365, "surgical_site": 298, "abscess": 255, "burn": 320,
    "chronic_ulcer": 300, "_default": 275,
}

