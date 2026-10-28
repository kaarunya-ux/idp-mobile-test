"""Permission strings and who has them (SEC-06; IDP-00 ch. 7.2).

Keycloak tokens carry roles (``roles`` claim) and, for platform staff, the realm role ``PLATFORM_ADMIN``; API keys
carry scopes. Both become permissions here, so routes and services only ever check permissions.
"""

from collections.abc import Iterable

TEMPLATE_READ = "template:read"
TEMPLATE_WRITE = "template:write"
TEMPLATE_PUBLISH = "template:publish"
STANDARD_TEMPLATE_WRITE = "template:standard-write"  # platform staff only: standard templates and document types

PLATFORM_ADMIN = "PLATFORM_ADMIN"

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "GUEST": frozenset({TEMPLATE_READ}),  # standard templates only: guests and personal accounts have no own ones
    "PERSONAL": frozenset({TEMPLATE_READ}),
    "MEMBER": frozenset({TEMPLATE_READ}),
    "REVIEWER": frozenset({TEMPLATE_READ}),
    "DEVELOPER": frozenset({TEMPLATE_READ}),
    "ORG_ADMIN": frozenset({TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH}),
    "ORG_OWNER": frozenset({TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH}),
    PLATFORM_ADMIN: frozenset({TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH, STANDARD_TEMPLATE_WRITE}),
}
_SCOPE_PERMISSIONS: dict[str, frozenset[str]] = {
    "templates:read": frozenset({TEMPLATE_READ}),
    "templates:write": frozenset({TEMPLATE_READ, TEMPLATE_WRITE}),  # API keys can't publish (IDP-00 ch. 7.2)
}


def permissions_for(roles: Iterable[str], scopes: Iterable[str]) -> frozenset[str]:
    granted: set[str] = set()
    for role in roles:
        granted |= _ROLE_PERMISSIONS.get(role, frozenset())
    for scope in scopes:
        granted |= _SCOPE_PERMISSIONS.get(scope, frozenset())
    return frozenset(granted)
