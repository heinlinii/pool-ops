from sqlalchemy import Column, Integer, String, ForeignKey, Text, Boolean, Float
from sqlalchemy.orm import relationship
from .db import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    email = Column(String, default="")

    properties = relationship(
        "Property",
        back_populates="client",
        cascade="all, delete-orphan"
    )
    tasks = relationship(
        "Task",
        back_populates="client",
        cascade="all, delete-orphan"
    )


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True)
    address = Column(String, nullable=False)
    pool_type = Column(String, default="")
    notes = Column(Text, default="")

    pump = Column(String, default="")
    filter = Column(String, default="")
    heater = Column(String, default="")
    sanitizer = Column(String, default="")
    automation = Column(String, default="")
    cleaner = Column(String, default="")
    cover_type = Column(String, default="")
    cover_notes = Column(Text, default="")
    install_year = Column(String, default="")
    equipment_notes = Column(Text, default="")

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)

    client = relationship("Client", back_populates="properties")
    jobs = relationship(
        "Job",
        back_populates="property",
        cascade="all, delete-orphan"
    )
    files = relationship(
        "FileAttachment",
        back_populates="property",
        cascade="all, delete-orphan"
    )
    service_stops = relationship(
        "ServiceStop",
        back_populates="property",
        cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    description = Column(Text, default="")
    date = Column(String, default="")

    quoted_price = Column(Float, default=0.0)
    labor_cost = Column(Float, default=0.0)
    material_cost = Column(Float, default=0.0)
    subcontractor_cost = Column(Float, default=0.0)
    other_cost = Column(Float, default=0.0)

    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)

    property = relationship("Property", back_populates="jobs")
    files = relationship(
        "FileAttachment",
        back_populates="job",
        cascade="all, delete-orphan"
    )


class ServiceStop(Base):
    __tablename__ = "service_stops"

    id = Column(Integer, primary_key=True)
    date = Column(String, default="")
    tech_name = Column(String, default="")
    problem_reported = Column(Text, default="")
    work_performed = Column(Text, default="")
    recommendation = Column(Text, default="")
    billed_amount = Column(Float, default=0.0)
    labor_hours = Column(Float, default=0.0)
    material_cost = Column(Float, default=0.0)

    trip_charge = Column(Float, default=0.0)
    tax = Column(Float, default=0.0)
    paid_status = Column(String, default="unpaid")
    invoice_notes = Column(Text, default="")

    status = Column(String, default="completed")

    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)

    property = relationship("Property", back_populates="service_stops")
    files = relationship(
        "FileAttachment",
        back_populates="service_stop",
        cascade="all, delete-orphan"
    )


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    status = Column(String, default="open")
    job_type = Column(String, default="")
    due_date = Column(String, default="")
    notes = Column(Text, default="")
    assigned_to = Column(String, default="")
    reminder_type = Column(String, default="")
    is_recurring = Column(Boolean, default=False)

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)

    client = relationship("Client", back_populates="tasks")


class FileAttachment(Base):
    __tablename__ = "file_attachments"

    id = Column(Integer, primary_key=True)
    original_name = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)
    file_type = Column(String, default="")
    notes = Column(Text, default="")
    category = Column(String, default="general")

    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    service_stop_id = Column(Integer, ForeignKey("service_stops.id"), nullable=True)

    property = relationship("Property", back_populates="files")
    job = relationship("Job", back_populates="files")
    service_stop = relationship("ServiceStop", back_populates="files")from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from .db import Base


class FileAttachment(Base):
    __tablename__ = "file_attachments"

    id = Column(Integer, primary_key=True, index=True)

    original_name = Column(String)
    stored_name = Column(String)
    file_type = Column(String)
    notes = Column(Text)
    category = Column(String)

    service_stop_id = Column(Integer, ForeignKey("service_stops.id"))

    service_stop = relationship("ServiceStop", back_populates="files")