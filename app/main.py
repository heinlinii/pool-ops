import os
import csv
import io
import shutil
import uuid

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload

from .db import SessionLocal, engine, Base
from .models import Client, Property, ServiceStop, FileAttachment


app = FastAPI()

Base.metadata.create_all(bind=engine)

UPLOAD_DIR = "app/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

templates = Jinja2Templates(directory="app/templates")


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def save_upload(file: UploadFile):
    ext = os.path.splitext(file.filename)[1]
    new_name = f"{uuid.uuid4()}{ext}"
    path = os.path.join(UPLOAD_DIR, new_name)

    with open(path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return file.filename, new_name


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    db = SessionLocal()
    clients = db.query(Client).order_by(Client.id.desc()).all()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "clients": clients,
        },
    )


@app.get("/properties/{property_id}", response_class=HTMLResponse)
def property_detail(request: Request, property_id: int):
    db = SessionLocal()

    prop = (
        db.query(Property)
        .options(
            joinedload(Property.client),
            joinedload(Property.service_stops).joinedload(ServiceStop.files),
        )
        .filter(Property.id == property_id)
        .first()
    )

    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    return templates.TemplateResponse(
        "property_detail.html",
        {
            "request": request,
            "property": prop,
        },
    )


@app.get("/properties/{property_id}/service-stop/new", response_class=HTMLResponse)
def new_service_stop(request: Request, property_id: int):
    db = SessionLocal()

    prop = (
        db.query(Property)
        .options(joinedload(Property.client))
        .filter(Property.id == property_id)
        .first()
    )

    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    return templates.TemplateResponse(
        "service_stop_new.html",
        {
            "request": request,
            "property": prop,
        },
    )


@app.post("/properties/{property_id}/service-stop/new")
def create_service_stop(
    property_id: int,
    date_value: str = Form(""),
    tech_name: str = Form(""),
    problem_reported: str = Form(""),
    work_performed: str = Form(""),
    recommendation: str = Form(""),
    billed_amount: str = Form("0"),
    labor_hours: str = Form("0"),
    material_cost: str = Form("0"),
    trip_charge: str = Form("0"),
    tax: str = Form("0"),
    paid_status: str = Form("unpaid"),
    invoice_notes: str = Form(""),
    before_photos: list[UploadFile] = File([]),
    after_photos: list[UploadFile] = File([]),
    general_files: list[UploadFile] = File([]),
):
    db = SessionLocal()

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    stop = ServiceStop(
        date=date_value.strip(),
        tech_name=tech_name.strip(),
        problem_reported=problem_reported.strip(),
        work_performed=work_performed.strip(),
        recommendation=recommendation.strip(),
        billed_amount=to_float(billed_amount),
        labor_hours=to_float(labor_hours),
        material_cost=to_float(material_cost),
        trip_charge=to_float(trip_charge),
        tax=to_float(tax),
        paid_status=paid_status.strip(),
        invoice_notes=invoice_notes.strip(),
        status="completed",
        property_id=property_id,
    )

    db.add(stop)
    db.commit()
    db.refresh(stop)

    def attach_files(files, category):
        for upload in files:
            if not upload or not upload.filename:
                continue

            original_name, stored_name = save_upload(upload)

            file_record = FileAttachment(
                original_name=original_name,
                stored_name=stored_name,
                file_type=upload.content_type or "",
                notes="",
                category=category,
                service_stop_id=stop.id,
            )
            db.add(file_record)

    attach_files(before_photos, "before")
    attach_files(after_photos, "after")
    attach_files(general_files, "general")

    db.commit()

    return RedirectResponse(url=f"/service-stops/{stop.id}", status_code=303)


@app.get("/service-stops/{service_stop_id}", response_class=HTMLResponse)
def service_stop_detail(request: Request, service_stop_id: int):
    db = SessionLocal()

    stop = (
        db.query(ServiceStop)
        .options(
            joinedload(ServiceStop.property).joinedload(Property.client),
            joinedload(ServiceStop.files),
        )
        .filter(ServiceStop.id == service_stop_id)
        .first()
    )

    if not stop:
        raise HTTPException(status_code=404, detail="Service stop not found")

    invoice_total = (
        float(stop.billed_amount or 0)
        + float(stop.material_cost or 0)
        + float(stop.trip_charge or 0)
        + float(stop.tax or 0)
    )

    return templates.TemplateResponse(
        "service_stop_detail.html",
        {
            "request": request,
            "service_stop": stop,
            "invoice_total": invoice_total,
        },
    )


@app.get("/export/service-stops")
def export_service_stops():
    db = SessionLocal()

    stops = (
        db.query(ServiceStop)
        .options(
            joinedload(ServiceStop.property).joinedload(Property.client),
            joinedload(ServiceStop.files),
        )
        .order_by(ServiceStop.id.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Service Stop ID",
        "Date",
        "Tech Name",
        "Client Name",
        "Client Email",
        "Property Address",
        "Property ID",
        "Problem Reported",
        "Work Performed",
        "Recommendation",
        "Service Charge",
        "Labor Hours",
        "Material Cost",
        "Trip Charge",
        "Tax",
        "Invoice Total",
        "Paid Status",
        "Invoice Notes",
        "Attached File Count",
    ])

    for s in stops:
        client_name = ""
        client_email = ""
        property_address = ""

        if s.property:
            property_address = s.property.address or ""
            if s.property.client:
                client_name = s.property.client.name or ""
                client_email = s.property.client.email or ""

        invoice_total = (
            float(s.billed_amount or 0)
            + float(s.material_cost or 0)
            + float(s.trip_charge or 0)
            + float(s.tax or 0)
        )

        writer.writerow([
            s.id,
            s.date or "",
            s.tech_name or "",
            client_name,
            client_email,
            property_address,
            s.property_id or "",
            s.problem_reported or "",
            s.work_performed or "",
            s.recommendation or "",
            s.billed_amount or 0,
            s.labor_hours or 0,
            s.material_cost or 0,
            s.trip_charge or 0,
            s.tax or 0,
            invoice_total,
            s.paid_status or "",
            s.invoice_notes or "",
            len(s.files) if s.files else 0,
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=service_stops_detailed.csv"},
    )


@app.get("/dev/seed")
def seed():
    db = SessionLocal()

    existing_client = db.query(Client).first()
    if existing_client:
        return {"status": "already seeded"}

    client = Client(
        name="John Smith",
        phone="812-555-1212",
        email="john@email.com",
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    prop = Property(
        address="1234 Oak Hill Rd",
        pool_type="Gunite",
        notes="Auto cover issue on right track.",
        client_id=client.id,
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)

    stop = ServiceStop(
        date="2026-04-13",
        tech_name="Mike",
        problem_reported="Customer reported cover dragging on right side.",
        work_performed="Inspected tracks, adjusted alignment, cleaned debris, tested operation.",
        recommendation="Return in one week and recheck alignment under use.",
        billed_amount=275.00,
        labor_hours=2.5,
        material_cost=18.00,
        trip_charge=35.00,
        tax=19.04,
        paid_status="unpaid",
        invoice_notes="Customer requested emailed invoice.",
        status="completed",
        property_id=prop.id,
    )
    db.add(stop)
    db.commit()

    return {"status": "seeded"}