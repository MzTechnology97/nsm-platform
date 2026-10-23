"""Local mirror of the ISPBilling customer registry (ISPB-01): what the worker read, and which NSM customer/site each row became.

Only what NSM needs to build its own customers and sites is kept: names, internal code, kind, status, group and
addresses. Fiscal data, contacts, payment data and the ``password`` field of the API are never stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utcnow


class IspbillingCustomer(Base):
    __tablename__ = "ispbilling_customers"
    __table_args__ = (Index("ix_ispbilling_customers_code", "internal_code"), Index("ix_ispbilling_customers_nsm", "nsm_customer_id"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    internal_code: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str | None] = mapped_column(String(40))
    group_name: Mapped[str | None] = mapped_column(String(120))
    # Registered (main) address, used as a site only when the customer has no service address.
    address: Mapped[str | None] = mapped_column(String(300))
    # Named nsm_* (not customer_id) on purpose: the mirror is administration data, outside the customer scope filter.
    nsm_customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set once the customer was imported, linked or left for review: the automatic import looks only at rows without it.
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IspbillingAddress(Base):
    """An installation address of a customer, read from the detail of its service instances."""

    __tablename__ = "ispbilling_addresses"
    __table_args__ = (Index("ix_ispbilling_addresses_customer", "customer_external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    customer_external_id: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    nsm_site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    # Set once a site was created or linked for it: a site deleted in NSM afterwards is not created again.
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IspbillingServiceInstance(Base):
    """A service of a customer: only what is needed to know which address to read (its detail holds the address)."""

    __tablename__ = "ispbilling_service_instances"
    __table_args__ = (Index("ix_ispbilling_instances_pending", "detail_read_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    customer_external_id: Mapped[str] = mapped_column(String(40), nullable=False)
    address_external_id: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str | None] = mapped_column(String(40))
    remote_updated_at: Mapped[str | None] = mapped_column(String(40))
    # None until the detail (with the address) has been read for this version of the instance.
    detail_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IspbillingRadiusAccount(Base):
    """A PPPoE account of ISPBilling Radius 2.0, and the NSM device it was matched to (PPPoE user, else static IP)."""

    __tablename__ = "ispbilling_radius_accounts"
    __table_args__ = (Index("ix_ispbilling_radius_username", "username"), Index("ix_ispbilling_radius_device", "nsm_device_id"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    customer_external_id: Mapped[str | None] = mapped_column(String(40))
    address_external_id: Mapped[str | None] = mapped_column(String(40))
    username: Mapped[str] = mapped_column(String(200), nullable=False)
    static_ip: Mapped[str | None] = mapped_column(String(64))
    profile_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(40))
    cpe_type: Mapped[str | None] = mapped_column(String(40))
    nsm_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    # "pppoe" (the device's PPPoE client uses this username) or "ip" (its management IP is the account's static IP).
    matched_by: Mapped[str | None] = mapped_column(String(10))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
