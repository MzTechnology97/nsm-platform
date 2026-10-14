/**
 * Navigation of the new interface during the gradual migration.
 *
 * ``route`` is set once a section exists in React; until then the entry opens
 * the classic page (``classic``) in the same tab, with the same session.
 */
export interface Section {
  key: string;
  icon: string;
  classic: string;
  route?: string;
  permission?: string;
  group: "operations" | "security" | "governance" | "admin";
}

export const SECTIONS: Section[] = [
  { key: "dashboard", icon: "dashboard", classic: "/", group: "operations" },
  { key: "customers", icon: "customers", classic: "/customers", permission: "customers.read", group: "operations" },
  { key: "devices", icon: "devices", classic: "/devices", permission: "devices.read", group: "operations" },
  { key: "monitoring", icon: "monitoring", classic: "/operations/monitoring", permission: "monitoring.read", group: "operations" },
  { key: "agents", icon: "agents", classic: "/operations/agents", permission: "devices.read", group: "operations" },
  { key: "backups", icon: "backup", classic: "/operations/backups", permission: "backup.read", group: "operations" },
  { key: "firmware", icon: "firmware", classic: "/operations/firmware", permission: "firmware.read", group: "operations" },
  { key: "incidents", icon: "incidents", classic: "/incidents", permission: "incidents.read", group: "operations" },
  { key: "vulnerabilities", icon: "vulnerabilities", classic: "/security/vulnerabilities", permission: "security.read", group: "security" },
  { key: "access", icon: "access", classic: "/security/access", permission: "security.read", group: "security" },
  { key: "exposure", icon: "exposure", classic: "/security/exposure", permission: "security.read", group: "security" },
  { key: "lifecycle", icon: "lifecycle", classic: "/security/lifecycle", permission: "security.read", group: "security" },
  { key: "compliance", icon: "compliance", classic: "/compliance", permission: "compliance.read", group: "governance" },
  { key: "actionCenter", icon: "action", classic: "/action-center", permission: "issues.read", group: "governance" },
  { key: "events", icon: "events", classic: "/audit/events", permission: "audit.read", group: "governance" },
  { key: "reports", icon: "reports", classic: "/audit/reports", permission: "reports.read", group: "governance" },
  { key: "integrations", icon: "integrations", classic: "/integrations", group: "admin" },
  { key: "admin", icon: "admin", classic: "/admin/users", permission: "users.manage", group: "admin" },
];
