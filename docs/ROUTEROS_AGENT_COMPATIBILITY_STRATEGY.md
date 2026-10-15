# RouterOS agent compatibility strategy

## Goal

Design the MikroTik agent/provisioning flow so NSM can support the widest practical range of RouterOS releases without forcing one script implementation across incompatible RouterOS scripting generations.

This document is based on real compatibility problems already encountered during RouterOS 7.12.1 testing and on the operational requirement to keep older MikroTik devices manageable while newer RouterOS releases continue to evolve.

---

## Problem statement

MikroTik has changed RouterOS scripting capabilities and command behavior across firmware generations.

A single agent source that assumes the latest scripting language features can fail completely on an older RouterOS release even when most of the desired NSM functions could otherwise be implemented with older syntax.

A concrete example already encountered in real testing is RouterOS 7.12.1, where modern script functions used by a newer agent generation are not available. This required a dedicated legacy transport rather than treating all RouterOS 7.x releases as equivalent.

The architecture should therefore avoid using `RouterOS 7` as one compatibility bucket.

---

## Proposed architecture

### 1. Bootstrap first, agent second

The adoption command should install/run only a minimal bootstrap that uses the broadest practical RouterOS-compatible syntax.

The bootstrap must first collect a small compatibility fingerprint, for example:

- RouterOS version;
- architecture;
- board/model where useful;
- optionally a small set of feature probes when version alone is insufficient.

The server then chooses the correct agent implementation.

Flow:

```text
adoption command
    ↓
minimal compatibility bootstrap
    ↓
RouterOS/version/capability detection
    ↓
NSM compatibility resolver
    ↓
select compatible agent family
    ↓
deliver matching .rsc / script source
    ↓
agent installs and enrolls
    ↓
server records exact capability set
```

The bootstrap itself must not depend on syntax that would exclude older supported releases before compatibility can be detected.

---

## 2. Agent families instead of one universal script

Maintain multiple agent implementations when RouterOS generations require substantially different scripting/transport behavior.

Do **not** hard-code arbitrary version ranges before they have been validated.

Conceptually the resolver may eventually produce families such as:

```text
legacy-v1
compat-v2
modern-v1
modern-v2
```

The important contract is not the family name but:

- which RouterOS versions/features it has been tested against;
- which syntax it uses;
- which NSM capabilities it implements;
- which transport/API contract it uses.

Version boundaries should be derived from tests and MikroTik behavior, not assumptions such as `7.13+ is always modern`.

---

## 3. Prefer capability detection over version assumptions

RouterOS version is the first selector, but where practical the compatibility resolver should also support explicit feature detection.

Examples of features that may materially change the generated source or transport:

- JSON serialization/deserialization support;
- command output forms required by the collector;
- HTTP/fetch behavior;
- script syntax/functions;
- API/HTTPS behavior used by backup or snapshot operations.

This allows NSM to represent cases where two releases in the same major line have different effective capabilities.

The server should persist both:

- detected RouterOS version;
- resolved agent family/capability profile.

---

## 4. Capability matrix as a first-class object

Each agent family must explicitly declare supported capabilities.

Example capability domains:

- enrollment;
- heartbeat/current metrics;
- historical telemetry;
- inventory;
- configuration snapshot;
- binary/config backup;
- diagnostics ping;
- traceroute;
- neighbor discovery;
- device logs;
- DHCP lookup;
- support snapshot;
- remote job execution;
- agent self-update.

A capability should not be inferred only from a broad `legacy` / `modern` label.

The UI and backend should query the same capability contract so unsupported operations are disabled consistently everywhere.

---

## 5. Graceful degradation

An older RouterOS release should not become completely unusable because one advanced feature is unavailable.

For example, if a release can support:

- enrollment;
- inventory;
- heartbeat;
- ping;
- traceroute;

but cannot safely support:

- configuration snapshot;
- HTTPS backup;
- full historical telemetry;

then the supported functions should continue to operate.

The device should be shown as partially capable, not generically broken.

This is especially important for long-lived ISP/WISP installations where firmware cannot always be upgraded immediately.

---

## 6. Current real-device reference: RouterOS 7.12.1

Real testing on:

- MikroTik `wAP R`;
- RouterOS `7.12.1 (stable)`;
- architecture `mipsbe`;

shows the current legacy path can enroll the device and expose basic inventory/current state.

The UI currently reports a legacy transport and explicitly marks several capabilities unavailable, including:

- configuration snapshot;
- HTTPS backup;
- historical telemetry.

Some diagnostic/job functions operate successfully.

This is a useful reference profile for regression testing, but it should not be treated as the complete definition of all old RouterOS releases.

---

## 7. Provisioning resolver behavior

The server-side provisioning endpoint should resolve an agent package using a structure conceptually similar to:

```text
resolve_agent(
    routeros_version,
    architecture,
    detected_features
) -> {
    agent_family,
    agent_version,
    source_template,
    transport_version,
    capabilities
}
```

The selected result should be deterministic and auditable.

