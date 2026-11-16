"""Permission strings and who has them (SEC-06; IDP-00 ch. 7.2).

Keycloak tokens carry roles (``roles`` claim) and, for platform staff, the realm role ``PLATFORM_ADMIN``; API keys
carry scopes. Both become permissions here, so routes and services only ever check permissions.
"""

from collections.abc import Iterable
from typing import Literal

type Plan = Literal["GUEST", "PERSONAL", "CORPORATE"]

TEMPLATE_READ = "template:read"
TEMPLATE_WRITE = "template:write"
TEMPLATE_PUBLISH = "template:publish"
STANDARD_TEMPLATE_WRITE = "template:standard-write"  # platform staff only: standard templates and document types
DOCUMENT_WRITE = "document:write"  # IDP-00 ch. 7.2: every account role, on its own documents (synchronous extract)

PLATFORM_ADMIN = "PLATFORM_ADMIN"

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "GUEST": frozenset({TEMPLATE_READ, DOCUMENT_WRITE}),  # standard templates only: guests and personal accounts
    "PERSONAL": frozenset({TEMPLATE_READ, DOCUMENT_WRITE}),  # have no own ones
    "MEMBER": frozenset({TEMPLATE_READ, DOCUMENT_WRITE}),
    "REVIEWER": frozenset({TEMPLATE_READ, DOCUMENT_WRITE}),
    "DEVELOPER": frozenset({TEMPLATE_READ, DOCUMENT_WRITE}),
    "ORG_ADMIN": frozenset({TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH, DOCUMENT_WRITE}),
    "ORG_OWNER": frozenset({TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH, DOCUMENT_WRITE}),
    PLATFORM_ADMIN: frozenset({TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH, STANDARD_TEMPLATE_WRITE}),
}
_ORGANISATION_ROLES = frozenset({"MEMBER", "REVIEWER", "DEVELOPER", "ORG_ADMIN", "ORG_OWNER"})
_SCOPE_PERMISSIONS: dict[str, frozenset[str]] = {
    "templates:read": frozenset({TEMPLATE_READ}),
    "templates:write": frozenset({TEMPLATE_READ, TEMPLATE_WRITE}),  # API keys can't publish (IDP-00 ch. 7.2)
    "documents:write": frozenset({DOCUMENT_WRITE}),
}


def plan_for(roles: Iterable[str], kind: str) -> Plan:
    """Which daily AI allowance applies (IDP-00 ch. 7.9): guests and personal accounts have their own; organisation
    roles and API keys are on a plan. A token with none of these roles gets the personal one, the smaller."""
    held = set(roles)
    if "GUEST" in held:
        return "GUEST"
    if "PERSONAL" in held:
        return "PERSONAL"
    if kind == "api_key" or held & _ORGANISATION_ROLES:
        return "CORPORATE"
    return "PERSONAL"


def permissions_for(roles: Iterable[str], scopes: Iterable[str]) -> frozenset[str]:
    granted: set[str] = set()
    for role in roles:
        granted |= _ROLE_PERMISSIONS.get(role, frozenset())
    for scope in scopes:
        granted |= _SCOPE_PERMISSIONS.get(scope, frozenset())
    return frozenset(granted)
