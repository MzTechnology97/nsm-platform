"""API scope registry for programmatic read-only access."""

API_SCOPE_INVENTORY_READ = "inventory.read"
API_SCOPE_BACKUP_READ = "backup.read"
API_SCOPE_SECURITY_READ = "security.read"
API_SCOPE_FIRMWARE_READ = "firmware.read"

OPTIONAL_READ_SCOPES = (
    API_SCOPE_BACKUP_READ,
    API_SCOPE_SECURITY_READ,
    API_SCOPE_FIRMWARE_READ,
)

SCOPE_LABELS = {
    API_SCOPE_INVENTORY_READ: "Inventario",
    API_SCOPE_BACKUP_READ: "Stato backup",
    API_SCOPE_SECURITY_READ: "Security / CVE",
    API_SCOPE_FIRMWARE_READ: "Firmware",
}

SCOPE_DESCRIPTIONS = {
    API_SCOPE_INVENTORY_READ: "Clienti e inventario apparati in sola lettura.",
    API_SCOPE_BACKUP_READ: "Stato ed esecuzioni backup; nessun download o modifica.",
    API_SCOPE_SECURITY_READ: "Vulnerabilità/CVE associate agli apparati in sola lettura.",
    API_SCOPE_FIRMWARE_READ: "Stato firmware e versione raccomandata in sola lettura.",
}


def normalize_scopes(values) -> list[str]:
    """Return a deterministic allow-listed scope set; inventory.read is always present."""
    selected = {str(value).strip() for value in (values or [])}
    result = [API_SCOPE_INVENTORY_READ]
    result.extend(scope for scope in OPTIONAL_READ_SCOPES if scope in selected)
    return result
