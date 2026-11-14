---
project: hdgui
scope: architecture
created: 2026-11-14 20
updated: 2026-11-14 20
update_reason: Document opt-in multi-user implementation and remaining target-host validation.
status: active
agent_project: hdgui
---

# Multi-user

[Architecture index](README.md) · [Current architecture](system-overview.md) · [Operations](../guides/operations.md)

## Goal and agreed decisions

**Implemented behind `HDGUI_MULTI_USER=1`; hosted rollout still requires staging
validation.** Local single-user mode remains the default. Enabled mode makes
HDGUI a private application with independent accounts and requires a separately
configured HTTPS calculation server. See [Uberspace setup](../guides/uberspace.md).

`app/accounts.py` implements account and session lifecycle;
`app/sharing.py` manages contacts and read grants. Revision `0009` adds the
account schema without reassigning any existing profile. Authentication middleware
protects all routes except sign-in/confirmation and static assets. Until unowned
legacy profiles are assigned, authenticated ordinary pages return an explicit
setup error; the authentication routes remain available to verify the first owner.

| Concern | Decision |
|---|---|
| Sign-in | Time-limited email link; no application passwords |
| Account identity | Verified email address and an opaque, immutable user ID |
| Profiles | Any number of profiles per user; no product-level profile-count limit |
| Contacts | Add another user by email; contact request requires acceptance |
| Sharing | Explicit, per-profile, read-only grants to accepted contacts |
| Editing and deletion | Owner only; no ownership transfer in the first release |
| Privacy | Private by default; no public links, directory or email search |
| Calculations | Server-to-server HTTPS API; token never reaches the browser |

A profile is a record managed by an account, not necessarily the account holder's
own birth data. Ownership in the application does not establish the right to
store or disclose another person's data. Users must have the appropriate
permission before creating or sharing such a record.

## User journeys

**Sign in.** Enter an email address, receive a short-lived link, and complete
sign-in. The initial verified sign-in creates the account. Use the same outward
response for existing and new addresses. Configure SMTP and the public base URL
explicitly; do not derive email links from an untrusted request Host header.

**Create profiles.** The profile overview separates **Meine Profile** from
**Mit mir geteilt**. Creation always assigns the authenticated user as owner.
Both lists are paginated, with search scoped to records the user may read.
There is no hard profile-count limit, but storage, request size and calculation
rate still need operational limits.

**Add contacts.** Enter a complete email address under Kontakte. The application
sends a request without revealing whether the address already has an account.
An existing user sees it under incoming requests; a new user can first verify
the address through sign-in. Acceptance establishes a mutual contact relationship.
Requests can be declined or cancelled. No profile is disclosed by a contact request.

**Share a profile.** The owner opens Teilen, selects accepted contacts, sees
which information will be exposed, and confirms. Each recipient receives a
separate grant. The profile remains the owner's record, not a copied profile.
Owner edits become visible to recipients; the UI labels the owner and the
read-only state clearly.

**Use a shared profile.** Recipients may read the birth fields, saved chart,
stored analysis and activations, and select the profile as their own current
reference for overlays and timelines. They cannot edit, delete, re-share,
refresh or request missing natal calculations. Missing or stale saved results
are explained explicitly; the owner must calculate them.

**Revoke access.** The owner removes a grant at any time; the recipient may also
remove an incoming share. Removing a contact revokes profile grants in both
directions in the same transaction. It also removes pending requests between
the users. No automatic re-sharing occurs if the contact is later re-established.

Revocation prevents subsequent server access, but cannot erase data already
viewed, copied or saved by a recipient. The sharing confirmation must say this.
After revocation, any current-profile selection pointing to that grant is
cleared. A stale open tab receives an explicit unavailable/access-revoked state.

## Data model

Use ordinary Alembic migrations and database constraints; do not encode ownership
only in sessions or templates.

| Table or change | Required fields and invariants |
|---|---|
| `users` | Opaque ID, canonical email key with unique constraint, delivery email, verified timestamp, display name, account state, creation timestamp |
| `login_tokens` | Token digest, target email key, purpose, expiry, consumed timestamp; never persist a plaintext bearer token |
| `auth_sessions` | Digest of opaque session ID, user FK, creation/expiry and revocation timestamps |
| `contact_requests` | Requester FK, target email key, pending/accepted/declined/cancelled state, expiry; at most one active request for an unordered pair |
| `contacts` | Canonically ordered pair of user FKs, unique pair constraint, acceptance timestamp; no self-contact |
| `profile_shares` | Profile FK, recipient FK, granting owner FK, creation timestamp; unique `(profile_id, recipient_id)`; no owner-as-recipient |
| `birth_profiles.owner_user_id` | Indexed user FK; nullable only for preserved local-mode/legacy records. Enabled mode blocks ordinary access until every record has an owner, and always assigns the actor on creation |
| `user_preferences` | One row per user; current profile, locale, autoreload, display cache preference and timeline detail |
| `security_events` | Actor, action, opaque target IDs, timestamp and outcome; no tokens, birth fields or message bodies |

