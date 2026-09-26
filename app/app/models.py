import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean,DateTime,ForeignKey,Index,JSON,String,Text,UniqueConstraint,Uuid
from sqlalchemy.orm import Mapped,mapped_column,relationship
from app.db import Base

def utcnow(): return datetime.now(timezone.utc)
class TimestampMixin:
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,onupdate=utcnow,nullable=False)
class User(Base,TimestampMixin):
    __tablename__='users'; id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4); username:Mapped[str]=mapped_column(String(100),unique=True); password_hash:Mapped[str]=mapped_column(String(512)); role:Mapped[str]=mapped_column(String(30),default='admin'); is_active:Mapped[bool]=mapped_column(Boolean,default=True)
class Customer(Base,TimestampMixin):
    __tablename__='customers'; id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4); name:Mapped[str]=mapped_column(String(200)); code:Mapped[str|None]=mapped_column(String(80),unique=True); notes:Mapped[str|None]=mapped_column(Text); is_active:Mapped[bool]=mapped_column(Boolean,default=True)
    sites:Mapped[list['Site']]=relationship(back_populates='customer',cascade='all, delete-orphan'); devices:Mapped[list['Device']]=relationship(back_populates='customer',cascade='all, delete-orphan')
class Site(Base,TimestampMixin):
    __tablename__='sites'; __table_args__=(Index('ix_sites_customer_id','customer_id'),); id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4); customer_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('customers.id',ondelete='CASCADE')); name:Mapped[str]=mapped_column(String(200)); address:Mapped[str|None]=mapped_column(String(300)); notes:Mapped[str|None]=mapped_column(Text); customer:Mapped['Customer']=relationship(back_populates='sites'); devices:Mapped[list['Device']]=relationship(back_populates='site')
class Device(Base,TimestampMixin):
    __tablename__='devices'; __table_args__=(UniqueConstraint('vendor','primary_mac',name='uq_devices_vendor_primary_mac'),Index('ix_devices_customer_id','customer_id'),Index('ix_devices_site_id','site_id'),Index('ix_devices_vendor','vendor'),Index('ix_devices_status','status'))
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4); customer_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('customers.id',ondelete='CASCADE')); site_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('sites.id',ondelete='SET NULL')); vendor:Mapped[str]=mapped_column(String(60)); family:Mapped[str|None]=mapped_column(String(100)); device_type:Mapped[str]=mapped_column(String(60)); name:Mapped[str]=mapped_column(String(200)); model:Mapped[str|None]=mapped_column(String(150)); serial_number:Mapped[str|None]=mapped_column(String(150)); primary_mac:Mapped[str|None]=mapped_column(String(17)); management_ip:Mapped[str|None]=mapped_column(String(255)); firmware_version:Mapped[str|None]=mapped_column(String(150)); management_source:Mapped[str]=mapped_column(String(60),default='manual'); external_device_id:Mapped[str|None]=mapped_column(String(255)); status:Mapped[str]=mapped_column(String(30),default='unknown'); last_seen:Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); customer:Mapped['Customer']=relationship(back_populates='devices'); site:Mapped['Site|None']=relationship(back_populates='devices')
class AuditEvent(Base):
    __tablename__='audit_events'; __table_args__=(Index('ix_audit_events_timestamp','timestamp'),Index('ix_audit_events_customer_id','customer_id'),Index('ix_audit_events_device_id','device_id'))
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4); timestamp:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); event_type:Mapped[str]=mapped_column(String(80)); severity:Mapped[str]=mapped_column(String(20),default='info'); customer_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('customers.id',ondelete='SET NULL')); device_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('devices.id',ondelete='SET NULL')); actor_user_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('users.id',ondelete='SET NULL')); source:Mapped[str]=mapped_column(String(50),default='portal'); result:Mapped[str]=mapped_column(String(30),default='success'); details:Mapped[dict]=mapped_column(JSON,default=dict)
