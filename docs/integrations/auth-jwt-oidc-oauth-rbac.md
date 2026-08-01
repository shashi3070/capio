# Authentication & authorization: JWT, OIDC, OAuth 2.0, RBAC

The `auth` capability is **provider-agnostic**. You supply one callable
(`provider`) that turns an incoming request into an identity, and Capio enforces
scope and policy checks around your wrapped call:

```python
@use.auth(
    provider=my_provider,        # (ctx) -> dict | None      (identity or anonymous)
    scopes=["read", "write"],    # str | list | (ctx) -> list, all required
    policy=my_policy,            # (identity, ctx) -> bool   (optional extra gate)
    required=True,               # None identity -> AuthenticationError
)
def delete_user(user_id, token): ...
```

Identity shape:

```python
{"subject": "alice", "scopes": ["read", "write"], "roles": ["admin"], "claims": {...}}
```

- `required=True` + `provider` returns `None` → `AuthenticationError`.
- A required scope missing from `identity["scopes"]` → `AuthorizationError`.
- `policy` returns `False` → `PolicyEvaluationError`.

The provider reads the token however your app passes it. In the examples below
the token is a keyword argument (`token`) — the same pattern works if you read
it from `ctx.carrier` (headers) or a validated `Context` field.

Failures emit `auth.authenticated` / `auth.denied` events; the authenticated
identity is available to inner capabilities as `ctx.auth`.

---

## 1. JWT (HS256 / RS256)

### 1.1 PyJWT (recommended for real tokens)

```bash
pip install pyjwt
```

```python
# jwt_auth.py
import jwt


def hmac_jwt_provider(secret: str, issuer: str | None = None, algorithms=("HS256",)):
    def provider(ctx):
        token = ctx.kwargs.get("token")
        if not token:
            return None
        try:
            claims = jwt.decode(
                token, secret, algorithms=algorithms,
                audience=None, issuer=issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.InvalidTokenError:
            return None
        return {
            "subject": claims.get("sub"),
            "scopes": claims.get("scope", "").split(),
            "roles": claims.get("roles", []),
            "claims": claims,
        }
    return provider


def rsa_jwt_provider(public_key: str, issuer: str, audience: str):
    def provider(ctx):
        token = ctx.kwargs.get("token")
        if not token:
            return None
        try:
            claims = jwt.decode(
                token, public_key, algorithms=["RS256"],
                audience=audience, issuer=issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.InvalidTokenError:
            return None
        return {
            "subject": claims.get("sub"),
            "scopes": claims.get("scope", "").split(),
            "roles": claims.get("roles", []),
            "claims": claims,
        }
    return provider
```

Wire up:

```python
from capio import use
from jwt_auth import hmac_jwt_provider

@use.auth(provider=hmac_jwt_provider("change-me", issuer="capio-example"))
@use.context()
def profile(token, ctx):
    return {"actor": ctx.auth["subject"], "scopes": ctx.auth["scopes"]}

print(profile(token="<valid jwt>"))
```

### 1.2 No third-party crypto (stdlib only)

Capio is dependency-free, and JWT HS256 only needs HMAC-SHA256. A pure-stdlib
verifier (for HS256 tokens you or your IdP issued with the same secret):

```python
# stdlib_jwt.py
import base64, hashlib, hmac, json, time


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(part: str) -> bytes:
    pad = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + pad)


def stdlib_hmac_jwt_provider(secret: str, max_age: int = 300):
    def provider(ctx):
        token = ctx.kwargs.get("token")
        if not token or token.count(".") != 2:
            return None
        header, payload, signature = token.split(".")
        expect = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url(expect), signature):
            return None
        claims = json.loads(_b64url_decode(payload))
        if claims.get("exp", 0) < time.time() + max_age and claims.get("exp") is not None:
            if claims["exp"] < time.time():
                return None
        return {"subject": claims.get("sub"), "scopes": claims.get("scope", "").split(),
                "roles": claims.get("roles", []), "claims": claims}
    return provider
```

## 2. OIDC (validate against an IdP's JWKS)

The provider fetches the discovery document once, then validates the `id_token`
signature against the IdP's rotating JWKS and enforces `iss` + `aud` + `exp`.

```bash
pip install pyjwt requests
```

