from fastapi import FastAPI, Request, Depends, HTTPException, Form
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
@app.get("/properties/new", response_class=HTMLResponse)
def new_property(request: Request, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()

    return templates.TemplateResponse(
        request,
        "property_new.html",
        {
            "clients": clients,
        },
    )
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
# SEED @app.get("/properties/new", response_class=HTMLResponse)
def new_property(request: Request, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name.asc()).all()

    return templates.TemplateResponse(
        request,
        "property_new.html",
        {
            "clients": clients,
        },
    )


@app.post("/properties/new")
def create_property(
    client_id: str = Form(""),
    client_name: str = Form(""),
    client_phone: str = Form(""),
    client_email: str = Form(""),
    address: str = Form(""),
    pool_type: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    if not address.strip():
        raise HTTPException(status_code=400, detail="Property address is required")

    selected_client = None

    if client_id.strip():
        selected_client = db.query(Client).filter(Client.id == int(client_id)).first()

    if not selected_client:
        if not client_name.strip():
            raise HTTPException(status_code=400, detail="Client name is required if no existing client is selected")

        selected_client = Client(
            name=client_name.strip(),
            phone=client_phone.strip(),
            email=client_email.strip(),
        )
        db.add(selected_client)
        db.commit()
        db.refresh(selected_client)

    prop = Property(
        address=address.strip(),
        pool_type=pool_type.strip(),
        notes=notes.strip(),
        client_id=selected_client.id,
    )

    db.add(prop)
    db.commit()
    db.refresh(prop)

    return RedirectResponse(url=f"/properties/{prop.id}", status_code=303)DATA
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