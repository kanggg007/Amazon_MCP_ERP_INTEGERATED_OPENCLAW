"""
Alert Rules
=============
Configurable thresholds for alert evaluation.
Each rule is store-aware.
"""

from typing import Dict, List


INVENTORY_RULES = {
    "low_stock_days": {
        "description": "Days of supply threshold for low stock alerts",
        "critical": 10,
        "warning": 20,
    },
    "overstock_days": {
        "description": "Days of supply threshold for overstock alerts",
        "warning": 90,
    },
}

DISCREPANCY_RULES = {
    "minimum_loss": {
        "description": "Minimum estimated loss before alerting",
        "default": 50.0,
    },
    "claim_confidence": {
        "description": "Minimum confidence for claim recommendation",
        "default": 0.75,
    },
}

ADS_RULES = {
    "high_acos": {
        "description": "ACoS threshold for alerts",
        "warning": 0.25,
        "critical": 0.40,
    },
    "high_tacos": {
        "description": "TACoS threshold for alerts",
        "warning": 0.25,
    },
}

VOC_RULES = {
    "negative_ratio": {
        "description": "Negative feedback ratio before alerting",
        "warning": 0.20,
        "critical": 0.35,
    },
}
