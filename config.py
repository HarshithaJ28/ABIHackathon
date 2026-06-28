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

