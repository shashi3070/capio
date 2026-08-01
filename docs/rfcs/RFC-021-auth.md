# RFC-021: Authentication & Authorization Capabilities

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Authentication** and **Authorization** capabilities: authenticating the
caller from the propagation carrier, establishing the `AuthPrincipal` on the Context (RFC-006),
and enforcing authorization policies (RBAC/ABAC/custom) before the wrapped callable runs. Consumed
via `@use.auth(...)`. Capio is NOT an identity provider (RFC-001 §7.8) — it delegates to
authorities (OIDC, JWT verifiers, Keycloak, custom providers) via auth backends (RFC-015 §3.7).

## 2. API

```python
@use.auth(
    provider="oidc",               # auth backend name (RFC-015 §3.7)
    scopes={"read"},               # required OAuth scopes
    require_roles={"service"},     # RBAC roles (any-of)
    require_all_roles=False,       # True = all-of
    policy="policy_name",          # ABAC/OPA-style policy (RFC-002 §7.5)
    reject="deny",                 # "deny" (raise) | "bypass" (fail-safe allow) | "custom"
    cache_principal=True,          # cache verification results (RFC-016)
    principal_ttl="5m",            # cache TTL for verified principals
)
def admin_action(payload: dict) -> dict: ...
```

### 2.1 Context effect

On success the capability sets:

```
ctx.auth = AuthPrincipal(
    subject="user:u-42",
    claims={...},                 # decoded, validated claims
    scopes={"read", "write"},
    roles={"service"},
    attributes={...},             # from policy/IDP
    provider="oidc",
    verified_at=<monotonic>,
)
```

Downstream capabilities read `ctx.auth` (e.g. rate_limit keys per user, RFC-018 §4.2; audit
actor, RFC-020 §3.2). `ctx.auth` is `None` until set, and remains `None` if the auth capability
is absent.

## 3. Authentication backends & flows

`AuthBackend` (RFC-015 §3.7):

| Backend | Flow | Carrier used |
| ------- | ---- | ------------ |
| JWT | verify `JWT`/`OAuth2 Bearer` token (RS256/ES256/HMAC), check exp/nbf/iss/aud, optional `jwks` caching. | `Authorization` header / MCP metadata |
| OAuth2/OIDC | introspection or local verification against IDP; optional `PKCE` exchange via integration. | token from carrier |
| API Key | lookup key → principal (key hashed at rest; RFC-026 §6). | `X-API-Key` |
| LDAP | bind + group lookup. | session identity |
| Keycloak / Auth0 / Azure AD | OIDC provider adapters. | token |
| Custom | `authenticate(carrier) -> Principal` for private systems. | any |

- Verification results may be cached (`cache_principal=True`) in the cache backend to avoid
  re-verifying every call; the cache key is the token fingerprint (sha256), never the token
  (RFC-026).
- **Async contract**: sync backends (LDAP, JWKS fetch) declare `blocking=True` and are dispatched
  to an executor on the async path (RFC-015 §5, RFC-024) so the loop is never blocked.

## 4. Authorization

### 4.1 RBAC (role-based)

- `require_roles` (any-of by default) checks `principal.roles`.
- `scopes` checks `principal.scopes` (OAuth scope semantics: all listed required).

### 4.2 ABAC / policy (attribute-based)

- `policy` names a registered policy (RFC-014 validator registry): a declarative rule set over
  `(principal, resource, action, context)` — e.g. `"can(app='payments', amount<=10000, region='US')"`.
- Evaluated by a policy engine (OPA/Rego via `capio-opa`, custom `PolicyEngine`, or a pure-Python
  DSL). The policy backend implements `authorize(principal, resource, action) -> Decision`
  (RFC-015 §3.7).
- `Decision` is one of allow/deny/review; `review` routes to human approval (RFC-023 §6
  human-in-the-loop).

### 4.3 Resource & action inference

- `action` defaults to the decorated function name; `resource` from `resource` option
  (`(ctx) -> str`).
- Plugins may extend `authorize` with data-plane checks (e.g. row-level ACL) via a
  `before_auth` hook (RFC-007 §3.2).

## 5. Rejection semantics

| `reject` | On deny/unauthenticated |
| -------- | ----------------------- |
| `deny` (default) | raise `AuthenticationError` / `AuthorizationError` (RFC-025). Typed, catchable. |
| `bypass` | fail-safe allow + emit `auth.bypassed` event; record on context. Explicitly NOT for production auth decisions (violates RFC-001 §3.8 intent). |
| `custom` | call `(ctx, reason) -> None | result`; may short-circuit. |

- Denials emit `auth.denied`; grants emit `auth.granted` (RFC-008 §2.5).
- Metrics: `auth.requests_total`, `auth.denied_total`, `auth.verify_latency_ms` (RFC-019).

## 6. Multi-tenancy

- When enabled, the auth capability derives `tenant_id` from the carrier/claims and sets it on
  the Context (`ctx.tenant_id`), driving tenant-scoped rate limits, cache namespaces, audit
  records, and policy evaluation (RFC-009 §10, RFC-018, RFC-016).
- Tenant isolation contract tests (RFC-029) verify no cross-tenant data leakage through cache or
  key building (RFC-016 §3.1 key namespace).

## 7. Interaction

- **Ordering**: `auth` is the outermost capability (priority 1000, RFC-005 §4.2) — nothing runs
  before the caller is authenticated.
- **Auth + validation**: validation (900) runs after auth, so schema errors surface only for
  authenticated callers.
- **Auth + rate limit**: rate limits key off `ctx.auth` (per-user quotas).
- **Auth + cache**: cache step (750) sits inside auth — unauthorized calls never touch the cache.
- **Auth + audit**: audit records the authenticated actor (RFC-020 §3.2).
- **Auth + AI**: for agent/LLM call sites, the principal becomes the identity attached to tool
  calls and audit records (RFC-030 §8) — "who asked the agent to do what, with which tools".

## 8. Document Dependencies

- Concepts: RFC-002 (§7.5); context principal: RFC-006; hooks: RFC-007; config: RFC-009;
  backends: RFC-015; errors: RFC-025; security: RFC-026; AI: RFC-030; CLI: RFC-028.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
