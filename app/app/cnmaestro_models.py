import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CambiumMetricSample(Base):
    """Operational metrics of a Cambium device read from cnMaestro (VEND-02), 90 days."""

    __tablename__ = "cambium_metric_samples"
    __table_args__ = (Index("ix_cambium_metric_samples_device_observed", "device_id", "observed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signal_dbm: Mapped[float | None] = mapped_column(Float)
    snr_db: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    ram_percent: Mapped[float | None] = mapped_column(Float)
    stations: Mapped[int | None] = mapped_column(Integer)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    dl_throughput_bps: Mapped[int | None] = mapped_column(BigInteger)
    ul_throughput_bps: Mapped[int | None] = mapped_column(BigInteger)
