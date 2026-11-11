"""The invented documents of the PII compliance test and the personal data on each. Nothing here is a real person's.

``id``: identifiers a log mask catches. ``other``: names, dates and addresses that no pattern catches, so finding one in
a log means the code logged extracted data (OBS-04).
"""

DOCS = {
    "P01_pan": {
        "kind": "card",
        "type": "PAN_CARD",
        "id": ["AAAPK1234L"],
        "other": ["TEST KUMAR", "SAMPLE KUMAR", "01/01/1990"],
    },
    "P02_aadhaar": {
        "kind": "card",
        "type": "AADHAAR_CARD",
        "id": ["2345 6789 1008"],
        "other": ["TEST KUMAR", "01/01/1990", "12 Test Street"],
    },
    "P03_aadhaar_badsum": {
        "kind": "card",
        "type": "AADHAAR_CARD",
        "id": ["2345 6789 1009"],
        "other": ["TEST KUMAR", "01/01/1990", "12 Test Street"],
    },
    "P04_licence": {
        "kind": "card",
        "type": "DRIVING_LICENCE",
        "id": ["KA01 20250001234"],
        "other": ["TEST KUMAR", "01/01/1990", "12 Test Street"],
    },
    "P05_letter": {
        "kind": "page",
        "type": None,
        "id": ["98765 43210", "a.mehta@greenfield.example", "AAAPK1234L"],
        "other": ["Orchid Trading", "12 Market Lane"],
    },
}
