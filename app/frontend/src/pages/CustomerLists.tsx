import { CustomerScoped } from "../app/CustomerScope";
import { ActionCenter } from "./ActionCenter";
import { AuditEvents } from "./AuditEvents";
import { Compliance } from "./Compliance";
import { BackupsOverview } from "./BackupsOverview";
import { Devices } from "./Devices";
import { Firmware } from "./Firmware";
import { Incidents } from "./Incidents";
import { Sla } from "./Sla";
import { Vulnerabilities, VulnerabilityDetail } from "./Vulnerabilities";

/** The global lists shown as tabs of a customer: same page, that customer only, the customer's header kept above. */
export function CustomerDevices() {
  return <CustomerScoped><Devices /></CustomerScoped>;
}

export function CustomerBackups() {
  return <CustomerScoped><BackupsOverview /></CustomerScoped>;
}

export function CustomerHistory() {
  return <CustomerScoped><AuditEvents /></CustomerScoped>;
}

export function CustomerActions() {
  return <CustomerScoped><ActionCenter /></CustomerScoped>;
}

export function CustomerIncidents() {
  return <CustomerScoped><Incidents /></CustomerScoped>;
}

export function CustomerSla() {
  return <CustomerScoped><Sla /></CustomerScoped>;
}

export function CustomerVulnerabilities() {
  return <CustomerScoped><Vulnerabilities /></CustomerScoped>;
}

export function CustomerVulnerability() {
  return <CustomerScoped><VulnerabilityDetail /></CustomerScoped>;
}

export function CustomerFirmware() {
  return <CustomerScoped><Firmware /></CustomerScoped>;
}

export function CustomerCompliance() {
  return <CustomerScoped><Compliance /></CustomerScoped>;
}
