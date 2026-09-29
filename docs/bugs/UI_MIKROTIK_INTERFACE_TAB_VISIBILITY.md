# UI bug — MikroTik `Interfacce` tab is not consistently visible

Status: OPEN
Area: MikroTik Device workspace / top-level navigation

## Observed behavior

The top-level `Interfacce` entry is visible when the operator opens `Configurazione`, but it disappears on `Panoramica`.

The same inconsistency is present on other MikroTik workspace pages because each template maintains its own hard-coded copy of the top-level tab bar.

## Source identified

Current templates are inconsistent:

- `mikrotik_configuration_v2.html` includes `Interfacce` between `Monitor` and `Configurazione`.
- `mikrotik_workspace.html` (Panoramica) omits it.
- `mikrotik_monitor.html` omits it.
- `mikrotik_diagnostics_v2.html` omits it.
- `mikrotik_agent_status.html` omits it.
- `device_activity.html` omits it.

The dedicated `mikrotik_interfaces.html` page also uses a different layout instead of the same Device workspace top-level navigation.

## Expected behavior

The top-level MikroTik Device navigation must be stable on every page.

Canonical order:

1. Panoramica
2. Monitor
3. Interfacce
4. Configurazione
5. Diagnostica
6. Agent
7. Job & attività

Only the active state should change.

## Recommended implementation

Use a shared Jinja partial/macro for the top-level MikroTik tabs instead of maintaining several hard-coded copies.

The `Interfacce` page should use the same Device workspace shell/navigation and highlight `Interfacce` as active.

## Acceptance criteria

- [ ] `Interfacce` is visible from Panoramica.
- [ ] `Interfacce` is visible from Monitor.
- [ ] `Interfacce` is visible from Configurazione.
- [ ] `Interfacce` is visible from Diagnostica.
- [ ] `Interfacce` is visible from Agent.
- [ ] `Interfacce` is visible from Job & attività.
- [ ] Opening `Interfacce` keeps the canonical top-level navigation visible.
- [ ] `Interfacce` is highlighted as active on its page.
- [ ] Tab order is identical across all MikroTik workspace pages.
- [ ] Add regression/smoke coverage for the canonical tab set.

## Scope boundary

This PR tracks only top-level navigation consistency. It does not cover the duplicated links inside `Configurazione`, which are tracked separately.
