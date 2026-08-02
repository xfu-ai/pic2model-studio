from enum import StrEnum


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_REVERSIBLE = "local_reversible"
    EXTERNAL = "external"
    EXTERNAL_PAID = "external_paid"
    DESTRUCTIVE = "destructive"
