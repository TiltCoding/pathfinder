"""Tiny dependency-free HTTP-ish router.

Routes are registered as (method, path_template) -> handler. Path templates use
`{name}` segments. This stands in for a web framework so the fixture needs no installs.
"""

import re
from typing import Callable, Dict, Tuple


class Router:
    def __init__(self):
        self._routes: Dict[Tuple[str, str], Callable] = {}

    def add(self, method: str, path: str, handler: Callable) -> None:
        self._routes[(method.upper(), path)] = handler

    def routes(self):
        return list(self._routes.keys())

    def dispatch(self, method: str, path: str):
        for (m, template), handler in self._routes.items():
            if m != method.upper():
                continue
            regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template) + "$"
            match = re.match(regex, path)
            if match:
                return handler(**match.groupdict())
        raise KeyError(f"no route for {method} {path}")


def build_router(service) -> Router:
    router = Router()
    router.add("GET", "/reports/{id}", lambda id: service.summary(id))
    return router
