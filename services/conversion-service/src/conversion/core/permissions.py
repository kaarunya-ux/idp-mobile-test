"""Who may convert a file while waiting (SEC-06; IDP-00 ch. 7.2): the same roles and scopes as extraction's
synchronous route. Tokens carry roles; API keys carry scopes."""

from collections.abc import Iterable

DOCUMENT_WRITE = "document:write"  # every account role, on its own documents

_ROLES_THAT_WRITE_DOCUMENTS = frozenset(
    {"GUEST", "PERSONAL", "MEMBER", "REVIEWER", "DEVELOPER", "ORG_ADMIN", "ORG_OWNER"}
)
_SCOPES_THAT_WRITE_DOCUMENTS = frozenset({"documents:write"})


def permissions_for(roles: Iterable[str], scopes: Iterable[str]) -> frozenset[str]:
    allowed = _ROLES_THAT_WRITE_DOCUMENTS.intersection(roles) or _SCOPES_THAT_WRITE_DOCUMENTS.intersection(scopes)
    return frozenset({DOCUMENT_WRITE}) if allowed else frozenset()
