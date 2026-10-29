import { CustomerScoped } from "../app/CustomerScope";
import { AuditEvents } from "./AuditEvents";
import { BackupsOverview } from "./BackupsOverview";
import { Devices } from "./Devices";

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
