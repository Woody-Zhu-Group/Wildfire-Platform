"""Accounts API. Production browser requests enter through the auth gateway."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import asynccontextmanager
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import UUID

import httpx
import psycopg
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from psycopg_pool import PoolTimeout
from starlette.concurrency import run_in_threadpool

from services.accounts.config import Settings
from services.accounts.identity import OIDC
from services.accounts.mail import Mailer, MailUnavailable
from services.accounts.schemas import AccessApplication, Claim, Decision, Invite, UserPatch
from services.accounts.security import (
    FLOW_COOKIE, INVITE_COOKIE, SAFE_METHODS, SESSION_COOKIE, AccountError,
    FlowCipher, check_write, csrf, return_path, token,
)
from services.accounts.store import Store


class FlowLimit:
    """Bound public login/claim requests without trusting forwarded IP headers."""

    def __init__(self):
        self.clients: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self.lock = Lock()

    def check(self, client: str) -> None:
        with self.lock:
            now = monotonic()
            started, count = self.clients.pop(client, (now, 0))
            if now - started >= 300:
                started, count = now, 0
            self.clients[client] = (started, count + 1)
            while len(self.clients) > 4096:
                self.clients.popitem(last=False)
            if count >= 100:
                raise AccountError(429, "flow_rate_limited", "Please wait before trying again.")


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "name", "email", "role", "status")}


def set_cookie(response: Response, name: str, raw: str, age: int) -> None:
    response.set_cookie(name, raw, max_age=age, httponly=True, secure=True, samesite="lax", path="/")


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, httponly=True, secure=True, samesite="lax", path="/")


def create_app(settings: Settings | None = None, store=None, identity=None, mailer=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resolved = settings or Settings.from_env()
        database = store or Store(resolved)
        client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        application.state.settings = resolved
        application.state.store = database
        application.state.identity = identity or OIDC(resolved, client)
        application.state.mailer = mailer or Mailer(resolved)
        application.state.cipher = FlowCipher(resolved.secret)
        application.state.limiter = FlowLimit()
        try:
            database.open()
            yield
        finally:
            database.close()
            await client.aclose()

    application = FastAPI(title="Wildfire accounts", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(AccountError)
    async def account_error(request: Request, error: AccountError):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Validation errors can contain the submitted invitation or credentials.
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_input", "message": "Please check the submitted fields."}})

    async def database_error(request: Request, error: Exception):
        return JSONResponse(status_code=503, content={"error": {"code": "accounts_unavailable", "message": "Account services are temporarily unavailable."}})

    application.add_exception_handler(psycopg.Error, database_error)
    application.add_exception_handler(PoolTimeout, database_error)

    def current_user(request: Request) -> dict:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            raise AccountError(401, "sign_in_required", "Please sign in.")
        user = request.app.state.store.authenticate(raw)
        if request.method not in SAFE_METHODS:
            cfg = request.app.state.settings
            check_write(cfg.secret, raw, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"), cfg.public_origin)
        return user

    def administrator(user: dict = Depends(current_user)) -> dict:
        if user["status"] != "active" or user["role"] != "admin":
            raise AccountError(403, "admin_required", "Administrator access is required.")
        return user

    @application.get("/health")
    def health(request: Request):
        request.app.state.store.health()
        return {"status": "ok", "service": "accounts"}

    @application.get("/auth/login")
    def login(request: Request, return_to: str = "/"):
        state = request.app.state
        state.limiter.check(request.client.host if request.client else "unknown")
        destination = return_path(return_to)
        invitation = None
        claim = request.cookies.get(INVITE_COOKIE)
        if claim:
            try:
                invitation = state.cipher.open(state.store.take_flow("invite", claim, claim))
            except AccountError as error:
                if error.code != "invalid_login_flow":
                    raise
                # Expired optional invitation context must not block normal login.
                invitation = None
        raw, browser, nonce, verifier = token(), token(), token(), token()
        payload = {"nonce": nonce, "verifier": verifier, "return_to": destination, "invitation": invitation}
        state.store.save_flow("login", raw, browser, state.cipher.seal(payload))
        response = RedirectResponse(state.identity.authorize_url(raw, nonce, verifier), status_code=302)
        set_cookie(response, FLOW_COOKIE, browser, state.settings.flow_seconds)
        clear_cookie(response, INVITE_COOKIE)
        return response

    @application.get("/auth/callback")
    async def callback(request: Request, state: str = Query(max_length=200), code: str = Query(max_length=4096)):
        context = request.app.state
        browser = request.cookies.get(FLOW_COOKIE)
        if not browser:
            raise AccountError(401, "invalid_login_flow", "Please start signing in again.")
        flow = context.cipher.open(await run_in_threadpool(context.store.take_flow, "login", state, browser))
        verified = await context.identity.exchange(code, flow["verifier"], flow["nonce"])
        raw = token()
        invitation = flow["invitation"]
        user = await run_in_threadpool(context.store.create_session,
            verified, raw, UUID(invitation["id"]) if invitation else None,
            invitation["token_hash"] if invitation else None,
            previous_token=request.cookies.get(SESSION_COOKIE),
        )
        if invitation and user["status"] != "suspended":
            destination = "/invite"
        elif user["status"] == "active":
            destination = flow["return_to"]
        else:
            destination = "/access-status"
        response = RedirectResponse(context.settings.public_origin + destination, status_code=302)
        set_cookie(response, SESSION_COOKIE, raw, context.settings.session_seconds)
        clear_cookie(response, FLOW_COOKIE)
        return response

    @application.get("/api/auth/me")
    def me(request: Request, user: dict = Depends(current_user)):
        return {
            "user": public_user(user), "application": request.app.state.store.application(user["id"]),
            "csrf_token": csrf(request.app.state.settings.secret, request.cookies[SESSION_COOKIE]),
        }

    @application.post("/auth/logout", status_code=204)
    def logout(request: Request):
        cfg = request.app.state.settings
        raw = request.cookies.get(SESSION_COOKIE)
        if raw:
            check_write(cfg.secret, raw, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"), cfg.public_origin)
            request.app.state.store.logout(raw)
        elif request.headers.get("Origin") != cfg.public_origin:
            raise AccountError(403, "origin_required", "Open this action on the website.")
        response = Response(status_code=204)
        clear_cookie(response, SESSION_COOKIE)
        clear_cookie(response, FLOW_COOKIE)
        clear_cookie(response, INVITE_COOKIE)
        return response

    @application.post("/api/access-requests", status_code=201)
    def apply(body: AccessApplication, request: Request, user: dict = Depends(current_user)):
        return request.app.state.store.apply(user["id"], body.name, body.organization, body.purpose)

    @application.get("/api/access-requests/me")
    def my_application(request: Request, user: dict = Depends(current_user)):
        return {"application": request.app.state.store.application(user["id"])}

    @application.post("/api/invitations/claim")
    def claim(body: Claim, request: Request):
        context = request.app.state
        if request.headers.get("Origin") != context.settings.public_origin:
            raise AccountError(403, "origin_required", "Open the invitation on this website.")
        context.limiter.check(request.client.host if request.client else "unknown")
        invitation = context.store.claim_invite(body.token)
        raw = token()
        context.store.save_flow("invite", raw, raw, context.cipher.seal({"id": str(invitation["id"]), "token_hash": invitation["token_hash"]}))
        response = JSONResponse({"login_url": "/auth/login?return_to=/invite"})
        set_cookie(response, INVITE_COOKIE, raw, context.settings.flow_seconds)
        return response

    @application.post("/api/invitations/accept")
    def accept(request: Request, user: dict = Depends(current_user)):
        return request.app.state.store.accept_invite(user["id"], user["session_id"])

    @application.get("/api/admin/access-requests")
    def applications(
        request: Request, user: dict = Depends(administrator),
        status: Literal["pending", "approved", "rejected"] | None = None,
        cursor: UUID | None = None, limit: int = Query(default=25, ge=1, le=100),
    ):
        return request.app.state.store.list_rows(user["id"], "access_requests", cursor, limit, status)

    @application.post("/api/admin/access-requests/{request_id}/decision")
    def decision(request_id: UUID, body: Decision, request: Request, user: dict = Depends(administrator)):
        context = request.app.state
        result = context.store.decide(user["id"], request_id, body.decision, body.note, body.public_note)
        email = result.pop("_recipient")
        return send_review(request, result, email, body.public_note)

    def send_review(request: Request, result: dict, email: str, public_note: str) -> dict:
        context = request.app.state
        try:
            context.mailer.review(email, result["status"], public_note)
            status = "sent"
        except MailUnavailable:
            status = "failed"
        context.store.review_delivery(result["id"], status)
        return {**result, "notification_status": status}

    @application.post("/api/admin/access-requests/{request_id}/notify")
    def notify(request_id: UUID, request: Request, user: dict = Depends(administrator)):
        result = request.app.state.store.review_notification(user["id"], request_id)
        email = result.pop("email")
        return send_review(request, result, email, result.pop("public_note"))

    def send_invitation(request: Request, invitation: dict, raw: str) -> dict:
        context = request.app.state
        try:
            context.mailer.invite(invitation["email"], raw)
            status = "sent"
        except MailUnavailable:
            status = "failed"
        context.store.delivery(invitation["id"], raw, status)
        return {**invitation, "delivery_status": status}

    @application.get("/api/admin/invitations")
    def invitations(request: Request, user: dict = Depends(administrator), cursor: UUID | None = None, limit: int = Query(default=25, ge=1, le=100)):
        return request.app.state.store.list_rows(user["id"], "invitations", cursor, limit)

    @application.post("/api/admin/invitations", status_code=201)
    def invite(body: Invite, request: Request, user: dict = Depends(administrator)):
        raw = token()
        row = request.app.state.store.create_invite(user["id"], body.email, raw)
        return send_invitation(request, row, raw)

    @application.post("/api/admin/invitations/{invitation_id}/resend")
    def resend(invitation_id: UUID, request: Request, user: dict = Depends(administrator)):
        raw = token()
        row = request.app.state.store.update_invite(user["id"], invitation_id, raw)
        return send_invitation(request, row, raw)

    @application.post("/api/admin/invitations/{invitation_id}/revoke", status_code=204)
    def revoke(invitation_id: UUID, request: Request, user: dict = Depends(administrator)):
        request.app.state.store.update_invite(user["id"], invitation_id, None)
        return Response(status_code=204)

    @application.get("/api/admin/users")
    def users(
        request: Request, user: dict = Depends(administrator),
        status: Literal["pending", "active", "suspended"] | None = None,
        q: str | None = Query(default=None, max_length=254),
        cursor: UUID | None = None, limit: int = Query(default=25, ge=1, le=100),
    ):
        return request.app.state.store.list_rows(user["id"], "users", cursor, limit, status, q.strip() if q else None)

    @application.patch("/api/admin/users/{user_id}")
    def patch_user(user_id: UUID, body: UserPatch, request: Request, user: dict = Depends(administrator)):
        if body.role is None and body.status is None:
            raise AccountError(422, "empty_update", "Choose a role or account status.")
        return request.app.state.store.patch_user(user["id"], user_id, body.role, body.status)

    @application.get("/api/admin/audit-events")
    def audit(request: Request, user: dict = Depends(administrator), cursor: UUID | None = None, limit: int = Query(default=25, ge=1, le=100)):
        return request.app.state.store.list_rows(user["id"], "audit_events", cursor, limit)

    @application.get("/internal/auth/active", status_code=204)
    def active(request: Request, user: dict = Depends(current_user)):
        if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
            raise AccountError(404, "not_found", "Not found.")
        if user["status"] != "active":
            raise AccountError(403, "access_pending", "Platform access is not active.")
        method = request.headers.get("X-Original-Method")
        if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            raise AccountError(403, "invalid_gateway_request", "The request could not be verified.")
        if method not in SAFE_METHODS:
            cfg = request.app.state.settings
            check_write(cfg.secret, request.cookies[SESSION_COOKIE], request.headers.get("X-CSRF-Token"), request.headers.get("Origin"), cfg.public_origin)
        return Response(status_code=204, headers={"X-Account-User-Id": str(user["id"])})

    return application


app = create_app()
