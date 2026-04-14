from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from .db import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    email = Column(String, default="")

    properties = relationship("Property", back_populates="client", cascade="all, delete")


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, nullable=False)
    pool_type = Column(String, default="")
    notes = Column(Text, default="")
    client_id = Column(Integer, ForeignKey("clients.id"))

    client = relationship("Client", back_populates="properties")
    service_stops = relationship("ServiceStop", back_populates="property", cascade="all, delete")


class ServiceStop(Base):
    __tablename__ = "service_stops"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, default="")
    tech_name = Column(String, default="")
    problem_reported = Column(Text, default="")
    work_performed = Column(Text, default="")
    recommendation = Column(Text, default="")
    billed_amount = Column(Float, default=0)
    labor_hours = Column(Float, default=0)
    material_cost = Column(Float, default=0)
    trip_charge = Column(Float, default=0)
    tax = Column(Float, default=0)
    paid_status = Column(String, default="unpaid")
    invoice_notes = Column(Text, default="")
    status = Column(String, default="completed")
    property_id = Column(Integer, ForeignKey("properties.id"))

    property = relationship("Property", back_populates="service_stops")
    files = relationship("FileAttachment", back_populates="service_stop", cascade="all, delete")


class FileAttachment(Base):
    __tablename__ = "file_attachments"

    id = Column(Integer, primary_key=True, index=True)
    original_name = Column(String, default="")
    stored_name = Column(String, default="")
    file_type = Column(String, default="")
    notes = Column(Text, default="")
    category = Column(String, default="")
    service_stop_id = Column(Integer, ForeignKey("service_stops.id"))

    service_stop = relationship("ServiceStop", back_populates="files")