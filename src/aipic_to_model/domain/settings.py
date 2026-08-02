from dataclasses import dataclass


@dataclass(frozen=True)
class SecretStatusV1:
    provider_profile: str
    configured: bool
    mask: str | None = None
