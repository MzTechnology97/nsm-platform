# RouterOS 7.12.1 real enrollment: HTTP 400 diagnostic

A physical MikroTik **wAP R** running **RouterOS 7.12.1 (stable)** was tested against NSM at `http://172.31.0.28`.

The compatibility bootstrap downloaded and executed successfully and the previous `:serialize` / `:deserialize` syntax problem was gone. The next request failed with:

```text
failure: closing connection: <400 Bad Request> 172.31.0.28:80 (5)
```

The failing path was:

```text
RouterOS 7.12.1 -> bootstrap GET -> RouterOS-built JSON body -> POST /api/v1/agents/mikrotik/enroll-legacy -> HTTP 400
```

This established that Python TestClient JSON coverage did not validate the exact body produced by the RouterOS 7.12 scripting runtime.

## Core 0.33 resolution

Core 0.33 keeps the existing one-shot enrollment token and per-device credential model but removes RouterOS-generated JSON from the real legacy pairing request:

```text
RouterOS 7.12.1
  -> bootstrap GET
  -> empty POST /api/v1/agents/mikrotik/enroll-legacy?token=<one-shot>&version=7.12.1
  -> agent source
  -> authenticated header-based heartbeat
```

The legacy heartbeat also no longer needs JSON for newly installed agents. Inventory and metrics are sent through explicitly named, sanitized `X-NSM-*` headers. Old JSON legacy enrollment/heartbeat clients remain accepted for compatibility until reinstalled.

The physical 7.12.1 router should remain on that release until this bodyless path has been validated on the real device.
