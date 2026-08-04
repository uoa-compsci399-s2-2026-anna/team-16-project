"""Authentication backend for the admin panel.

STUB (Task 3) - Tasks 4 and 6 replace this with the real login and MFA-gate
logic from docs/interfaces.md 8.3. Every method here refuses unconditionally,
so the panel is reachable (``/admin/login`` renders) but nobody can pass it.
That is the safe default for a project that has no auth backend yet.
"""

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from admin.config import Settings


class AdminAuth(AuthenticationBackend):
    """STUB (Task 3) - always refuses.

    Task 4 gives ``login`` real credential checking; Task 6 gives
    ``authenticate`` the mandatory-MFA gate described in interfaces.md 8.3.
    """

    def __init__(self, settings: Settings, app) -> None:
        super().__init__(secret_key=settings.secret_key)
        self._settings = settings
        self._app = app

    async def login(self, request: Request) -> bool:
        return False

    async def logout(self, request: Request) -> bool:
        return False

    async def authenticate(self, request: Request) -> bool:
        return False
