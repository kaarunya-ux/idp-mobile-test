"""Which implementation runs each kind of step (IDP-03 ch. 3.3: ``ConverterRegistry``).

IDP-03 describes the registry as mapping (source, target) to a route; the route planner (domain) already does
that, so here the registry maps each ``Converter`` to the adapter that runs it.
"""

from collections.abc import Iterable

from conversion.conversion.domain.ports import ConverterPort
from conversion.conversion.domain.route import Converter, Route


class ConverterNotRegisteredError(LookupError):
    """A route needs a converter this process doesn't have: a deployment problem, never a user error."""


class ConverterRegistry:
    """Implements ``ConverterLookupPort``. Built once per process in ``bootstrap.py``."""

    def __init__(self, converters: Iterable[ConverterPort]) -> None:
        self._converters: dict[Converter, ConverterPort] = {}
        for implementation in converters:
            if implementation.converter in self._converters:
                raise ValueError(f"{implementation.converter} is registered twice")
            self._converters[implementation.converter] = implementation

    def get(self, converter: Converter) -> ConverterPort:
        implementation = self._converters.get(converter)
        if implementation is None:
            raise ConverterNotRegisteredError(f"no implementation for {converter}")
        return implementation

    def can_run(self, route: Route) -> bool:
        """True when every step of ``route`` has an implementation here."""
        return all(converter in self._converters for converter in route.converters)