Define one email normalization policy for authentication, requests and contacts.
Validate syntax; normalize the domain using IDNA and lowercase it. Treat the
account lookup key case-insensitively as an explicit application policy, while
retaining the delivery spelling. Do not strip plus-tags or apply Gmail-style dot
normalization. Test collisions before migrating any addresses.

Contact and share relationships bind to immutable user IDs after acceptance,
not to a mutable email string. A change of email requires fresh verification,
revokes outstanding email tokens, and does not transfer another account's
contacts or shares. Account deletion removes its sessions, contacts, requests,
owned profiles and grants; it clears other users' affected current selections.
Deletion must not silently transfer ownership.

Foreign keys ensure referential integrity; transactional service checks enforce
that a grant is issued by the current owner to an accepted contact. Do not let
clients set `owner_user_id` or `granting_owner_id`.

## Authentication and sessions

Use cryptographically random email tokens with at least 256 bits of entropy,
single-use consumption and a short expiry, initially 15 minutes. Store their
digests, rate-limit issuing and consuming them, and expire outstanding tokens
after account disablement. The implementation sends email synchronously over TLS, records failed login
deliveries, and invalidates failed-delivery tokens. Operators see explicit errors
without revealing account existence to an unauthenticated caller. A durable
background mail queue is not included; operators must monitor delivery failures.

Email scanners must not consume a link or create an authenticated session by
following GET alone. The landing page provides an explicit confirmation POST
with a browser-bound confirmation nonce. Consume the token and create the session
atomically; duplicate concurrent confirmations must not both succeed. Remove
the token from the URL promptly, use `Referrer-Policy: no-referrer`, and redact
token-bearing paths/query strings in application and proxy logs.

Use server-side revocable sessions rather than storing identity/profile data in
the current signed client-side session. Cookies are Secure, HttpOnly and
SameSite=Lax, with explicit idle and absolute expiry. Rotate session IDs on
sign-in; support logout of this session and all sessions. Require a fresh email
verification for account deletion or email changes.

Retain CSRF protection on every state-changing operation, including contacts,
shares and preferences. Do not accept arbitrary redirect URLs after login.
Operator accounts must not gain access to profile data through an ordinary UI
role. Administrative maintenance access is a separate, audited operational concern.

## Authorization: every surface

Introduce a request-scoped authenticated actor and two shared authorization
operations, rather than scattered owner checks:

* `readable_profile(actor, profile_id)`: owner or current read grant from a contact.
* `owned_profile(actor, profile_id)`: owner only.

Apply them to HTML, JSON, SVG, downloads and service calls. Unknown and
unauthorized profile IDs receive the same 404 response. List queries apply
ownership/share predicates in the database before loading activations or charts.
Hidden buttons are only a UX measure, never an authorization boundary.

The existing `get_profile`, `current_profile` and `get_preferences` closures in
`app/main.py` currently have no actor. `/profiles/current/overlay`,
`/profiles/{id}/analysis`, saved SVG routes and `app/center_timeline.py` need the
same actor-aware scope. Update the callback used by `app/experiments.py` too.
`NatalService` must require an actor/authorization context, not accept an
unrestricted profile ID from a protected route and later reload it globally.

Recheck the grant after a long calculation and immediately before returning
private data. Recheck owner permissions and the profile calculation key before
persisting refreshed natal results. This covers revocation, account disablement,
profile deletion and edits occurring while a request is in flight.

For recipients, reading saved analysis and using cached natal activations are
allowed. Operations that would call the natal API or write refreshed profile
state are owner-only and explicit. Transit sampling remains available under
each user's bounded request budget. Cache reuse does not bypass authorization.

Private responses use `Cache-Control: no-store`; never put them in a public
reverse-proxy cache. Public ephemeris samples can be shared across users, but
profile activations, SVGs and natal snapshots remain profile-scoped.

## Personal versus shared state

Replace the singleton `UiSettings(id=1)` selection with `user_preferences`.
Two users can select different current profiles, including the same shared
profile, without changing each other's interface. Setting a current profile
checks read access; every later overlay/timeline request checks it again.

The canonical knowledge catalog and its editorial texts are application-wide,
not automatically owned by every account. Ordinary users may browse them and
select a content language, but cannot run catalog imports or modify global text
through the web UI. User-specific catalogs are outside this first release.

