import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, Uuid
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