```python
# oidc_auth.py
import jwt
from jwt import PyJWKClient


class OidcProvider:
    def __init__(self, issuer: str, client_id: str, timeout: float = 5.0):
        import requests
        discovery = requests.get(
            f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=timeout
        ).json()
        self._jwks_client = PyJWKClient(discovery["jwks_uri"])
        self._issuer = issuer
        self._client_id = client_id

    def __call__(self, ctx):
        token = ctx.kwargs.get("token")
        if not token:
            return None
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256", "ES256"],
                audience=self._client_id, issuer=self._issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        return {
            "subject": claims.get("sub"),
            "scopes": claims.get("scope", "").split(),
            "roles": claims.get("roles", []),
            "claims": claims,
        }


@use.auth(provider=OidcProvider("https://accounts.example.com", "my-api-client"))
def me(token, ...): ...
```

## 3. OAuth 2.0 (resource-server introspection)

When the token is an opaque `access_token`, validate it against the
authorization server's introspection endpoint (`RFC 7662`):

```python
# oauth_introspection.py
import requests


def oauth_introspection_provider(introspection_url, client_id, client_secret, timeout=5.0):
    def provider(ctx):
        token = ctx.kwargs.get("token")
        if not token:
            return None
        try:
            resp = requests.post(
                introspection_url,
                auth=(client_id, client_secret),
                data={"token": token},
                timeout=timeout,
            )
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        info = resp.json()
        if not info.get("active"):
            return None
        return {
            "subject": info.get("sub") or info.get("username"),
            "scopes": (info.get("scope") or "").split(),
            "roles": info.get("roles") or [],
            "claims": info,
        }
    return provider
```

## 4. RBAC (roles + policies on top of any provider)

`scopes` gives you *permission* checks; `policy` gives you arbitrary *role/ABAC*
logic. Combine them for a classic RBAC matrix:

```python
ROLE_SCOPES = {
    "viewer": {"read"},
    "editor": {"read", "write"},
    "admin":  {"read", "write", "delete"},
}


def rbac_provider(upstream_provider):
    """Promote roles -> scopes so `scopes=[...]` works with role-based IdPs."""
    def provider(ctx):
        identity = upstream_provider(ctx)
        if identity is None:
            return None
        granted = set(identity.get("scopes") or [])
        for role in identity.get("roles") or []:
            granted |= ROLE_SCOPES.get(role, set())
        identity["scopes"] = sorted(granted)
        return identity
    return provider


def require_roles(*roles):
    """Policy gate: any of the given roles (or matching scope) is allowed."""
    def policy(identity, ctx):
        granted = set(identity.get("roles") or [])
        granted |= set(identity.get("scopes") or [])
        return bool(set(roles) & granted)
    return policy
```

Apply:

```python
@use.auth(
    provider=rbac_provider(hmac_jwt_provider("change-me")),
    scopes=["write"],                                  # fails -> AuthorizationError
    policy=require_roles("editor", "admin"),           # fails -> PolicyEvaluationError
    required=True,                                     # no token -> AuthenticationError
)
@use.audit(
    action="order.update",
    actor=lambda ctx: ctx.auth.get("subject", "anonymous"),
    include_payload=True,
)
def update_order(order_id, token):
    ...
```

## 5. Composing with the rest of Capio

The authenticated identity is available to every inner capability through
`ctx.auth`, so it composes naturally:

```python
@use.auth(provider=jwt_provider, scopes=["read"])
@use.trace(attributes_from=lambda ctx: {"actor": ctx.auth["subject"]})   # per-call spans
@use.audit(actor=lambda ctx: ctx.auth["subject"])
@use.cache(ttl="1m", cache_when=lambda ctx, result: ctx.auth.get("tier") == "premium")
def get_account(account_id, token):
    ...
```

Ordering: in the **chained** form above the physical order is kept (RFC-005 rule
1) — `auth` is outermost, so identity is established *before* `trace`/`audit`/
`cache` run and `ctx.auth` is always populated inside them. In the **composite**
form (`@use("auth", "cache", ...)`) capabilities run by priority descending —
highest priority is outermost — so `cache` (750) would wrap `auth` (710). When a
composed pipeline reads `ctx.auth`, write the chain so `auth` sits above the
readers.
