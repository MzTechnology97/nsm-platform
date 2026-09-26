import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(80), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    sites: Mapped[list["Site"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    devices: Mapped[list["Device"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class Site(Base, TimestampMixin):
    __tablename__ = "sites"
    __table_args__ = (Index("ix_sites_customer_id", "customer_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)

    customer: Mapped["Customer"] = relationship(back_populates="sites")
    devices: Mapped[list["Device"]] = relationship(back_populates="site")


class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint(
            "vendor", "primary_mac", name="uq_devices_vendor_primary_mac"
        ),
        Index("ix_devices_customer_id", "customer_id"),
        Index("ix_devices_site_id", "site_id"),
        Index("ix_devices_vendor", "vendor"),
        Index("ix_devices_status", "status"),
        Index("ix_devices_serial_number", "serial_number"),
        Index("ix_devices_device_identity", "device_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL")
    )

    vendor: Mapped[str] = mapped_column(String(60))
    family: Mapped[str | None] = mapped_column(String(100))
    device_type: Mapped[str] = mapped_column(String(60))

    # `name` is retained for backwards compatibility with 0.1.
    # New UI uses display_name (operator alias) and device_identity (observed).
    name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    device_identity: Mapped[str | None] = mapped_column(String(200))

    model: Mapped[str | None] = mapped_column(String(150))
    serial_number: Mapped[str | None] = mapped_column(String(150))
    primary_mac: Mapped[str | None] = mapped_column(String(17))
    management_ip: Mapped[str | None] = mapped_column(String(255))
    firmware_version: Mapped[str | None] = mapped_column(String(150))

    management_source: Mapped[str] = mapped_column(String(60), default="manual")
    external_device_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="unknown")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    inventory_source: Mapped[str | None] = mapped_column(String(60))
    inventory_last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    architecture: Mapped[str | None] = mapped_column(String(100))
    software_id: Mapped[str | None] = mapped_column(String(100))
    routerboot_version: Mapped[str | None] = mapped_column(String(100))
    inventory_data: Mapped[dict] = mapped_column(JSON, default=dict)

    firmware_status: Mapped[str] = mapped_column(String(30), default="unknown")
    recommended_firmware_version: Mapped[str | None] = mapped_column(String(150))

    lifecycle_status: Mapped[str] = mapped_column(String(30), default="unknown")
    eol_date: Mapped[date | None] = mapped_column(Date)
    eos_date: Mapped[date | None] = mapped_column(Date)
    lifecycle_source: Mapped[str | None] = mapped_column(String(255))
    lifecycle_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    customer: Mapped["Customer"] = relationship(back_populates="devices")
    site: Mapped["Site | None"] = relationship(back_populates="devices")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_timestamp", "timestamp"),
        Index("ix_audit_events_customer_id", "customer_id"),
        Index("ix_audit_events_device_id", "device_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    event_type: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="info")
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(50), default="portal")
    result: Mapped[str] = mapped_column(String(30), default="success")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class DeviceEnrollment(Base):
    __tablename__ = "device_enrollments"
    __table_args__ = (
        Index("ix_device_enrollments_device_id", "device_id"),
        Index("ix_device_enrollments_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(60))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_created_at", "created_at"),
        Index("ix_notifications_severity", "severity"),
        Index("ix_notifications_category", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    severity: Mapped[str] = mapped_column(String(20), default="info")
    category: Mapped[str] = mapped_column(String(40), default="system")
    title: Mapped[str] = mapped_column(String(240))
    message: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    advisory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("security_advisories.id", ondelete="SET NULL")
    )
    source_url: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "user_id", name="uq_notification_reads_notification_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ActionIssue(Base):
    __tablename__ = "action_issues"
    __table_args__ = (
        Index("ix_action_issues_status", "status"),
        Index("ix_action_issues_severity", "severity"),
        Index("ix_action_issues_device_id", "device_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    category: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    title: Mapped[str] = mapped_column(String(240))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    advisory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("security_advisories.id", ondelete="SET NULL")
    )
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityAdvisory(Base):
    __tablename__ = "security_advisories"
    __table_args__ = (
        Index("ix_security_advisories_published_at", "published_at"),
        Index("ix_security_advisories_severity", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cve_id: Mapped[str] = mapped_column(String(40), unique=True)
    vendor: Mapped[str | None] = mapped_column(String(100))
    product: Mapped[str | None] = mapped_column(String(180))
    severity: Mapped[str] = mapped_column(String(20), default="unknown")
    cvss: Mapped[float | None] = mapped_column(Float)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    affected_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    fixed_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    vendor_advisory_url: Mapped[str | None] = mapped_column(String(1000))
    nvd_url: Mapped[str | None] = mapped_column(String(1000))
    cisa_url: Mapped[str | None] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(80), default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class DeviceVulnerability(Base):
    __tablename__ = "device_vulnerabilities"
    __table_args__ = (
        UniqueConstraint(
            "advisory_id", "device_id", name="uq_device_vulnerabilities_advisory_device"
        ),
        Index("ix_device_vulnerabilities_status", "status"),
        Index("ix_device_vulnerabilities_device_id", "device_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    advisory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("security_advisories.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(30), default="open")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    installed_version: Mapped[str | None] = mapped_column(String(150))
    fixed_version: Mapped[str | None] = mapped_column(String(150))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class BackupPolicy(Base, TimestampMixin):
    __tablename__ = "backup_policies"
    __table_args__ = (Index("ix_backup_policies_scope_type", "scope_type"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scope_type: Mapped[str] = mapped_column(String(20), default="global")
    vendor: Mapped[str | None] = mapped_column(String(60))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    schedule_cron: Mapped[str] = mapped_column(String(100), default="0 3 * * *")
    binary_backup: Mapped[bool] = mapped_column(Boolean, default=True)
    text_export: Mapped[bool] = mapped_column(Boolean, default=True)
    pre_firmware_backup: Mapped[bool] = mapped_column(Boolean, default=True)
    verify_hash: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_daily: Mapped[int] = mapped_column(Integer, default=30)
    retention_weekly: Mapped[int] = mapped_column(Integer, default=12)
    retention_monthly: Mapped[int] = mapped_column(Integer, default=12)
    retry_count: Mapped[int] = mapped_column(Integer, default=3)


class BackupRun(Base):
    __tablename__ = "backup_runs"
    __table_args__ = (
        Index("ix_backup_runs_device_id", "device_id"),
        Index("ix_backup_runs_started_at", "started_at"),
        Index("ix_backup_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backup_policies.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    backup_type: Mapped[str | None] = mapped_column(String(40))
    file_path: Mapped[str | None] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)