Separate neutral calculation caches from localized rendering. The existing
process-wide transit cache must not reuse one user's language-specific tooltips
or personal overlay for another user. Include locale in presentation cache keys
or render localized text per request from shared neutral samples.
Cache duration preferences must not change global backend credentials or
resource policy.

## External calculation service and deployment

On Uberspace, run HDGUI as a Python service with `--no-api`; configure the remote
HTTPS URL and API token through the process environment or protected local secret
storage. No Docker daemon is required on the WebUI host.

The configured API endpoint is operator-controlled, never a user-editable URL.
Verify TLS certificates, restrict backend access, use bounded timeouts and
concurrency, and report unavailable calculations explicitly. Never send user
email, contact lists or profile labels to the calculation service; send only
the fields required by its API. Inform users that required birth timestamps
are processed on that external server and define retention/logging there.

The browser talks only to HDGUI. Do not expose the calculation API token,
add a generic authenticated API proxy, or treat that token as WebUI authentication.
Use HTTPS, trusted proxy configuration and a fixed public base URL on the WebUI.
U7 uses Supervisord; U8 uses user Systemd services. Start with one WebUI worker
and SQLite if the expected load permits it; load-test concurrent logins, grants
and cache writes before choosing more workers or moving to PostgreSQL.

No profile-count cap does not mean unlimited CPU or disk. Apply per-account and
global calculation budgets, pagination, request-size limits, login/contact-email
rate limits and operator storage monitoring. Communicate exhausted budgets
explicitly; never delete existing profiles silently.

## Migration and delivery order

1. **Foundation:** define email/session policy, SMTP delivery, external API
   configuration, HTTPS deployment and shared actor-aware authorization helpers.
2. **Expand and assign ownership:** add account/preferences/contact/share tables
   and nullable ownership. In a stopped, backed-up installation, explicitly
   select one verified initial owner for existing profiles and migrate the
   singleton preferences to that account. Do not assign records to the first
   public signup or infer ownership from a profile name.
3. **Enforce isolation:** verify every existing profile has an owner before
   ordinary multi-user access is permitted. Nullable ownership remains solely
   for the explicit legacy/local mode. Replace global selection and unrestricted
   service reads. Never expose a mixed mode where some routes skip authentication.
4. **Contacts and sharing:** implement request acceptance, owner-issued read
   grants, recipient views and transactional revocation/deletion.
5. **Release gate:** verify authorization, email lifecycle, concurrency, backups,
   restore procedures and resource limits on staging before public activation.

Keep legacy preference storage only until all consumers migrate; do not retain
an implicit singleton fallback that hides incomplete actor wiring. Preserve
profile IDs, saved charts, natal snapshots and catalog text. A rollback after
new users create data requires a documented restore strategy, not dropping
ownership columns and making all data readable again.

## Acceptance criteria

Tests must use at least three distinct accounts and exercise direct HTTP
requests, not only visible controls. Prove:

* An owner creates multiple profiles and alone may edit/delete/refresh them.
* A stranger cannot read IDs, SVGs, JSON, overlays, timelines or exports.
* An accepted contact gets no profile access until an explicit grant exists.
* A recipient can read/select a granted profile but cannot mutate or re-share it.
* Revocation/contact removal/account deletion denies subsequent requests,
  clears affected selections and prevents in-flight private responses/writes.
* Concurrent sign-ins consume a token once; expired, replayed and revoked
  tokens fail; scanner GETs do not log in.
* Login/contact requests do not reveal account existence or send unbounded mail.
* Current-profile and locale preferences are isolated; shared neutral caches
  leak neither private data nor another user's rendered language.
* API outages, rate limits and missing saved analyses yield explicit states;
  recipient reads never trigger natal calculations.
* Migration preserves existing records with the explicitly selected owner,
  and a tested backup/restore works before the application is exposed.

## Operation boundaries

The first hosted release uses one worker and SQLite; independent processes need
shared database-backed budgets, and PostgreSQL deployment needs its own load
validation. The WebUI limits calculation routes to 300 budget units per account
per hour and 3000 globally; multi-sample requests consume multiple units.
Contact notifications are limited to 10 per account per day and 100 globally.
Authentication email requests have an additional global limit of 100 per hour,
alongside per-address and per-IP budgets.
POST bodies are bounded to 64 KiB. These are resource policies, not profile-count
limits. The calculation client permits at most four concurrent outbound requests.

The Uberspace script installs dependencies, migrates, checks configuration and
generates a user-service definition. It does not provision the external API,
SMTP, domain/certificates or backups, and has not been executed on a target host.
Do not expose local single-user mode publicly or infer a completed production
security assessment from this implementation.
