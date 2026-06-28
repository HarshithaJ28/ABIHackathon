"""P2 extraction engine.

This module is intentionally isolated from ingestion and routing. It consumes
already-ingested notes and assessments and emits one validated Contract B
record per patient.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

Confidence = Literal["clean", "guessed", "missing"]
Drainage = Literal["none", "light", "moderate", "heavy"]


class Confidences(BaseModel):
    wound_type: Confidence = "missing"
    stage: Confidence = "missing"
    location: Confidence = "missing"
    measurements: Confidence = "missing"
    drainage: Confidence = "missing"


class WoundRecord(BaseModel):
    patient_id: str
    wound_type: Optional[str] = None
    wound_stage: Optional[str] = None
    location: Optional[str] = None
    length_cm: Optional[float] = None
    width_cm: Optional[float] = None
    depth_cm: Optional[float] = None
    drainage: Optional[Drainage] = None
    source_format: Optional[str] = None
    confidence: Confidences = Field(default_factory=Confidences)


class GeminiExtraction(BaseModel):
    wound_type: Optional[str] = None
    wound_stage: Optional[str] = None
    location: Optional[str] = None
    length_cm: Optional[float] = None
    width_cm: Optional[float] = None
    depth_cm: Optional[float] = None
    drainage: Optional[Drainage] = None
    evidence: Optional[str] = None


MEASUREMENT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)(?:\s*[x×*]\s*(\d+(?:\.\d+)?))?\s*(mm|cm)?",
    re.IGNORECASE,
)

MEASUREMENT_RE_VERBOSE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|cm)?\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|cm)?(?:\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|cm)?)?",
    re.IGNORECASE,
)

LABELED_LENGTH_RE = re.compile(r"(?:length|l)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm)?", re.IGNORECASE)
LABELED_WIDTH_RE = re.compile(r"(?:width|w)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm)?", re.IGNORECASE)
LABELED_DEPTH_RE = re.compile(r"(?:depth|d)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm)?", re.IGNORECASE)

LABELED_FIELD_RES = {
    "wound_type": re.compile(r"(?:wound\s*type|type)\s*[:=]?\s*([^\n;,.]+)", re.IGNORECASE),
    "wound_stage": re.compile(r"(?:wound\s*stage|stage)\s*[:=]?\s*([^\n;,.]+)", re.IGNORECASE),
    "location": re.compile(r"(?:location|site)\s*[:=]?\s*([^\n;,.]+)", re.IGNORECASE),
    "drainage": re.compile(r"(?:drainage\s*amount|drainage)\s*[:=]?\s*([^\n;,.]+)", re.IGNORECASE),
}

SYSTEM_PROMPT = """You extract wound-care fields from a clinical note.
RULES:
- Only extract what is explicitly stated. If a value is not in the text, return null.
- Measurements are in centimeters. If the note says mm, convert to cm.
- Drainage must be exactly one of: none, light, moderate, heavy.
- If the note says no drainage, denies drainage, or the wound is healed/resolved, drainage = none.
- If two wounds are described, extract the most severe one (highest stage / largest size).
- In evidence, quote the exact phrase each value came from.
- Return JSON only."""

FLAG_WIDTH_GT_LENGTH = False


def has_word(text: str, *words: str) -> bool:
    """True if any whole word/phrase in `words` appears in `text` (case-insensitive)."""
    t = _clean_text(text).lower()
    return any(re.search(rf"\b{re.escape(word.lower())}\b", t) for word in words)


def _clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_wound_type(value: Any) -> Optional[str]:
    text = _clean_text(value).lower().replace("_", " ")
    if not text:
        return None
    if has_word(text, "pressure", "decubitus", "bedsore", "pressure injury", "pressure ulcer"):
        return "pressure ulcer"
    if has_word(text, "diabetic", "dfu", "diabetic foot ulcer"):
        return "diabetic foot ulcer"
    if has_word(text, "venous", "vsu", "stasis", "venous ulcer"):
        return "venous ulcer"
    if has_word(text, "arterial", "arterial ulcer"):
        return "arterial ulcer"
    if has_word(text, "surgical", "ssi", "surgical site infection"):
        return "surgical site infection"
    if has_word(text, "abscess"):
        return "abscess"
    if has_word(text, "burn"):
        return "burn"
    return _clean_text(value) or None


def normalize_stage(value: Any) -> Optional[str]:
    text = _clean_text(value).lower()
    if not text:
        return None
    if text in {"n/a", "na", "not applicable", "none", "nil", "missing"}:
        return None
    if any(token in text for token in ("unstageable", "un-stageable")):
        return "unstageable"
    if "dti" in text or "deep tissue" in text:
        return "DTI"
    match = re.search(r"\b(i{1,3}|iv|[1-4])\b", text, re.IGNORECASE)
    if not match:
        return _clean_text(value) or None
    token = match.group(1).lower()
    roman_map = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
    return roman_map.get(token, token)


def normalize_drainage(value: Any) -> Optional[Drainage]:
    text = _clean_text(value).lower()
    if not text:
        return None
    if text == "none":
        return "none"
    if has_word(text, "copious", "large", "heavy", "profuse"):
        return "heavy"
    if has_word(text, "moderate", "mod"):
        return "moderate"
    if has_word(text, "scant", "minimal", "small", "light"):
        return "light"
    return None


def _convert_to_cm(amount: Optional[float], unit: Optional[str]) -> Optional[float]:
    if amount is None:
        return None
    if unit and unit.lower() == "mm":
        return amount / 10.0
    return amount


def _parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _range_ok(length: Optional[float], width: Optional[float], depth: Optional[float]) -> bool:
    checks: list[bool] = []
    if length is not None:
        checks.append(0.1 <= length <= 30.0)
    if width is not None:
        checks.append(0.1 <= width <= 30.0)
    if depth is not None:
        checks.append(0.0 <= depth <= 10.0)
    return all(checks) if checks else False


def _measurement_confidence(length: Optional[float], width: Optional[float], depth: Optional[float]) -> Confidence:
    if not _range_ok(length, width, depth):
        return "guessed"
    if FLAG_WIDTH_GT_LENGTH and length is not None and width is not None and width > length:
        return "guessed"
    return "clean"


def parse_measurements(text: str) -> tuple[Optional[float], Optional[float], Optional[float], Confidence]:
    text = text or ""
    match = MEASUREMENT_RE_VERBOSE.search(text)
    if match:
        length = _convert_to_cm(_parse_number(match.group(1)), match.group(2) or match.group(6))
        width = _convert_to_cm(_parse_number(match.group(3)), match.group(4) or match.group(6) or match.group(2))
        depth = _convert_to_cm(_parse_number(match.group(5)), match.group(6) or match.group(4) or match.group(2))
        return length, width, depth, _measurement_confidence(length, width, depth)

    match = MEASUREMENT_RE.search(text)
    if match:
        length = _convert_to_cm(_parse_number(match.group(1)), match.group(4))
        width = _convert_to_cm(_parse_number(match.group(2)), match.group(4))
        depth = _convert_to_cm(_parse_number(match.group(3)), match.group(4))
        return length, width, depth, _measurement_confidence(length, width, depth)

    length_match = LABELED_LENGTH_RE.search(text)
    width_match = LABELED_WIDTH_RE.search(text)
    depth_match = LABELED_DEPTH_RE.search(text)
    if length_match or width_match or depth_match:
        length = _convert_to_cm(_parse_number(length_match.group(1)), length_match.group(2)) if length_match else None
        width = _convert_to_cm(_parse_number(width_match.group(1)), width_match.group(2)) if width_match else None
        depth = _convert_to_cm(_parse_number(depth_match.group(1)), depth_match.group(2)) if depth_match else None
        return length, width, depth, _measurement_confidence(length, width, depth)

    return None, None, None, "missing"


def _field_from_text(text: str, regex: re.Pattern[str]) -> Optional[str]:
    match = regex.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _is_multi_wound(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("two wounds", "multiple wounds", "wound 1", "wound 2", "and another wound"))


def _extract_stage(text: str) -> Optional[str]:
    match = re.search(r"\bstage\s*(i{1,3}|iv|[1-4])\b", text, re.IGNORECASE)
    if match:
        return normalize_stage(match.group(1))
    match = re.search(r"\b(unstageable|dti|deep tissue injury)\b", text, re.IGNORECASE)
    if match:
        return normalize_stage(match.group(1))
    return None


def _extract_location(text: str) -> Optional[str]:
    patterns = (
        r"(?:location|located\s+at|wound\s+on|site)\s*[:=]?\s*([A-Za-z0-9\-\s/]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip().rstrip(".,;")
            return value.title() if value else None
    for token in ("sacrum", "sacral region", "heel", "toe", "foot", "ankle", "buttock", "coccyx", "hip", "leg", "arm", "back", "abdomen"):
        if token in text.lower():
            return token.title()
    return None


def _extract_location_from_laterality(location: Optional[str], laterality: Optional[str]) -> Optional[str]:
    if not location:
        return None
    value = location.strip()
    if laterality:
        side = laterality.strip().lower()
        if side in {"left", "right", "bilateral"} and side not in value.lower():
            return f"{side.title()} {value}"
    return value


def _assessment_question_map(payload: dict[str, Any]) -> dict[str, str]:
    question_map: dict[str, str] = {}
    for section in payload.get("sections", []):
        for question in section.get("questions", []):
            q_text = _clean_text(question.get("question"))
            a_text = _clean_text(question.get("answer"))
            if q_text and a_text:
                question_map[q_text.lower()] = a_text
    return question_map


def _extract_drainage(text: str) -> Optional[Drainage]:
    lower = text.lower()
    if has_word(lower, "copious", "large", "heavy", "profuse"):
        return "heavy"
    if has_word(lower, "moderate", "mod"):
        return "moderate"
    if has_word(lower, "scant", "minimal", "small", "light"):
        return "light"
    if re.search(r"\bno\b(?:\W+\w+){0,4}\W+\bdrainage\b", lower) or re.search(r"\bdenies\b(?:\W+\w+){0,4}\W+\bdrainage\b", lower):
        return "none"
    if re.search(r"\bwithout\b(?:\W+\w+){0,4}\W+\bdrainage\b", lower) or re.search(r"\babsent\b(?:\W+\w+){0,4}\W+\bdrainage\b", lower):
        return "none"
    return None


def _extract_wound_type_and_location(text: str) -> tuple[Optional[str], Optional[str]]:
    match = re.search(r"(?:wound\s+status|wound\s+narrative)?\s*:??\s*([^/\n]+?)\s+to\s+([^/\n]+?)(?:\s*/|$)", text, re.IGNORECASE)
    if not match:
        return None, None
    wound_type = normalize_wound_type(match.group(1))
    location = _clean_text(match.group(2)) or None
    return wound_type, location


def _looks_unstructured(text: str) -> bool:
    """No labeled fields and no parseable measurement -> treat as narrative."""
    t = text or ""
    has_labels = any(
        r.search(t)
        for r in (
            LABELED_LENGTH_RE,
            LABELED_WIDTH_RE,
            LABELED_DEPTH_RE,
            LABELED_FIELD_RES["wound_type"],
            LABELED_FIELD_RES["drainage"],
        )
    )
    has_meas = MEASUREMENT_RE.search(t) is not None or MEASUREMENT_RE_VERBOSE.search(t) is not None
    return not has_labels and not has_meas


def _is_envive(source_format: str, text: str) -> bool:
    return "envive" in _clean_text(source_format).lower() or _looks_unstructured(text)


def _normalize_source_format(raw: str) -> str:
    r = _clean_text(raw).lower()
    if "envive" in r:
        return "envive"
    if "assessment" in r:
        return "assessment"
    if "spn" in r or "soap" in r:
        return "soap"
    return "prose"


def _pick_latest(rows: Iterable[dict], date_fields: tuple[str, ...]) -> Optional[dict]:
    latest_row: Optional[dict] = None
    latest_dt: Optional[datetime] = None
    for row in rows:
        candidate: Optional[datetime] = None
        for field_name in date_fields:
            value = row.get(field_name)
            if value:
                candidate = _parse_datetime(str(value))
                if candidate is not None:
                    break
        if candidate is None:
            continue
        if latest_dt is None or candidate > latest_dt:
            latest_dt = candidate
            latest_row = row
    return latest_row


def _parse_datetime(value: str) -> Optional[datetime]:
    text = value.strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _load_raw_json(raw_json: Any) -> dict[str, Any]:
    if raw_json is None:
        return {}
    if isinstance(raw_json, dict):
        return raw_json
    text = _clean_text(raw_json)
    if not text:
        return {}
    loaded = json.loads(text)
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def _find_nested_value(data: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _find_nested_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_nested_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def extract_from_assessment(raw_json: str, patient_id: str) -> WoundRecord:
    assessment = _load_raw_json(raw_json)
    record = WoundRecord(patient_id=patient_id)
    question_map = _assessment_question_map(assessment)

    # Structured question/answer assessments are the common case.
    laterality = question_map.get("laterality")
    location = question_map.get("location")
    if location not in (None, ""):
        record.location = _extract_location_from_laterality(location, laterality)
        record.confidence.location = "clean"
    elif laterality not in (None, ""):
        record.location = _clean_text(laterality)
        record.confidence.location = "clean"

    value = question_map.get("wound type") or _find_nested_value(assessment, ("wound_type", "type"))
    if value not in (None, ""):
        record.wound_type = normalize_wound_type(value)
        record.confidence.wound_type = "clean"

    value = question_map.get("stage") or _find_nested_value(assessment, ("stage", "wound_stage"))
    if value not in (None, ""):
        record.wound_stage = normalize_stage(value)
        record.confidence.stage = "clean"

    length = question_map.get("length (cm)") or _find_nested_value(assessment, ("length_cm", "length", "l"))
    width = question_map.get("width (cm)") or _find_nested_value(assessment, ("width_cm", "width", "w"))
    depth = question_map.get("depth (cm)") or _find_nested_value(assessment, ("depth_cm", "depth", "d"))
    if any(value is not None for value in (length, width, depth)):
        record.length_cm = _parse_number(length)
        record.width_cm = _parse_number(width)
        record.depth_cm = _parse_number(depth)
        record.confidence.measurements = "clean"

    drainage = question_map.get("drainage amount") or _find_nested_value(assessment, ("drainage_amount", "drainage"))
    if drainage not in (None, ""):
        record.drainage = normalize_drainage(drainage)
        record.confidence.drainage = "clean"

    narrative = question_map.get("wound narrative")
    if narrative:
        wound_type_guess, location_guess = _extract_wound_type_and_location(narrative)
        if record.wound_type is None and wound_type_guess:
            record.wound_type = wound_type_guess
            record.confidence.wound_type = "clean"
        if record.wound_stage is None:
            stage = _extract_stage(narrative)
            if stage:
                record.wound_stage = stage
                record.confidence.stage = "clean"
        if record.location is None and location_guess:
            record.location = location_guess
            record.confidence.location = "clean"
        if record.drainage is None:
            drainage_guess = _extract_drainage(narrative)
            if drainage_guess:
                record.drainage = drainage_guess
                record.confidence.drainage = "clean"
        if any(v is None for v in (record.length_cm, record.width_cm, record.depth_cm)):
            length, width, depth, meas_conf = parse_measurements(narrative)
            if any(v is not None for v in (length, width, depth)):
                record.length_cm = record.length_cm or length
                record.width_cm = record.width_cm or width
                record.depth_cm = record.depth_cm or depth
                record.confidence.measurements = meas_conf if meas_conf != "missing" else "guessed"

    record.source_format = "assessment"
    return finalize_record(record)


def extract_from_note(note_text: str, patient_id: str, source_format: str = "soap") -> WoundRecord:
    text = note_text or ""
    record = WoundRecord(patient_id=patient_id)
    multi_wound = _is_multi_wound(text)

    wound_type_guess, location_guess = _extract_wound_type_and_location(text)
    value = _field_from_text(text, LABELED_FIELD_RES["wound_type"])
    if value is not None:
        record.wound_type = normalize_wound_type(value)
        record.confidence.wound_type = "guessed" if multi_wound else "clean"
    elif wound_type_guess is not None:
        record.wound_type = wound_type_guess
        record.confidence.wound_type = "guessed" if multi_wound else "clean"
    elif text.strip():
        inferred = normalize_wound_type(text)
        if inferred:
            record.wound_type = inferred
            record.confidence.wound_type = "guessed"

    value = _extract_stage(text)
    if value is not None:
        record.wound_stage = value
        record.confidence.stage = "guessed" if multi_wound else "clean"

    value = _field_from_text(text, LABELED_FIELD_RES["location"]) or location_guess or _extract_location(text)
    if value is not None:
        record.location = _clean_text(value)
        record.confidence.location = "guessed" if multi_wound else "clean"

    length, width, depth, meas_conf = parse_measurements(text)
    if any(v is not None for v in (length, width, depth)):
        record.length_cm = length
        record.width_cm = width
        record.depth_cm = depth
        record.confidence.measurements = "guessed" if multi_wound or meas_conf == "guessed" else "clean"

    drainage = _extract_drainage(text) or _field_from_text(text, LABELED_FIELD_RES["drainage"])
    if drainage is not None:
        record.drainage = normalize_drainage(drainage)
        record.confidence.drainage = "guessed" if multi_wound else "clean"

    if record.wound_type is None and text.strip():
        inferred = normalize_wound_type(text)
        if inferred:
            record.wound_type = inferred
            record.confidence.wound_type = "guessed"

    if _is_envive(source_format, text) and _needs_gemini(record):
        gemini = extract_with_gemini(text)
        apply_gemini_extraction(record, gemini, multi_wound=multi_wound)
        record.source_format = "envive"
    else:
        record.source_format = _normalize_source_format(source_format)

    return finalize_record(record)


def _needs_gemini(record: WoundRecord) -> bool:
    return any(
        value is None
        for value in (
            record.wound_type,
            record.wound_stage,
            record.location,
            record.length_cm,
            record.width_cm,
            record.drainage,
        )
    )


def extract_with_gemini(note_text: str, model: str = "gemini-2.5-flash") -> GeminiExtraction:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return GeminiExtraction()

    from google import genai  # type: ignore[import-not-found]
    from google.genai import types  # type: ignore[import-not-found]

    client = genai.Client(api_key=api_key)
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=model,
                contents=note_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=GeminiExtraction,
                    temperature=0,
                ),
            )
            parsed = getattr(response, "parsed", None)
            if parsed:
                return parsed if isinstance(parsed, GeminiExtraction) else GeminiExtraction(**parsed)
            raw_text = re.sub(r"^```json\s*|\s*```$", "", (getattr(response, "text", "") or "").strip(), flags=re.IGNORECASE)
            return GeminiExtraction(**json.loads(raw_text)) if raw_text else GeminiExtraction()
        except Exception:
            if attempt == 3:
                return GeminiExtraction()
            time.sleep(2 ** attempt)
    return GeminiExtraction()


def apply_gemini_extraction(record: WoundRecord, gemini: GeminiExtraction, multi_wound: bool = False) -> WoundRecord:
    if gemini.wound_type and not record.wound_type:
        record.wound_type = normalize_wound_type(gemini.wound_type)
        record.confidence.wound_type = "guessed"
    if gemini.wound_stage and not record.wound_stage:
        record.wound_stage = normalize_stage(gemini.wound_stage)
        record.confidence.stage = "guessed"
    if gemini.location and not record.location:
        record.location = _clean_text(gemini.location)
        record.confidence.location = "guessed"
    if gemini.length_cm is not None and record.length_cm is None:
        record.length_cm = gemini.length_cm
    if gemini.width_cm is not None and record.width_cm is None:
        record.width_cm = gemini.width_cm
    if gemini.depth_cm is not None and record.depth_cm is None:
        record.depth_cm = gemini.depth_cm
    if any(value is not None for value in (gemini.length_cm, gemini.width_cm, gemini.depth_cm)):
        record.confidence.measurements = "guessed"
    if gemini.drainage and not record.drainage:
        record.drainage = gemini.drainage
        record.confidence.drainage = "guessed"
    if multi_wound:
        record.confidence.wound_type = "guessed"
        record.confidence.stage = "guessed"
        record.confidence.location = "guessed"
        record.confidence.measurements = "guessed"
        record.confidence.drainage = "guessed"
    return record


def finalize_record(record: WoundRecord) -> WoundRecord:
    # Drop physically implausible values; remember whether we had to discard any.
    dropped = False
    if record.length_cm is not None and not 0.1 <= record.length_cm <= 30.0:
        record.length_cm = None
        dropped = True
    if record.width_cm is not None and not 0.1 <= record.width_cm <= 30.0:
        record.width_cm = None
        dropped = True
    if record.depth_cm is not None and not 0.0 <= record.depth_cm <= 10.0:
        record.depth_cm = None
        dropped = True

    has_length = record.length_cm is not None
    has_width = record.width_cm is not None
    any_present = has_length or has_width or record.depth_cm is not None

    # Confidence policy for measurements:
    #   - nothing present                       -> missing
    #   - missing a PRIMARY dimension (L or W)   -> guessed
    #   - we had to discard an out-of-range value-> guessed
    #   - otherwise                              -> leave as-is (clean stays clean)
    # NOTE: a missing DEPTH alone no longer downgrades to "guessed" — length + width
    #       present and in range is sufficient for "clean".
    if not any_present:
        record.confidence.measurements = "missing"
    elif not (has_length and has_width):
        record.confidence.measurements = "guessed"
    elif dropped:
        record.confidence.measurements = "guessed"

    return WoundRecord.model_validate(record.model_dump())


def build_wound_record_from_sources(
    patient_id: str,
    notes: Iterable[dict],
    assessments: Iterable[dict],
) -> WoundRecord:
    record = WoundRecord(patient_id=patient_id)
    selected_assessment = _pick_latest(assessments, ("assessment_date", "completion_date"))
    if selected_assessment and selected_assessment.get("raw_json"):
        record = extract_from_assessment(selected_assessment["raw_json"], patient_id)
    selected_note = _pick_latest(notes, ("effective_date",))
    if selected_note and selected_note.get("note_text"):
        candidate = extract_from_note(
            selected_note["note_text"],
            patient_id,
            source_format=(selected_note.get("note_label") or selected_note.get("note_type") or "soap"),
        )
        record = merge_records(record, candidate)
    return finalize_record(record)


def merge_records(base: WoundRecord, incoming: WoundRecord) -> WoundRecord:
    merged = WoundRecord.model_validate(base.model_dump())
    for field_name in ("wound_type", "wound_stage", "location", "length_cm", "width_cm", "depth_cm", "drainage"):
        if getattr(merged, field_name) is None and getattr(incoming, field_name) is not None:
            setattr(merged, field_name, getattr(incoming, field_name))
    confidence_field_to_record_field = {
        "wound_type": "wound_type",
        "stage": "wound_stage",
        "location": "location",
        "measurements": "length_cm",
        "drainage": "drainage",
    }
    for field_name in ("wound_type", "stage", "location", "measurements", "drainage"):
        current = getattr(merged.confidence, field_name)
        incoming_conf = getattr(incoming.confidence, field_name)
        if current == "missing" and incoming_conf != "missing":
            setattr(merged.confidence, field_name, incoming_conf)
        elif incoming_conf == "clean" and current != "clean":
            if field_name == "measurements":
                base_values = (merged.length_cm, merged.width_cm, merged.depth_cm)
                incoming_values = (incoming.length_cm, incoming.width_cm, incoming.depth_cm)
                if base_values == incoming_values:
                    setattr(merged.confidence, field_name, "clean")
            elif getattr(merged, confidence_field_to_record_field[field_name]) == getattr(incoming, confidence_field_to_record_field[field_name]):
                setattr(merged.confidence, field_name, "clean")
    if merged.source_format is None:
        merged.source_format = incoming.source_format
    return merged


def build_records_by_patient(notes_by_patient: dict[str, list[dict]], assessments_by_patient: dict[str, list[dict]]) -> dict[str, WoundRecord]:
    results: dict[str, WoundRecord] = {}
    for patient_id in sorted(set(notes_by_patient) | set(assessments_by_patient)):
        try:
            results[patient_id] = build_wound_record_from_sources(
                patient_id=patient_id,
                notes=notes_by_patient.get(patient_id, []),
                assessments=assessments_by_patient.get(patient_id, []),
            )
        except Exception:
            results[patient_id] = WoundRecord(patient_id=patient_id)
    return results


def serialize_record(record: WoundRecord) -> dict[str, Any]:
    # Output the drainage value under the key `drainage_amount` (the P3 contract),
    # keeping it in its original position rather than appended at the end.
    data = record.model_dump()
    ordered: dict[str, Any] = {}
    for key, value in data.items():
        if key == "drainage":
            ordered["drainage_amount"] = value
        else:
            ordered[key] = value
    return ordered


def validate_record(record: dict[str, Any]) -> WoundRecord:
    data = dict(record)
    if "drainage_amount" in data and "drainage" not in data:
        data["drainage"] = data.pop("drainage_amount")
    try:
        return WoundRecord.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid wound record: {exc}") from exc


def _load_raw_data(base_dir: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    raw_dir = base_dir / "data" / "raw"
    patients = json.loads((raw_dir / "patients.json").read_text())
    notes = json.loads((raw_dir / "notes.json").read_text())
    assessments = json.loads((raw_dir / "assessments.json").read_text())
    return patients, notes, assessments


def _save_extracted_records(records: list[dict[str, Any]], base_dir: Path) -> tuple[Path, Path]:
    output_dir = base_dir / "data" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "extracted_records.json"
    json_path.write_text(json.dumps(records, indent=2))

    csv_path = output_dir / "extracted_records.csv"
    import csv

    with csv_path.open("w", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    return json_path, csv_path


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    patients, notes, assessments = _load_raw_data(base_dir)
    notes_by_patient = {patient["patient_id"]: notes.get(str(patient["id"]), []) for patient in patients}
    assessments_by_patient = {patient["patient_id"]: assessments.get(str(patient["id"]), []) for patient in patients}

    records = build_records_by_patient(notes_by_patient, assessments_by_patient)
    serialized = [serialize_record(records[patient["patient_id"]]) for patient in patients]
    json_path, csv_path = _save_extracted_records(serialized, base_dir)

    print(f"records {len(serialized)}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()


__all__ = [
    "Confidence",
    "Confidences",
    "Drainage",
    "GeminiExtraction",
    "WoundRecord",
    "apply_gemini_extraction",
    "build_records_by_patient",
    "build_wound_record_from_sources",
    "extract_from_assessment",
    "extract_from_note",
    "extract_with_gemini",
    "finalize_record",
    "merge_records",
    "normalize_drainage",
    "normalize_stage",
    "normalize_wound_type",
    "parse_measurements",
    "serialize_record",
    "validate_record",
]