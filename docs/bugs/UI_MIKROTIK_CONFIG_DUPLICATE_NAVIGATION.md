# UI bug — duplicated navigation in MikroTik Configuration

Status: OPEN
Area: MikroTik Device workspace / Configuration

## Observed behavior

The `Configurazione` page renders two overlapping navigation/action groups.

The configuration subsection row already contains:

- Risorse di sistema
- Indirizzi IP
- Route IP
- Interfacce
- Firewall
- Sessioni PPP
- DHCP leases
- Warning / error log
- Cronologia & drift

Inside the panel header a second `page-actions` group repeats several of the same destinations:

- Interfacce
- Indirizzi IP
- Route
- Firewall
- DHCP
- Cronologia

This duplication makes the page look malformed and creates two competing navigation levels for the same functions.

## Source identified

`app/app/templates/mikrotik_configuration_v2.html`

The template first renders the canonical subsection navigation from `snapshot_sections`, then renders direct links to Interfaces/IP/Routes/Firewall/DHCP/History again inside the panel header.

## Expected behavior

Use a single authoritative subsection navigation row.

Recommended behavior:

1. keep `mt-section-tabs` as the configuration subsection navigation;
2. remove equivalent navigation links from `page-actions`;
3. keep only actions that operate on the currently selected subsection, e.g. `Aggiorna snapshot` when supported;
4. expose `Cronologia & drift` only once in this page;
5. preserve legacy capability warnings and historical-data visibility.

## Acceptance criteria

- [ ] No duplicate navigation links for Interfacce, Indirizzi IP, Route, Firewall, DHCP or Cronologia.
- [ ] Active configuration subsection remains highlighted.
- [ ] `Aggiorna snapshot` remains available only when the selected section is executable and the user has permission.
- [ ] Legacy RouterOS warning/empty states remain unchanged.
- [ ] Existing subsection routes continue to work.
- [ ] Add a smoke/UI regression assertion to prevent duplicated navigation from returning.

## Scope boundary

This PR tracks only duplicated/malformed navigation inside `Configurazione`.

The separate problem where the top-level `Interfacce` tab is visible only on some MikroTik workspace pages is tracked independently.
