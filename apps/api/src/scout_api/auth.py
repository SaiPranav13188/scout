"""Single-user auth: verify the Supabase access token locally against the project's JWKS
(asymmetric signing keys) and accept only OWNER_EMAIL.
Docs: https://supabase.com/docs/guides/auth/jwts
"""

import hmac
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from scout_core.config import get_settings

bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    # Supabase caches the JWKS at the edge for ~10 minutes; keep our cache at most that long.
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600)


def verify_token(token: str) -> dict:
    s = get_settings()
    if not s.supabase_url or not s.owner_email:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Auth is not configured (SUPABASE_URL / OWNER_EMAIL). See docs/SETUP.md.")
    issuer = f"{s.supabase_url.rstrip('/')}/auth/v1"
    try:
        key = _jwks_client(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["ES256", "RS256", "EdDSA"],
                            audience="authenticated", issuer=issuer)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session") from exc
    if (claims.get("email") or "").lower() != s.owner_email.lower():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this dashboard has a single owner")
    return claims


def require_owner(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    s = get_settings()
    if s.dev_auth_token and not s.is_production and hmac.compare_digest(
            creds.credentials, s.dev_auth_token.get_secret_value()):
        return {"email": s.owner_email or "local-dev", "dev": True}
    return verify_token(creds.credentials)
