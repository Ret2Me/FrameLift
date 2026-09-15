"""Validated runtime contact identity for publication API traffic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlparse


CONTACT_SCHEMA = "observation-planning-api-contact-v2"
USER_AGENT_PREFIX = "telemetry-yield-research/0.2 contact="
OPEN_METEO_TERMS_URL = "https://open-meteo.com/en/terms"
_RESERVED_SUFFIXES = (
    ".invalid",
    ".test",
    "example.com",
    "example.net",
    "example.org",
    "localhost",
)


class ApiContactError(ValueError):
    """A publication API contact is absent, placeholder-like, or malformed."""


def validate_api_contact(value: object) -> str:
    if not isinstance(value, str):
        raise ApiContactError("API contact must be a string")
    contact = value.strip()
    if not contact or any(character in contact for character in "\r\n\t "):
        raise ApiContactError("API contact must be non-empty and contain no whitespace")
    lowered = contact.casefold()
    if contact.startswith("https://"):
        parsed = urlparse(contact)
        host = (parsed.hostname or "").casefold()
        if (
            not host
            or "." not in host
            or parsed.username is not None
            or parsed.password is not None
            or any(host.endswith(suffix) for suffix in _RESERVED_SUFFIXES)
        ):
            raise ApiContactError("API contact URL is invalid or reserved")
        return contact
    if contact.count("@") == 1:
        local, domain = contact.rsplit("@", 1)
        domain = domain.casefold()
        if (
            not local
            or "." not in domain
            or any(domain.endswith(suffix) for suffix in _RESERVED_SUFFIXES)
        ):
            raise ApiContactError("API contact email is invalid or reserved")
        return contact
    raise ApiContactError("API contact must be an email address or HTTPS URL")


def publication_user_agent(contact: object) -> str:
    return USER_AGENT_PREFIX + validate_api_contact(contact)


def validate_publication_user_agent(value: object) -> str:
    if not isinstance(value, str) or not value.startswith(USER_AGENT_PREFIX):
        raise ApiContactError("publication User-Agent prefix is invalid")
    contact = validate_api_contact(value.removeprefix(USER_AGENT_PREFIX))
    return USER_AGENT_PREFIX + contact


def load_api_contact(path: Path) -> tuple[str, Mapping[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiContactError("cannot read API contact artifact") from exc
    if not isinstance(payload, Mapping):
        raise ApiContactError("API contact artifact must be a JSON object")
    if payload.get("schema_version") != CONTACT_SCHEMA:
        raise ApiContactError("unsupported API contact schema")
    user_agent = publication_user_agent(payload.get("contact"))
    usage = payload.get("open_meteo_free_api_usage")
    if not isinstance(usage, Mapping):
        raise ApiContactError("Open-Meteo free API usage attestation is required")
    if usage.get("terms_url") != OPEN_METEO_TERMS_URL:
        raise ApiContactError("Open-Meteo terms URL mismatch")
    if usage.get("use_category") != "non-commercial-research":
        raise ApiContactError(
            "the configured free Open-Meteo endpoints require non-commercial research use"
        )
    if usage.get("attested") is not True:
        raise ApiContactError("Open-Meteo non-commercial use was not attested")
    return user_agent, payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contact-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    user_agent, _ = load_api_contact(args.contact_file)
    print(user_agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
