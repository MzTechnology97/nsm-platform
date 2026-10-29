/** Navigation of the interface: one entry per section, grouped in the sidebar. */
export interface Section {
  key: string;
  icon: string;
  route: string;
  permission?: string;
  group: "operations" | "security" | "governance" | "admin";
}

export const SECTIONS: Section[] = [
  { key: "dashboard", icon: "dashboard", route: "/", group: "operations" },
  { key: "customers", icon: "customers", route: "/customers", permission: "customers.read", group: "operations" },
  { key: "devices", icon: "devices", route: "/devices", permission: "devices.read", group: "operations" },
  { key: "monitoring", icon: "monitoring", route: "/monitoring", permission: "monitoring.read", group: "operations" },
  { key: "agents", icon: "agents", route: "/agents", permission: "devices.read", group: "operations" },
  { key: "backups", icon: "backup", route: "/backups", permission: "backup.read", group: "operations" },
  { key: "firmware", icon: "firmware", route: "/firmware", permission: "firmware.read", group: "operations" },
  { key: "incidents", icon: "incidents", route: "/incidents", permission: "incidents.read", group: "operations" },
  { key: "vulnerabilities", icon: "vulnerabilities", route: "/vulnerabilities", permission: "security.read", group: "security" },
  { key: "access", icon: "access", route: "/access", permission: "security.read", group: "security" },
  { key: "exposure", icon: "exposure", route: "/exposure", permission: "security.read", group: "security" },
  { key: "ipAbuse", icon: "abuse", route: "/ip-abuse", permission: "security.read", group: "security" },
  { key: "lifecycle", icon: "lifecycle", route: "/lifecycle", permission: "security.read", group: "security" },
  { key: "compliance", icon: "compliance", route: "/compliance", permission: "compliance.read", group: "governance" },
  { key: "actionCenter", icon: "action", route: "/action-center", permission: "issues.read", group: "governance" },
  { key: "events", icon: "events", route: "/events", permission: "audit.read", group: "governance" },
  { key: "reports", icon: "reports", route: "/reports", permission: "reports.read", group: "governance" },
  { key: "sla", icon: "monitoring", route: "/sla", permission: "reports.read", group: "governance" },
  { key: "integrations", icon: "integrations", route: "/integrations", group: "admin" },
  { key: "admin", icon: "admin", route: "/admin/users", permission: "users.manage", group: "admin" },
];
