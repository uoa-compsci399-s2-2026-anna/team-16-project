"""Custom admin pages: MFA verification, forced password change, enrolment.

STUB (Task 3) - Tasks 5-7 replace these bodies with the real flows from
docs/interfaces.md 8.3 (login -> forced password change -> forced TOTP
enrolment -> access granted). They exist here only so that ``admin.app``
imports and so that ``url_for("admin:view-<identity>")`` already resolves
for whichever task lands next - the ``identity`` on each view below is the
one its real implementation must keep.

Every route currently returns an empty 200 and does nothing else.
"""

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import Response


class VerifyView(BaseView):
    """STUB (Task 3) - replaced by the real TOTP verification step."""

    name = "Verify"
    identity = "verify"

    @expose("/verify", methods=["GET"], identity="verify")
    async def verify(self, request: Request) -> Response:
        return Response(status_code=200)


class ChangePasswordView(BaseView):
    """STUB (Task 3) - replaced by the real forced password-change step."""

    name = "Change password"
    identity = "change-password"

    @expose("/change-password", methods=["GET"], identity="change-password")
    async def change_password(self, request: Request) -> Response:
        return Response(status_code=200)


class EnrolView(BaseView):
    """STUB (Task 3) - replaced by the real MFA enrolment step (QR code,
    recovery codes)."""

    name = "Enrol"
    identity = "enrol"

    @expose("/enrol", methods=["GET"], identity="enrol")
    async def enrol(self, request: Request) -> Response:
        return Response(status_code=200)
