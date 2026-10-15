# NSM Platform — Product Scope and Priorities

Status: durable product-scope decision

This document defines the operational target of NSM and constrains how future roadmap work must be selected.

## Primary scope

NSM is primarily intended for the **management, inventory, tracking, maintenance, security and compliance evidence of devices installed at customer premises or customer-serving edge locations**.

Typical managed assets include, according to the connector/capability available:

- MikroTik routers and customer-edge/CPE devices;
- Ubiquiti AirMax AC / LTU and other Ubiquiti customer-serving devices through UISP;
- TP-Link and other CPE managed through TR-069/ACS;
- additional customer-installed network appliances added through future vendor adapters, ACS profiles or safe generic integrations.

The product should answer questions such as:

- what equipment is installed for this Customer and Site?
- what model, serial, MAC, firmware and management source does it have?
- is the Device online and when was it last verified?
- is its configuration backed up and has it changed?
- is firmware current, supported and affected by known security issues?
- is the Device EOL/EOS?
- what operational incidents or remediation actions involved it?
- what evidence can be produced for internal controls, audits and NIS2-oriented processes?

## Explicit non-priority: ISP backbone/core

The current product roadmap must **not expand into a full ISP backbone/core management platform** unless a future product decision explicitly changes this scope.

The following are currently out of scope or non-priority:

- ISP backbone topology reconstruction;
- POP-to-POP dependency mapping;
- BGP policy/route optimization;
- OSPF/MPLS/VPLS engineering and simulation;
- traffic-engineering/path-selection optimization;
- backbone capacity planning;
- automated core failover design;
- ISP-wide routing diagnostics unrelated to a managed customer-edge Device;
- NMS replacement for the operator's complete core network.

NSM may still store or use limited network information when it is directly necessary to manage a customer-installed Device, but that must not evolve silently into a backbone-management subsystem.

Before starting any new feature, development must ask:

> Does this capability improve management, tracking, maintenance, security, backup, lifecycle or compliance evidence for customer-installed devices?

If the answer is no and the feature primarily belongs to ISP-core operations, it should not be selected without an explicit product-scope decision.

## Development priority hierarchy

Preferred priority order:

1. reliable Customer/Device/Site inventory and identity;
2. vendor onboarding and synchronization;
3. monitoring and health of customer-installed devices;
4. configuration backup, history and restore evidence;
5. firmware and lifecycle management;
6. CVE/security correlation and remediation;
7. Action Center and incident management;
8. compliance baselines and evidence;
9. customer/executive/NIS2-oriented reporting;
10. additional vendor adapters and device families.

Backbone/core features do not belong in this priority sequence.

## Scope change rule

A future expansion into ISP-core/backbone management must be an explicit product decision, documented separately, rather than an incidental side effect of implementing a customer-device feature.
