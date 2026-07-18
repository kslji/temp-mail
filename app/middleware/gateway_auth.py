"""
Gateway auth middleware for FastAPI services.

The auth-gateway verifies JWTs and injects identity headers:
  - X-User-Id: the authenticated user's ID
  - X-User-Email: the authenticated user's email
  - X-Forwarded-By: "auth-gateway"

This middleware extracts those headers and makes them available on request.state.
For direct access (without gateway), it also supports JWT verification as a fallback.
"""

import os
import logging
from typing import Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

# JWT secret for fallback direct verification (optional)
JWT_SECRET = os.getenv("JWT_SECRET", "")


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    """
    Extracts user identity from gateway-injected headers.
    Skips auth for health checks, docs, and static files.
    """

    SKIP_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json", "/static"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip auth for health checks, docs, and static paths
        if path in self.SKIP_PATHS or path.startswith("/static"):
            return await call_next(request)

        # Extract gateway-injected headers
        user_id = request.headers.get("x-user-id")
        user_email = request.headers.get("x-user-email")
        forwarded_by = request.headers.get("x-forwarded-by")

        if forwarded_by == "auth-gateway" and user_id:
            # Request came through the gateway — trust the headers
            request.state.user_id = user_id
            request.state.user_email = user_email or ""
            return await call_next(request)

        # Fallback: try JWT verification for direct access
        if JWT_SECRET:
            token = _extract_bearer_token(request)
            if token:
                payload = _verify_jwt(token)
                if payload:
                    request.state.user_id = payload.get("userId", "")
                    request.state.user_email = payload.get("email", "")
                    return await call_next(request)

        # No auth provided
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please provide a valid token.",
        )


def _extract_bearer_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def _verify_jwt(token: str) -> Optional[dict]:
    """Verify JWT token using PyJWT. Returns decoded payload or None."""
    try:
        import jwt

        decoded = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            issuer="toolstack-auth-gateway",
        )
        return decoded
    except Exception:
        return None