Record on the device:

- RouterOS version at enrollment;
- agent family;
- agent version;
- transport version;
- compatibility profile/version;
- capability set;
- last compatibility evaluation timestamp.

---

## 8. Re-evaluate after firmware upgrade/downgrade

Compatibility selection must not happen only once at first adoption.

When NSM detects a RouterOS version change:

1. recompute the compatibility profile;
2. determine whether the currently installed agent remains valid;
3. if necessary, schedule or recommend migration to the matching agent family;
4. update capability state only after the compatible agent is confirmed installed/running.

This prevents a device adopted on an old firmware from remaining permanently tied to the legacy agent after an upgrade.

The same logic should handle downgrade scenarios safely.

---

## 9. Agent source generation and delivery

Prefer server-generated/downloaded `.rsc` or equivalent script sources per compatibility family rather than maintaining one large script full of fragile version-specific branches.

Recommended principles:

- keep family-specific source small and testable;
- share server-side Python/domain logic where possible;
- isolate RouterOS syntax differences in templates/generators;
- avoid sending unsupported syntax to a device at all;
- expose agent source/version checksum for diagnostics;
- retain the previous working family during controlled migration where feasible.

---

## 10. Compatibility test matrix

Add a maintained test matrix rather than validating only with HTTP TestClient payloads.

For every supported RouterOS compatibility family, track at least:

- representative RouterOS versions;
- architecture;
- bootstrap parsing/execution;
- enrollment;
- heartbeat;
- job polling/completion;
- each declared capability;
- unsupported capability failure behavior;
- upgrade/migration between agent families.

Where real RouterOS CI execution is not practical, maintain captured fixtures generated from real devices and supplement them with periodic hardware/CHR validation.

Critical rule: a server-side JSON request test is not sufficient proof that the actual RouterOS-generated script/body is valid.

---

## 11. Compatibility registry

Maintain a registry in code/data rather than scattering version checks across templates, routes and UI code.

Conceptual entry:

```yaml
family: legacy-v1
match:
  routeros: <validated rule>
transport: bodyless-v1
source_template: mikrotik_legacy_v1.rsc
capabilities:
  enrollment: true
  current_metrics: true
  historical_telemetry: false
  config_snapshot: false
  https_backup: false
  ping: true
  traceroute: true
```

Exact fields and version rules should follow the existing project architecture.

The registry should be the authoritative source used by:

- provisioning;
- device capability display;
- remote-job eligibility;
- backup/snapshot actions;
- monitoring UI;
- agent migration logic.

---

## 12. Unknown/unvalidated RouterOS releases

Do not silently send the newest agent to an unknown firmware.

Safer behavior:

1. identify that the release is not validated;
2. run only the conservative bootstrap/probe path;
3. select the safest compatible family if feature probes prove it;
4. otherwise stop with a clear `unsupported/unvalidated RouterOS` message and preserve the enrollment token for retry if appropriate.

This is preferable to installing a script that partially parses and leaves the router in an uncertain state.

---

## 13. UI requirements

The Agent tab should expose at least:

- detected RouterOS version;
- selected agent family;
- installed agent version;
- transport version;
- compatibility state (`validated`, `legacy`, `unvalidated`, etc.);
- capabilities supported/not supported;
- compatibility reason for disabled capabilities;
- whether an agent migration is available after firmware change.

Do not make the user infer compatibility only from an agent version string.

---

## 14. Logging and diagnostics

Enrollment and agent migration logs should make compatibility decisions visible.

Useful fields:

- detected RouterOS version;
- matched compatibility rule;
- selected agent family;
- selected template/source revision;
- detected feature probes;
- rejected alternatives and reason where useful;
- enrollment/migration result.

This is important when diagnosing devices across many RouterOS releases in production.

---

## 15. Implementation sequencing

Suggested dependency-aware sequence:

1. inventory all RouterOS-specific syntax/features currently used by legacy and modern agents;
2. create the centralized compatibility registry/resolver;
3. make bootstrap version/feature detection authoritative;
4. route source generation through the resolver;
5. persist agent family + capability profile on the device;
6. make UI/actions consume the same capability profile;
7. add re-evaluation after RouterOS version changes;
8. expand real-device/fixture compatibility tests;
9. split additional agent families only where testing proves the existing families are insufficient.

---

## Acceptance criteria

The compatibility architecture can be considered effective when:

- an older validated RouterOS release receives only syntax it supports;
- a newer validated release receives the correct modern source;
- one unsupported advanced capability does not break basic enrollment/monitoring;
- the UI accurately reflects what that exact device/agent profile can execute;
- firmware upgrade/downgrade causes compatibility re-evaluation;
- unknown releases fail safely or use a proven capability-based fallback;
- compatibility behavior is covered by regression tests using realistic RouterOS-generated data.

---

## Scope note

This PR is intentionally documentation/design only. It does not prescribe final RouterOS version boundaries yet. Those boundaries should be established from current code review plus real-device testing, including additional newer RouterOS releases.
