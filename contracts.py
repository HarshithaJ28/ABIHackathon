"""Shared data contracts for the hackathon pipeline.

These definitions are intentionally stable and should not be changed without
team discussion.
"""

from __future__ import annotations

from typing import Final


CONFIDENCE_VALUES: Final[list[str]] = ["clean", "guessed", "missing"]

ROUTING_DECISIONS: Final[list[str]] = [
	"auto_accept",
	"flag_for_review",
	"reject",
]

RAW_PATIENT_FIELDS: Final[list[str]] = [
	"id",
	"facility_id",
	"patient_id",
	"first_name",
	"last_name",
	"birth_date",
	"gender",
	"primary_payer_code",
	"last_modified_at",
	"is_new_admission",
]

RAW_DIAGNOSIS_FIELDS: Final[list[str]] = [
	"id",
	"patient_id",
	"icd10_code",
	"icd10_description",
	"clinical_status",
	"onset_date",
	"last_modified_at",
]

RAW_COVERAGE_FIELDS: Final[list[str]] = [
	"id",
	"patient_id",
	"payer_name",
	"payer_code",
	"payer_type",
	"effective_from",
	"effective_to",
	"last_modified_at",
]

RAW_NOTE_FIELDS: Final[list[str]] = [
	"id",
	"patient_id",
	"org_id",
	"pcc_note_id",
	"note_type",
	"effective_date",
	"note_text",
	"created_by",
	"note_label",
	"sync_version",
	"is_current",
]

RAW_ASSESSMENT_FIELDS: Final[list[str]] = [
	"id",
	"patient_id",
	"org_id",
	"pcc_assessment_id",
	"assessment_type",
	"status",
	"assessment_date",
	"completion_date",
	"template_id",
	"assessment_type_description",
	"raw_json",
	"sync_version",
	"is_current",
]

EXTRACTED_RECORD_EXAMPLE: Final[dict[str, object]] = {
	"patient_id": "FA-001",
	"wound_type": "pressure ulcer",
	"wound_stage": "3",
	"location": "sacrum",
	"length_cm": 4.2,
	"width_cm": 3.1,
	"depth_cm": 1.5,
	"drainage": "moderate",
	"source_format": "SOAP",
	"confidence": {
		"wound_type": "clean",
		"measurements": "clean",
		"drainage": "guessed",
		"stage": "missing",
	},
}

FINAL_OUTPUT_EXAMPLE: Final[dict[str, object]] = {
	"patient_id": "FA-001",
	"name": "Agnes Dunbar",
	"facility": "Facility A",
	"wound_type": "pressure ulcer",
	"stage": "3",
	"location": "sacrum",
	"length_cm": 4.2,
	"width_cm": 3.1,
	"depth_cm": 1.5,
	"drainage": "moderate",
	"has_part_b": True,
	"decision": "auto_accept",
	"reason": (
		"Active Medicare Part B; wound type, measurements, and drainage all "
		"clearly documented."
	),
	"summary": None,
}
