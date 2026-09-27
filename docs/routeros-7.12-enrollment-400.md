# RouterOS 7.12.1 real enrollment: HTTP 400 diagnostic

## Purpose

This note records the first real-device test of the RouterOS 7.12 legacy enrollment path after Core 0.28, so development can continue without reconstructing the context from chat history.

No application code is changed by this PR.

## Device / environment

- Device: MikroTik wAP R
- Identity observed: `W-AP-R-CDA_NET`
- RouterOS: `7.12.1 (stable)`
- NSM server: `http://172.31.0.28`
- Bootstrap endpoint: `/api/v1/enrollment/mikrotik/bootstrap?token=<token>`

## Real-device result

The bootstrap downloads successfully and RouterOS no longer reports syntax errors related to `:serialize` / `:deserialize`.

Observed output:

```text
NSM preflight OK - RouterOS 7.12.1 (stable)
      status: finished
  downloaded: 3KiB
       total: 3KiB
    duration: 1s

failure: closing connection: <400 Bad Request> 172.31.0.28:80 (5)
```

This means:

1. RouterOS can reach NSM over HTTP.
2. The bootstrap GET succeeds.
3. The enrollment token is valid enough to retrieve the bootstrap.
4. The `.rsc` executes without the previous unsupported `:serialize` syntax failure.
5. The failure occurs on the subsequent POST from the bootstrap to `/api/v1/agents/mikrotik/enroll-legacy`.

## Current implementation

The legacy bootstrap is generated in:

- `app/app/mikrotik_legacy.py`
- `_legacy_bootstrap_script()`

The bootstrap manually builds JSON and posts it to:

```text
/api/v1/agents/mikrotik/enroll-legacy
```

The legacy endpoint calls the existing enrollment implementation:

```python
result = await agent.mikrotik_enroll(request)
```

The underlying `mikrotik_enroll()` implementation can return HTTP 400 for cases including:

- invalid JSON (`JSON non valido.`)
- non-object JSON (`Payload JSON non valido.`)
- missing token (`Token mancante.`)
- device invalid for enrollment (`Device non valido.`)

A bad/expired enrollment token normally produces HTTP 401, not 400.

Since the bootstrap GET just validated the same enrollment/device before returning the script, the most likely remaining issue is the exact payload produced by RouterOS 7.12.1 or how `/tool fetch` transmits `http-data=$nsmJson`.

## Important CI coverage gap

Current legacy smoke tests validate the API using FastAPI `TestClient` with a Python-created JSON body. They also verify that the generated script does not contain `:serialize` / `:deserialize`.

They do **not** execute the generated bootstrap inside a real RouterOS 7.12 parser/runtime and therefore do not verify the exact JSON string produced by the RouterOS script.

So this path is covered today:

```text
Python TestClient -> valid JSON -> enroll-legacy -> 200
```

but the failing real path is:

```text
RouterOS 7.12.1 -> manual JSON builder -> /tool fetch -> enroll-legacy -> 400
```

## Recommended next diagnostic

Before changing implementation, identify the exact HTTP 400 detail emitted by NSM during the real enrollment attempt.

Useful things to capture server-side:

- request path (`/api/v1/agents/mikrotik/enroll-legacy`)
- response body/detail for the 400
- raw request body or a safely redacted representation
- content length and content type

Likely outcomes:

- `JSON non valido.` -> RouterOS-generated JSON is malformed
- `Payload JSON non valido.` -> decoded body is valid JSON but not an object
- `Token mancante.` -> JSON arrived but the `token` member did not parse/persist as expected
- `Device non valido.` -> enrollment/device association needs investigation

## Production-readiness conclusion

The RouterOS 7.12 legacy path should not yet be considered production-ready based only on CI. The implementation structure is present and the syntax incompatibility is solved, but the real-device RouterOS-to-NSM enrollment POST still needs one final transport/payload diagnostic.

Do not solve this by upgrading the test router yet; keeping it on RouterOS 7.12.1 is useful to validate actual legacy compatibility.
