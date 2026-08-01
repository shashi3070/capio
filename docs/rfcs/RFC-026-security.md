# RFC-026: Security Model & Plugin Sandboxing

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies Capio's **security model**: trust boundaries, plugin permissioning, signature
verification, secrets handling, encryption, redaction, and the **in-process containment** rules
(plugin sandboxing as a trust-and-containment model, not an OS boundary — RFC-001 §7.9). It also
covers **AI-specific security**: prompt injection resistance, tool-call permissioning, data
isolation in agent workflows, and MCP trust boundaries (RFC-030).

## 2. Trust model

### 2.1 Trust tiers

| Tier | Artifact | Trust level |
| ---- | -------- | ----------- |
| Core | the `capio` base package | trusted (official release, signed) |
| Verified | plugins signed by a trusted publisher | trusted after signature check (§5) |
| Community | unsigned/third-party plugins | restricted by default permissions (§4) |
| Runtime data | inbound carriers, message payloads, prompts | UNtrusted by default |

The default position: **inbound data is untrusted**; plugin code is trusted only up to its
declared and granted permissions.

### 2.2 Trust decisions

- `capio doctor` (RFC-028) reports the trust posture of every installed plugin.
- Runtime config `security.trust_policy`: `default-deny` (recommended), `ask`, `default-allow`
  (dev only).
- Isolated runtimes (RFC-004 §6.3) give tests and untrusted plugin evaluation a fresh trust
  boundary.

## 3. Plugin sandboxing (in-process containment)

In-process containment rules (RFC-011 §9.1), made concrete:

1. **Namespaced state**: plugin state lives under `ctx.plugin_state[<plugin>]`; cross-plugin
   state access is prohibited by contract test.
2. **No raw capability interception**: a plugin cannot wrap another plugin's step; it can only
   contribute hooks/events at declared points (RFC-007/008).
3. **Resource caps**: per-plugin event payload size (RFC-008), memory use (debug watchdog),
   executor share (RFC-024 §5), and I/O must be declared in permissions.
4. **Blocking probes**: in `debug` profile, loop-blocking and excessive resource use are
   reported as `security.violation` events and fail CI (RFC-029).
5. **Containment, not OS isolation**: subprocess/container sandboxing is future work
   (RFC-032 roadmap).

## 4. Permissions

Plugins MUST declare a `permissions` list in `capability.yaml` (RFC-013 §2). Runtime grants are
intersected with the declared set.

| Permission | Meaning | Default (community tier) |
| ---------- | ------- | ------------------------ |
| `network_client` | outbound network I/O | require explicit grant |
| `network_server` | bind/listen | deny by default |
| `fs_read` / `fs_write` | filesystem | read maybe, write deny by default |
| `subprocess` | spawn processes | deny |
| `executor` | thread/process pool | bounded by runtime cap |
| `secret_read` | access secret backend refs | deny unless granted |
| `event_publish` / `event_subscribe` | event bus | allow |
| `prompt_read` | access raw prompt content | deny unless granted (AI) |
| `tool_call` | invoke external tools (agents) | require explicit grant (AI) |

Violations raise `PluginPermissionError` (RFC-025) at load or invocation.

## 5. Signature verification

- Publishers MAY sign plugin artifacts (Ed25519). Signature stored in `capability.yaml`
  (`signature` block, RFC-013 §2.1); public keys managed via a keys directory or a keys backend.
- Verification runs at plugin load; failure raises `PluginSignatureError`.
- For verified plugins, permissions defaults relax per the publisher's trust tier.
- Verification is enforced in the `prod` profile for `security.trust_policy="verified-only"`
  (RFC-009 §7).

## 6. Secrets handling

1. Secrets live in a `SecretBackend` (RFC-015 §3.8): env, vault, cloud KMS, keyring.
2. Config references secrets by ref (`key_ref`), never by value (RFC-009 §10).
3. Secrets are cached in-memory with TTL; never logged, never in context snapshots, never in
   event payloads (RFC-006 §9, RFC-008 §2.1).
4. Masking (RFC-022 §5) is enforced at every emission boundary by default patterns
   (`*password*`, `*token*`, `*secret*`, `*api_key*`, `*card*`, `*ssn*`).
5. Encryption keys are rotated; rotation events are audited (RFC-020 §4).

## 7. Serialization safety

- Untrusted decode uses ONLY `json`/`msgpack`; `pickle`/`cloudpickle` require explicit
  `serialize.trusted=True` config and a trust declaration (RFC-022 §3.3). The default is a
  safe-serializer-only policy in `prod`.

## 8. AI & agent security

The security model extends to AI workloads (RFC-030 for full capability design):

- **Prompt injection defense**: `guardrails` capability (RFC-030 §6) runs input/output scanners;
  injection attempts are blocked/flagged with `security.violation` and audit records.
- **Tool-call permissioning**: every tool call an agent makes is gated by the auth/policy system
  (RFC-021 §4.3) — the agent acts as the *authenticated principal*; tools cannot be called with
  elevated rights. Tool registry entries declare `allowed_roles`/`policy`.
- **Data isolation**: RAG/vector memory and semantic caches are tenant-scoped (RFC-021 §6,
  RFC-030 §4); cross-tenant leakage is tested (RFC-029 contract test).
- **Model trust**: model outputs are untrusted data; they are validated (RFC-022 §2),
  serialized safely, and treated as input to the next tool call with the same policy checks.
- **Prompt/response privacy**: prompts are masked by default before logging/tracing/audit
  (RFC-022 §5.3, RFC-019 §4, RFC-020 §4).

## 9. MCP trust boundaries

MCP (RFC-030 §7) adds two trust surfaces:

- **MCP client → server**: connecting to an MCP server is connecting to an untrusted remote.
  Tool calls from the remote are validated, permissioned (never `fs_write`/`subprocess` unless
  explicitly granted), and audited.
- **MCP server → host**: exposing Capio capabilities as MCP tools means inbound MCP tool calls
  carry attacker-controlled input; they run through the FULL pipeline (auth, validate, rate
  limit, audit, guardrails) exactly like any other invocation.

## 10. Security events & audit

All security-relevant actions emit events (`security.violation`, `security.denied`,
`security.signature_failed`, `auth.denied`, `guardrail.blocked`) and, where required by policy,
audit records (RFC-020 §4). `capio doctor` (RFC-028) aggregates security posture.

## 11. Document Dependencies

- Plugin lifecycle: RFC-011; manifest: RFC-013; secrets backend: RFC-015 §3.8; serialization:
  RFC-022; masking: RFC-022 §5; audit: RFC-020; errors: RFC-025; AI/agents: RFC-030; CLI:
  RFC-028; roadmap: RFC-032.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
