# UI bug — contextual action feedback instead of raw JSON error pages

Status: OPEN
Area: Web UI / browser-triggered actions / error and success feedback

## Observed behavior

From the MikroTik Device workspace, clicking `Aggiorna snapshot` while the Device cannot execute the snapshot causes the browser to leave the NSM interface and render FastAPI's raw JSON error response on a new page:

```json
{"detail":"Lo snapshot richiede un MikroTik online con agent NSM."}
```

The action originates from:

`Device → Configurazione → Risorse di sistema → Aggiorna snapshot`

The backend currently raises an expected business-state `HTTPException(409, ...)` when the Device is not online. Because the request is a normal HTML form POST, the default FastAPI error response replaces the entire application page with JSON.

This is technically valid for an API endpoint but is not an acceptable operator experience for a browser UI action.

## Product requirement

Browser-triggered NSM actions must preserve the operator's current UI context.

Expected operational errors, warnings and confirmations must be presented **inside the NSM GUI**, without navigating to a raw JSON document.

The preferred visual treatment can be a toast/notification bubble, dismissible banner, compact contextual message or equivalent component, provided that:

- it is immediately understandable;
- it uses the existing NSM visual language;
- it clearly distinguishes success, information, warning and error;
- it keeps the user on the originating page/workspace;
- it does not require the user to use the browser Back button;
- it is accessible and remains readable in dark/light themes;
- important messages are not hidden solely behind animation or hover state.

## Recommended interaction model

### Expected business/validation error

Example:

```text
Impossibile aggiornare lo snapshot
Il MikroTik deve essere online con agent NSM attivo.
```

Render as an error/warning toast or inline alert in the current `Configurazione` page.

### Successful action

Example:

```text
Snapshot accodato
La richiesta è stata inviata all'agent MikroTik.
```

Render as a success toast/banner after the POST/redirect cycle.

### Informational/no-op action

Example:

```text
Snapshot già in coda
Esiste già una richiesta pendente per questa sezione.
```

Render as info/warning without leaving the page.

## Architecture requirement

Do **not** globally replace JSON errors for real API/agent endpoints.

NSM has two different consumers:

1. human-facing browser UI routes;
2. machine-facing API, agent, connector and upload routes.

Machine-facing endpoints must retain structured HTTP/JSON behavior where appropriate.

The fix therefore needs an explicit UI feedback layer for HTML actions rather than a blanket exception handler that converts every `HTTPException` into HTML.

## Recommended implementation pattern

Use a consistent Post/Redirect/Get flow for browser actions:

1. operator submits an HTML form/action;
2. backend validates capability/current state;
3. expected user-facing failure is converted to a UI message;
4. backend redirects with HTTP 303 to the originating or canonical workspace URL;
5. the base UI renders the message once;
6. refresh must not repeat the original POST.

A small server-side flash-message mechanism is preferred over placing arbitrary backend error text directly in query parameters.

Suggested normalized message structure:

```text
level: success | info | warning | error
title: short operator-facing title
message: concise explanatory text
return_to: validated internal path
```

The implementation may use session-backed flash state or another equivalent server-side one-shot mechanism already compatible with the current authentication/session architecture.

## Shared GUI component

Create one reusable component/partial rendered from the common application layout, rather than implementing per-page message HTML.

It should support at least:

- `success`;
- `info`;
- `warning`;
- `error`.

Recommended behavior:

- visible immediately after navigation;
- close/dismiss control;
- optional auto-dismiss only for low-severity success/info messages;
- warning/error should remain visible long enough to read or until explicitly dismissed;
- `aria-live`/appropriate accessibility semantics;
- no layout-breaking raw stack traces or JSON;
- no secret/token/internal exception details.

The repository already contains several page-specific `.alert` patterns, so the global component should reuse or normalize that visual language instead of introducing an unrelated design system.

## First confirmed route

`POST /devices/{device_id}/snapshot/{section}`

Current behavior when the Device is not online:

