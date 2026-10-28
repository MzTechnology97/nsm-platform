import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AcsMetricSample(Base):
    """Operational metrics of a TR-069 CPE read from GenieACS (ACS-05), one per Inform, 90 days."""

    __tablename__ = "acs_metric_samples"
    __table_args__ = (Index("ix_acs_metric_samples_device_observed", "device_id", "observed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    ram_percent: Mapped[float | None] = mapped_column(Float)
    wan_rx_bps: Mapped[int | None] = mapped_column(BigInteger)
    wan_tx_bps: Mapped[int | None] = mapped_column(BigInteger)
    wifi_clients: Mapped[int | None] = mapped_column(Integer)
    lan_hosts: Mapped[int | None] = mapped_column(Integer)
    dsl_down_bps: Mapped[int | None] = mapped_column(BigInteger)
    dsl_up_bps: Mapped[int | None] = mapped_column(BigInteger)
    dsl_snr_down_db: Mapped[float | None] = mapped_column(Float)
    dsl_attenuation_down_db: Mapped[float | None] = mapped_column(Float)
    # PON optical levels (ACS-05, Core 0.49.263): last values read by the hourly provision.
    pon_rx_dbm: Mapped[float | None] = mapped_column(Float)
    pon_tx_dbm: Mapped[float | None] = mapped_column(Float)
    pon_temperature_c: Mapped[float | None] = mapped_column(Float)
    pon_voltage_v: Mapped[float | None] = mapped_column(Float)
    pon_bias_ma: Mapped[float | None] = mapped_column(Float)


class AcsFirmwareCampaign(Base):
    """Firmware campaign over many TR-069 CPEs (ACS-08): one pilot CPE, then waves, inside a time window."""

    __tablename__ = "acs_firmware_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, running, paused, stopped, completed, cancelled
    product_class: Mapped[str | None] = mapped_column(String(120))
    file_name: Mapped[str] = mapped_column(String(255))
    expected_version: Mapped[str | None] = mapped_column(String(120))
    wave_size: Mapped[int] = mapped_column(Integer, default=10)
    window_start: Mapped[str] = mapped_column(String(5), default="01:00")
    window_end: Mapped[str] = mapped_column(String(5), default="05:00")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    started_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)


class AcsFirmwareCampaignDevice(Base):
    """One CPE of a firmware campaign: wave 0 is the pilot."""

    __tablename__ = "acs_firmware_campaign_devices"
    __table_args__ = (UniqueConstraint("campaign_id", "device_id", name="uq_acs_campaign_device"),
                      Index("ix_acs_campaign_devices_campaign_wave", "campaign_id", "wave"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("acs_firmware_campaigns.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    wave: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # pending, requested, verified, failed, unconfirmed, skipped, cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending")
    from_version: Mapped[str | None] = mapped_column(String(120))
    to_version: Mapped[str | None] = mapped_column(String(120))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # A failed or unconfirmed result that already stopped the campaign (a resume goes on with the others).
    reported: Mapped[bool] = mapped_column(Boolean, default=False)
