from dataclasses import dataclass


@dataclass
class TriageResult:
    primary_route: str          # task|event|collection|finance|note|other
    confidence: float
    contains_time_reference: bool
    contains_multiple_items: bool
    raw_response: dict