```python
raise HTTPException(
    409,
    "Lo snapshot richiede un MikroTik online con agent NSM.",
)
```

For a browser-originated request this must become contextual GUI feedback and return the operator to the same Configuration section.

## Systematic audit required

The screenshot confirms one route, but the same pattern is likely present elsewhere because many human-facing POST handlers raise `HTTPException(400/403/404/409)` directly.

Audit **all browser-triggered form/action routes**, including at minimum:

### MikroTik

- snapshot/configuration actions;
- diagnostics actions;
- agent install/reinstall/update actions;
- configuration-history/baseline actions;
- firmware readiness/planning/staging/activation;
- RouterBOOT actions;
- backup actions;
- interface/policy-health actions where forms exist.

### Ubiquiti / UISP

- connector configuration/test;
- preview/association;
- refresh/sync actions.

### Backup

- policy creation/update/delete;
- manual backup;
- artifact/history actions intended for the operator.

### Administration

- user management;
- API-key actions;
- branding/settings;
- integrations.

### Inventory/customer workflow

- create/update/delete operations;
- CSV import;
- Site/Device association actions.

### Security / Action Center / Audit

- acknowledge/resolve/remediation actions;
- report/export operations that can fail due to current state or permission.

This audit does **not** mean suppressing genuine HTTP status codes from machine endpoints. It means ensuring browser forms never dump raw JSON as the final operator UI for an expected application condition.

## Error classification

The implementation should distinguish:

### Expected operator/business-state errors

Examples:

- Device offline;
- capability not supported;
- agent not associated;
- action already queued;
- invalid current firmware state;
- missing prerequisite backup;
- duplicate policy/name;
- connector not configured;
- unsupported legacy transport.

These belong in contextual GUI feedback.

### Authentication/authorization

A denied browser action should remain inside a branded NSM error/feedback experience where practical, without exposing implementation detail.

### Unexpected internal errors

Do not convert unhandled 500 errors into misleading success-style toasts. They may use a branded error page or safe generic contextual error while retaining server logging/correlation data.

### API/agent errors

Keep structured JSON/HTTP semantics.

## Return-context safety

If a generic `return_to` / `next` parameter is introduced:

- allow internal application paths only;
- reject absolute/external URLs;
- prevent open redirects;
- fall back to the canonical resource page if the return path is missing or invalid.

## UX acceptance criteria

- [ ] Clicking `Aggiorna snapshot` on an unavailable/offline MikroTik does not open a raw JSON page.
- [ ] The operator remains in `Device → Configurazione` on the same relevant section.
- [ ] The message `Lo snapshot richiede un MikroTik online con agent NSM.` is shown in a clear branded GUI component.
- [ ] Successful browser actions can use the same component for confirmation.
- [ ] The component supports success/info/warning/error states.
- [ ] The component works in dark and light themes.
- [ ] Browser refresh after a POST does not repeat the action.
- [ ] Messages do not expose tokens, secrets, stack traces or internal-only details.
- [ ] The same handling is applied to other human-facing routes discovered by the audit.
- [ ] Machine-facing API/agent endpoints continue returning the structured responses expected by their clients.

## Regression coverage

Add tests for at least:

1. failed MikroTik snapshot action from an HTML page;
2. successful/queued action feedback;
3. one non-MikroTik browser action with a business-state error;
4. one administration/browser action with a conflict error;
5. an API/agent endpoint proving JSON semantics remain unchanged;
6. safe return-path validation;
7. one-shot message consumption after redirect.

The main regression assertion is that browser-facing actions return/redirect to HTML application context and never finish on FastAPI's raw `{"detail": ...}` response for expected user-action errors.

## Scope boundary

This PR defines a cross-UI feedback foundation and the audit needed to apply it consistently.

It does not change the semantics of agent APIs, public APIs or connector machine protocols, and it should not be mixed with unrelated visual-navigation fixes such as the MikroTik duplicate Configuration menu or the missing Interfaces tab.
