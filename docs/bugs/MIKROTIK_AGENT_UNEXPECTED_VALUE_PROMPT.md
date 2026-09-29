# MikroTik agent bug — unexpected `value:` prompt during RouterOS enrollment

Status: OPEN
Area: MikroTik onboarding / generated modern agent / RouterOS runtime

## Observed behavior

On a real MikroTik running RouterOS 7.24.4, the one-shot onboarding command successfully completes the preflight and downloads/imports the NSM bootstrap. The console then reaches:

```text
NSM enrollment transport: bodyless-v1, RouterOS 7.24.4
value:
Script Error:
```

The `value:` prompt is displayed by the RouterOS console **after the bootstrap has been downloaded and the bodyless enrollment path has started**. It is not an NSM web-form field and is not part of Device creation.

The operator must never be asked to manually provide a value while the generated agent is being installed or executed.

## Reclassification

This issue was initially interpreted as an unexpected field in the NSM Device association form. Real-device evidence shows that interpretation was incorrect.

The bug belongs to the generated RouterOS enrollment/agent source path.

## Current execution path

The current bootstrap:

1. reads and normalizes the RouterOS version;
2. performs the bodyless enrollment POST;
3. receives the generated agent source;
4. installs it as `nsm-agent-heartbeat`;
5. runs the installed script immediately.

The prompt appears after the bodyless enrollment message, so investigation must focus on the returned/generated modern agent and its first execution, not on `device_new.html` or FastAPI Device form validation.

## Recent regression candidate

Core 0.49 introduced MikroTik agent self-update/source-integrity support. The modern generated source is extended with an integrity probe before the normal inventory payload:

```routeros
:local nsmAgentSourceSha512 ""
:do {
    :set nsmAgentSourceSha512 [:convert [/system script get [find where name="nsm-agent-heartbeat"] source] transform=sha512]
} on-error={}
```

This path is a strong regression candidate because it is executed at the beginning of the newly generated modern agent and was introduced immediately before the observed behavior.

The RouterOS `value:` prompt is characteristic of an interactive command invocation that RouterOS believes is missing a required/unnamed value. The implementation must therefore validate the exact generated syntax on real RouterOS instead of relying only on string/smoke tests.

This is a candidate, not a confirmed root cause until isolated on RouterOS.

## Required investigation

- capture the exact modern agent source returned for RouterOS 7.24.4 after Core 0.49;
- number the generated lines and identify which command is active immediately before the `value:` prompt;
- test the new SHA-512 integrity probe independently on the same RouterOS version;
- verify RouterOS syntax/behavior for `:convert ... transform=sha512` on the supported target versions;
- verify `/system script get [find where name="nsm-agent-heartbeat"] source` always returns a valid positional value before conversion;
- inspect all other generated commands that accept a required unnamed/value argument (`:convert`, `:serialize`, `:deserialize`, etc.);
- ensure no generated script command can fall into RouterOS interactive prompting;
- preserve rollback/source-integrity functionality using syntax validated on real devices;
- add a generated-source regression test for the corrected pattern;
- repeat full real-device enrollment on RouterOS 7.24.4 and 7.20.7 after correction.

## Useful isolation tests

Run only on the affected test router, without entering any value into an unexpected prompt:

```routeros
:put [:convert "test" transform=sha512]
```

Then, only if `nsm-agent-heartbeat` exists:

```routeros
:local s [/system script get [find where name="nsm-agent-heartbeat"] source]
:put [:len $s]
:put [:convert $s transform=sha512]
```

If one of these produces `value:`, the integrity probe path is directly implicated. If both work, inspect the next command in the generated agent source before the prompt.

## Expected behavior

A successful onboarding must be completely non-interactive after the operator pastes the one-shot command:

```text
preflight OK
bootstrap downloaded
bodyless enrollment completed
agent source installed
first heartbeat completed
```

No `value:`, `name:`, `value-name:` or other RouterOS parameter prompt may occur.

## Acceptance criteria

- [ ] RouterOS 7.24.4 onboarding never presents `value:`.
- [ ] RouterOS 7.20.7 onboarding never presents `value:`.
- [ ] Generated modern agent installs and executes without interactive input.
- [ ] First heartbeat succeeds after installation.
- [ ] Agent version/source fingerprint is reported as intended.
- [ ] Source-integrity/self-update functionality remains operational if supported.
- [ ] Any unsupported RouterOS capability fails explicitly and non-interactively.
- [ ] Regression coverage validates the final composed agent source, not just individual source fragments.
- [ ] Real-device validation result is recorded in the compatibility documentation.

## Scope boundary

This issue is limited to the RouterOS-side unexpected parameter prompt during generated-agent enrollment/execution. It does not concern the NSM Device creation web form.