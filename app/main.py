from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, joinedload
from starlette.templating import Jinja2Templates

from .db import SessionLocal, engine, Base
from .models import Client, Property, ServiceStop, FileAttachment

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


# DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -----------------------
# DASHBOARD
# -----------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    properties = db.query(Property).order_by(Property.id.desc()).all()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "properties": properties,
        },
    )


# -----------------------
# PROPERTY DETAIL
# -----------------------
@app.get("/properties/{property_id}", response_class=HTMLResponse)
def property_detail(request: Request, property_id: int, db: Session = Depends(get_db)):

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
        request,
        "property_detail.html",
        {
            "property": prop,
        },
    )


# -----------------------
# NEW SERVICE STOP
# -----------------------
@app.get("/properties/{property_id}/service-stop/new", response_class=HTMLResponse)
def new_service_stop(request: Request, property_id: int, db: Session = Depends(get_db)):

    prop = (
        db.query(Property)
        .options(joinedload(Property.client))
        .filter(Property.id == property_id)
        .first()
    )

    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    return templates.TemplateResponse(
        request,
        "service_stop_new.html",
        {
            "property": prop,
        },
    )


# -----------------------
# SERVICE STOP DETAIL
# -----------------------
@app.get("/service-stops/{service_stop_id}", response_class=HTMLResponse)
def service_stop_detail(request: Request, service_stop_id: int, db: Session = Depends(get_db)):

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
        request,
        "service_stop_detail.html",
        {
            "service_stop": stop,
            "invoice_total": invoice_total,
        },
    )


# -----------------------
# SEED DATA
# -----------------------
@app.get("/dev/seed")
def seed(db: Session = Depends(get_db)):

    client = Client(name="Test Client", phone="123-456-7890")
    db.add(client)
    db.commit()
    db.refresh(client)

    prop = Property(address="123 Test St", client_id=client.id)
    db.add(prop)
    db.commit()
    db.refresh(prop)

    stop = ServiceStop(
        date="2026-04-14",
        problem_reported="Pump not working",
        work_performed="Replaced capacitor",
        property_id=prop.id,
    )

    db.add(stop)
    db.commit()

    return {"status": "seeded"}# -----------------------
# TEMP TEST ROUTE (PROOF FIX)
# -----------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return "<h1>APP IS WORKING</h1>"