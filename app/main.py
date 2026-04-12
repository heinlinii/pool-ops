from datetime import date, timedelta, datetime
from collections import defaultdict
from pathlib import Path
import shutil
import uuid
from io import BytesIO, StringIO
import csv

from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import joinedload

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from .db import SessionLocal, engine, Base
from .models import Client, Property, Job, Task, FileAttachment, ServiceStop

app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory="app/uploads"), name="uploads")

templates = Jinja2Templates(directory="app/templates")

UPLOAD_DIR = Path("app/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

Base.metadata.create_all(bind=engine)


def sort_tasks(task_list):
    return sorted(
        task_list,
        key=lambda t: (
            0 if (t.status or "").lower() != "done" else 1,
            t.due_date or "",
            -(t.id or 0),
        ),
    )


def to_float(value) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def money(value) -> str:
    return f"${value:,.2f}"


def service_stop_total(stop: ServiceStop) -> float:
    return (
        to_float(stop.billed_amount)
        + to_float(stop.material_cost)
        + to_float(stop.trip_charge)
        + to_float(stop.tax)
    )


def job_total_cost(job: Job) -> float:
    return (
        to_float(job.labor_cost)
        + to_float(job.material_cost)
        + to_float(job.subcontractor_cost)
        + to_float(job.other_cost)
    )


def job_profit(job: Job) -> float:
    return to_float(job.quoted_price) - job_total_cost(job)


def job_margin(job: Job) -> float:
    quoted = to_float(job.quoted_price)
    if quoted <= 0:
        return 0.0
    return (job_profit(job) / quoted) * 100.0


def get_assigned_people(db):
    people = (
        db.query(Task.assigned_to)
        .filter(Task.assigned_to.isnot(None))
        .all()
    )
    names = sorted({(p[0] or "").strip() for p in people if (p[0] or "").strip()})
    return names


def next_due_date(current_due_date: str, reminder_type: str) -> str:
    if not current_due_date:
        base = date.today()
    else:
        base = datetime.strptime(current_due_date, "%Y-%m-%d").date()

    reminder_type = (reminder_type or "").strip().lower()

    if reminder_type == "weekly":
        return (base + timedelta(days=7)).isoformat()
    if reminder_type == "monthly":
        return (base + timedelta(days=30)).isoformat()
    if reminder_type == "opening follow-up":
        return (base + timedelta(days=14)).isoformat()
    if reminder_type == "closing follow-up":
        return (base + timedelta(days=30)).isoformat()

    return (base + timedelta(days=7)).isoformat()


def save_upload(upload: UploadFile) -> tuple[str, str]:
    original_name = upload.filename or "upload"
    ext = Path(original_name).suffix
    stored_name = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / stored_name

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)

    return original_name, stored_name


def draw_wrapped_text(pdf, text, x, y, max_width, line_height=14, font_name="Helvetica", font_size=10):
    if not text:
        return y

    pdf.setFont(font_name, font_size)
    words = str(text).split()
    line = ""

    for word in words:
        test_line = f"{line} {word}".strip()
        if pdf.stringWidth(test_line, font_name, font_size) <= max_width:
            line = test_line
        else:
            if line:
                pdf.drawString(x, y, line)
                y -= line_height
            line = word

    if line:
        pdf.drawString(x, y, line)
        y -= line_height

    return y


def ensure_page_space(pdf, y, needed=80):
    if y < needed:
        pdf.showPage()
        pdf.setFont("Helvetica", 10)
        return 10.5 * inch
    return y


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    db = SessionLocal()

    clients = db.query(Client).order_by(Client.name.asc()).all()
    tasks = (
        db.query(Task)
        .options(joinedload(Task.client))
        .order_by(Task.id.desc())
        .all()
    )

    overdue_count = 0
    today_str = date.today().isoformat()

    for task in tasks:
        if (task.status or "").lower() != "done" and task.due_date and task.due_date < today_str:
            overdue_count += 1

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "clients": clients,
            "tasks": tasks,
            "overdue_count": overdue_count,
        },
    )


@app.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request, assigned_to: str = ""):
    db = SessionLocal()

    query = (
        db.query(Task)
        .options(joinedload(Task.client))
        .order_by(Task.due_date.asc(), Task.id.desc())
    )

    selected_person = assigned_to.strip()

    if selected_person:
        query = query.filter(Task.assigned_to == selected_person)

    tasks = query.all()

    today = date.today()
    today_str = today.isoformat()
    tomorrow_str = (today + timedelta(days=1)).isoformat()

    week_start = today - timedelta(days=today.weekday())
    week_days = []
    weekly_tasks = {}

    for i in range(7):
        day = week_start + timedelta(days=i)
        day_str = day.isoformat()
        short_label = day.strftime("%a %m/%d")
        week_days.append(
            {
                "date": day,
                "date_str": day_str,
                "short_label": short_label,
                "is_today": day == today,
            }
        )
        weekly_tasks[day_str] = []

    overdue_tasks = []
    today_tasks = []
    tomorrow_tasks = []
    upcoming_tasks = defaultdict(list)
    undated_tasks = []

    for task in tasks:
        due = (task.due_date or "").strip()
        status = (task.status or "").lower()

        if due:
            if due in weekly_tasks:
                weekly_tasks[due].append(task)

            if status != "done" and due < today_str:
                overdue_tasks.append(task)
            elif due == today_str:
                today_tasks.append(task)
            elif due == tomorrow_str:
                tomorrow_tasks.append(task)
            elif due not in weekly_tasks:
                upcoming_tasks[due].append(task)
        else:
            undated_tasks.append(task)

    sorted_upcoming_dates = sorted(upcoming_tasks.keys())
    assigned_people = get_assigned_people(db)

    for day_str in weekly_tasks:
        weekly_tasks[day_str] = sort_tasks(weekly_tasks[day_str])

    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "request": request,
            "today_str": today_str,
            "tomorrow_str": tomorrow_str,
            "overdue_tasks": sort_tasks(overdue_tasks),
            "today_tasks": sort_tasks(today_tasks),
            "tomorrow_tasks": sort_tasks(tomorrow_tasks),
            "upcoming_tasks": upcoming_tasks,
            "sorted_upcoming_dates": sorted_upcoming_dates,
            "undated_tasks": sort_tasks(undated_tasks),
            "assigned_people": assigned_people,
            "selected_person": selected_person,
            "week_days": week_days,
            "weekly_tasks": weekly_tasks,
        },
    )


@app.get("/properties/{property_id}/pdf")
def property_pdf(property_id: int):
    db = SessionLocal()

    prop = (
        db.query(Property)
        .options(
            joinedload(Property.client),
            joinedload(Property.jobs).joinedload(Job.files),
            joinedload(Property.files),
            joinedload(Property.service_stops).joinedload(ServiceStop.files),
        )
        .filter(Property.id == property_id)
        .first()
    )

    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    left = 0.75 * inch
    right_width = letter[0] - (1.5 * inch)
    y = 10.5 * inch

    pdf.setTitle(f"Property Report - {prop.address}")

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(left, y, "Property Report")
    y -= 24

    pdf.setFont("Helvetica", 11)
    pdf.drawString(left, y, f"Client: {prop.client.name}")
    y -= 16
    pdf.drawString(left, y, f"Address: {prop.address}")
    y -= 16
    pdf.drawString(left, y, f"Pool Type: {prop.pool_type or 'Not set'}")
    y -= 24

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(left, y, "Service Stop History")
    y -= 16
    pdf.setFont("Helvetica", 10)

    if prop.service_stops:
        for stop in prop.service_stops:
            y = ensure_page_space(pdf, y)
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(left, y, f"{stop.date or 'No date'} - {stop.tech_name or 'No tech'}")
            y -= 12
            pdf.setFont("Helvetica", 10)
            y = draw_wrapped_text(pdf, f"Problem: {stop.problem_reported or 'None'}", left + 12, y, right_width - 12)
            y = draw_wrapped_text(pdf, f"Work: {stop.work_performed or 'None'}", left + 12, y, right_width - 12)
            y = draw_wrapped_text(pdf, f"Recommendation: {stop.recommendation or 'None'}", left + 12, y, right_width - 12)
            y = draw_wrapped_text(
                pdf,
                f"Invoice Total: {money(service_stop_total(stop))} | Paid Status: {stop.paid_status or 'unpaid'}",
                left + 12,
                y,
                right_width - 12
            )
            y -= 8
    else:
        pdf.drawString(left, y, "No service stops yet.")
        y -= 16

    pdf.showPage()
    pdf.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()

    filename = f"property_report_{property_id}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/service-stops/{service_stop_id}/pdf")
def service_stop_pdf(service_stop_id: int):
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

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    left = 0.75 * inch
    right_width = letter[0] - (1.5 * inch)
    y = 10.5 * inch

    pdf.setTitle(f"Service Stop Report - {service_stop_id}")

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(left, y, "Service Stop Report")
    y -= 24

    pdf.setFont("Helvetica", 11)
    pdf.drawString(left, y, f"Client: {stop.property.client.name}")
    y -= 16
    pdf.drawString(left, y, f"Property: {stop.property.address}")
    y -= 16
    pdf.drawString(left, y, f"Date: {stop.date or 'Not set'}")
    y -= 16
    pdf.drawString(left, y, f"Tech: {stop.tech_name or 'Not set'}")
    y -= 24

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(left, y, "Problem Reported")
    y -= 16
    y = draw_wrapped_text(pdf, stop.problem_reported or "None", left, y, right_width)
    y -= 10

    y = ensure_page_space(pdf, y)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(left, y, "Work Performed")
    y -= 16
    y = draw_wrapped_text(pdf, stop.work_performed or "None", left, y, right_width)
    y -= 10

    y = ensure_page_space(pdf, y)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(left, y, "Recommendation / Next Step")
    y -= 16
    y = draw_wrapped_text(pdf, stop.recommendation or "None", left, y, right_width)
    y -= 10

    y = ensure_page_space(pdf, y)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(left, y, "Billing Summary")
    y -= 16
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Service Charge: {money(stop.billed_amount or 0)}")
    y -= 14
    pdf.drawString(left, y, f"Material Cost: {money(stop.material_cost or 0)}")
    y -= 14
    pdf.drawString(left, y, f"Trip Charge: {money(stop.trip_charge or 0)}")
    y -= 14
    pdf.drawString(left, y, f"Tax: {money(stop.tax or 0)}")
    y -= 14
    pdf.drawString(left, y, f"Invoice Total: {money(service_stop_total(stop))}")
    y -= 14
    pdf.drawString(left, y, f"Paid Status: {stop.paid_status or 'unpaid'}")
    y -= 14
    pdf.drawString(left, y, f"Labor Hours: {stop.labor_hours or 0}")
    y -= 18

    if stop.invoice_notes:
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Invoice Notes")
        y -= 16
        pdf.setFont("Helvetica", 10)
        y = draw_wrapped_text(pdf, stop.invoice_notes, left, y, right_width)
        y -= 10

    y = ensure_page_space(pdf, y)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(left, y, "Attached Files")
    y -= 16
    pdf.setFont("Helvetica", 10)

    if stop.files:
        for file in stop.files:
            y = ensure_page_space(pdf, y)
            pdf.drawString(left, y, f"{file.category.title()}: {file.original_name}")
            y -= 12
            if file.notes:
                y = draw_wrapped_text(pdf, f"Notes: {file.notes}", left + 12, y, right_width - 12)
            y -= 4
    else:
        pdf.drawString(left, y, "No attached files.")
        y -= 16

    pdf.showPage()
    pdf.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()

    filename = f"service_stop_{service_stop_id}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/service-stops/{service_stop_id}/quickbooks-invoice.csv")
def service_stop_quickbooks_csv(service_stop_id: int):
    db = SessionLocal()

    stop = (
        db.query(ServiceStop)
        .options(
            joinedload(ServiceStop.property).joinedload(Property.client),
        )
        .filter(ServiceStop.id == service_stop_id)
        .first()
    )

    if not stop:
        raise HTTPException(status_code=404, detail="Service stop not found")

    invoice_number = f"SS-{stop.id}"
    invoice_date = stop.date or date.today().isoformat()
    due_date = stop.date or date.today().isoformat()
    customer_name = stop.property.client.name

    rows = []

    def add_line(description: str, amount: float):
        amount_value = to_float(amount)
        if amount_value <= 0:
            return
        rows.append({
            "Invoice number": invoice_number,
            "Customer": customer_name,
            "Invoice date": invoice_date,
            "Due date": due_date,
            "Product/Service": "",
            "Description": description,
            "Item amount": f"{amount_value:.2f}",
            "Email": stop.property.client.email or "",
            "Message": stop.invoice_notes or "",
        })

    add_line("Service charge", stop.billed_amount)
    add_line("Materials", stop.material_cost)
    add_line("Trip charge", stop.trip_charge)
    add_line("Tax", stop.tax)

    if not rows:
        add_line("Service stop", 0.00)

    output = StringIO()
    fieldnames = [
        "Invoice number",
        "Customer",
        "Invoice date",
        "Due date",
        "Product/Service",
        "Description",
        "Item amount",
        "Email",
        "Message",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    csv_text = output.getvalue()
    output.close()

    filename = f"quickbooks_invoice_service_stop_{service_stop_id}.csv"

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/properties/{property_id}/service-stop/new", response_class=HTMLResponse)
def new_service_stop_page(request: Request, property_id: int):
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
        request,
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
    create_followup_task: str = Form("false"),
    followup_title: str = Form(""),
    followup_due_date: str = Form(""),
    followup_assigned_to: str = Form(""),
):
    db = SessionLocal()

    prop = (
        db.query(Property)
        .options(joinedload(Property.client))
        .filter(Property.id == property_id)
        .first()
    )
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

    if create_followup_task == "true" and followup_title.strip():
        task = Task(
            title=followup_title.strip(),
            status="open",
            job_type="Service Follow-up",
            due_date=followup_due_date.strip(),
            notes=recommendation.strip(),
            assigned_to=followup_assigned_to.strip(),
            client_id=prop.client_id,
        )
        db.add(task)
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

    return templates.TemplateResponse(
        request,
        "service_stop_detail.html",
        {
            "request": request,
            "service_stop": stop,
            "money": money,
            "service_stop_total": service_stop_total,
        },
    )


@app.post("/service-stops/{service_stop_id}/files/add")
def add_service_stop_file(
    service_stop_id: int,
    category: str = Form("general"),
    notes: str = Form(""),
    upload: UploadFile = File(...),
):
    db = SessionLocal()

    stop = db.query(ServiceStop).filter(ServiceStop.id == service_stop_id).first()
    if not stop:
        raise HTTPException(status_code=404, detail="Service stop not found")

    original_name, stored_name = save_upload(upload)

    file_record = FileAttachment(
        original_name=original_name,
        stored_name=stored_name,
        file_type=(upload.content_type or ""),
        notes=notes.strip(),
        category=category.strip(),
        service_stop_id=service_stop_id,
    )
    db.add(file_record)
    db.commit()

    return RedirectResponse(url=f"/service-stops/{service_stop_id}", status_code=303)


@app.post("/tasks/{task_id}/move")
def move_task(task_id: int, due_date: str = Form(...), assigned_to: str = Form("")):
    db = SessionLocal()

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.due_date = due_date.strip()
    db.commit()

    return JSONResponse({"success": True, "task_id": task_id, "due_date": task.due_date})


@app.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, assigned_to: str = Form("")):
    db = SessionLocal()

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = "done"

    if task.is_recurring:
        new_task = Task(
            title=task.title,
            status="open",
            job_type=task.job_type,
            due_date=next_due_date(task.due_date, task.reminder_type),
            notes=task.notes,
            assigned_to=task.assigned_to,
            reminder_type=task.reminder_type,
            is_recurring=True,
            client_id=task.client_id,
        )
        db.add(new_task)

    db.commit()

    redirect_url = "/schedule"
    if assigned_to.strip():
        redirect_url += f"?assigned_to={assigned_to.strip()}"

    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_page(request: Request, task_id: int):
    db = SessionLocal()

    task = (
        db.query(Task)
        .options(joinedload(Task.client))
        .filter(Task.id == task_id)
        .first()
    )

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    clients = db.query(Client).order_by(Client.name.asc()).all()

    return templates.TemplateResponse(
        request,
        "task_edit.html",
        {
            "request": request,
            "task": task,
            "clients": clients,
        },
    )


@app.post("/tasks/{task_id}/edit")
def update_task(
    task_id: int,
    title: str = Form(...),
    status: str = Form("open"),
    job_type: str = Form(""),
    due_date: str = Form(""),
    notes: str = Form(""),
    assigned_to: str = Form(""),
    reminder_type: str = Form(""),
    is_recurring: str = Form("false"),
    client_id: int | None = Form(None),
):
    db = SessionLocal()

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if client_id == 0:
        client_id = None

    task.title = title.strip()
    task.status = status.strip()
    task.job_type = job_type.strip()
    task.due_date = due_date.strip()
    task.notes = notes.strip()
    task.assigned_to = assigned_to.strip()
    task.reminder_type = reminder_type.strip()
    task.is_recurring = (is_recurring == "true")
    task.client_id = client_id

    db.commit()

    return RedirectResponse(url="/schedule", status_code=303)


@app.post("/tasks/{task_id}/delete")
def delete_task(task_id: int):
    db = SessionLocal()

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    db.delete(task)
    db.commit()

    return RedirectResponse(url="/schedule", status_code=303)


@app.post("/tasks/{task_id}/status")
def update_task_status(task_id: int, status: str = Form(...), assigned_to: str = Form("")):
    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = status.strip()
    db.commit()

    redirect_url = "/schedule"
    if assigned_to.strip():
        redirect_url += f"?assigned_to={assigned_to.strip()}"

    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_detail(request: Request, client_id: int):
    db = SessionLocal()
    client = (
        db.query(Client)
        .options(
            joinedload(Client.properties),
            joinedload(Client.tasks),
        )
        .filter(Client.id == client_id)
        .first()
    )

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "request": request,
            "client": client,
        },
    )


@app.get("/properties/{property_id}", response_class=HTMLResponse)
def property_detail(request: Request, property_id: int):
    db = SessionLocal()

    prop = (
        db.query(Property)
        .options(
            joinedload(Property.client),
            joinedload(Property.jobs).joinedload(Job.files),
            joinedload(Property.files),
            joinedload(Property.service_stops).joinedload(ServiceStop.files),
        )
        .filter(Property.id == property_id)
        .first()
    )

    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    property_job_count = len(prop.jobs)
    property_revenue = sum(to_float(job.quoted_price) for job in prop.jobs)
    property_cost = sum(job_total_cost(job) for job in prop.jobs)
    property_profit_value = property_revenue - property_cost
    property_margin_value = (property_profit_value / property_revenue * 100.0) if property_revenue > 0 else 0.0

    return templates.TemplateResponse(
        request,
        "property_detail.html",
        {
            "request": request,
            "property": prop,
            "job_total_cost": job_total_cost,
            "job_profit": job_profit,
            "job_margin": job_margin,
            "money": money,
            "property_job_count": property_job_count,
            "property_revenue": property_revenue,
            "property_cost": property_cost,
            "property_profit_value": property_profit_value,
            "property_margin_value": property_margin_value,
        },
    )


@app.post("/clients/add")
def add_client(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
):
    db = SessionLocal()
    client = Client(name=name.strip(), phone=phone.strip(), email=email.strip())
    db.add(client)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/clients/{client_id}/properties/add")
def add_property(
    client_id: int,
    address: str = Form(...),
    pool_type: str = Form(""),
    notes: str = Form(""),
):
    db = SessionLocal()
    client = db.query(Client).filter(Client.id == client_id).first()

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    prop = Property(
        address=address.strip(),
        pool_type=pool_type.strip(),
        notes=notes.strip(),
        client_id=client_id,
    )
    db.add(prop)
    db.commit()

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@app.post("/properties/{property_id}/equipment/update")
def update_equipment(
    property_id: int,
    pump: str = Form(""),
    filter: str = Form(""),
    heater: str = Form(""),
    sanitizer: str = Form(""),
    automation: str = Form(""),
    cleaner: str = Form(""),
    cover_type: str = Form(""),
    cover_notes: str = Form(""),
    install_year: str = Form(""),
    equipment_notes: str = Form(""),
):
    db = SessionLocal()

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    prop.pump = pump.strip()
    prop.filter = filter.strip()
    prop.heater = heater.strip()
    prop.sanitizer = sanitizer.strip()
    prop.automation = automation.strip()
    prop.cleaner = cleaner.strip()
    prop.cover_type = cover_type.strip()
    prop.cover_notes = cover_notes.strip()
    prop.install_year = install_year.strip()
    prop.equipment_notes = equipment_notes.strip()

    db.commit()

    return RedirectResponse(url=f"/properties/{property_id}", status_code=303)


@app.post("/properties/{property_id}/jobs/add")
def add_job(
    property_id: int,
    description: str = Form(...),
    date: str = Form(""),
    quoted_price: str = Form("0"),
    labor_cost: str = Form("0"),
    material_cost: str = Form("0"),
    subcontractor_cost: str = Form("0"),
    other_cost: str = Form("0"),
):
    db = SessionLocal()

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    job = Job(
        description=description.strip(),
        date=date.strip(),
        quoted_price=to_float(quoted_price),
        labor_cost=to_float(labor_cost),
        material_cost=to_float(material_cost),
        subcontractor_cost=to_float(subcontractor_cost),
        other_cost=to_float(other_cost),
        property_id=property_id,
    )
    db.add(job)
    db.commit()

    return RedirectResponse(url=f"/properties/{property_id}", status_code=303)


@app.post("/properties/{property_id}/files/add")
def add_property_file(
    property_id: int,
    category: str = Form("general"),
    notes: str = Form(""),
    upload: UploadFile = File(...),
):
    db = SessionLocal()

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    original_name, stored_name = save_upload(upload)

    file_record = FileAttachment(
        original_name=original_name,
        stored_name=stored_name,
        file_type=(upload.content_type or ""),
        notes=notes.strip(),
        category=category.strip(),
        property_id=property_id,
    )
    db.add(file_record)
    db.commit()

    return RedirectResponse(url=f"/properties/{property_id}", status_code=303)


@app.post("/jobs/{job_id}/files/add")
def add_job_file(
    job_id: int,
    category: str = Form("general"),
    notes: str = Form(""),
    upload: UploadFile = File(...),
):
    db = SessionLocal()

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    original_name, stored_name = save_upload(upload)

    file_record = FileAttachment(
        original_name=original_name,
        stored_name=stored_name,
        file_type=(upload.content_type or ""),
        notes=notes.strip(),
        category=category.strip(),
        job_id=job_id,
    )
    db.add(file_record)
    db.commit()

    return RedirectResponse(url=f"/properties/{job.property_id}", status_code=303)


@app.post("/tasks/add")
def add_task(
    title: str = Form(...),
    status: str = Form("open"),
    job_type: str = Form(""),
    due_date: str = Form(""),
    notes: str = Form(""),
    assigned_to: str = Form(""),
    reminder_type: str = Form(""),
    is_recurring: str = Form("false"),
    client_id: int | None = Form(None),
):
    db = SessionLocal()

    if client_id == 0:
        client_id = None

    task = Task(
        title=title.strip(),
        status=status.strip(),
        job_type=job_type.strip(),
        due_date=due_date.strip(),
        notes=notes.strip(),
        assigned_to=assigned_to.strip(),
        reminder_type=reminder_type.strip(),
        is_recurring=(is_recurring == "true"),
        client_id=client_id,
    )
    db.add(task)
    db.commit()

    if client_id:
        return RedirectResponse(url=f"/clients/{client_id}", status_code=303)

    return RedirectResponse(url="/", status_code=303)


@app.get("/dev/seed")
def seed():
    db = SessionLocal()

    client = Client(
        name="John Smith",
        phone="812-555-1212",
        email="john@email.com",
    )
    db.add(client)
    db.commit()

    prop = Property(
        address="1234 Oak Hill Rd",
        pool_type="Gunite",
        notes="Auto cover issue on right track.",
        pump="Pentair IntelliFlo",
        filter="Pentair Clean & Clear",
        heater="Pentair MasterTemp",
        sanitizer="Salt System",
        automation="EasyTouch",
        cleaner="Vac Daddy",
        cover_type="Automatic Cover",
        cover_notes="Right track drag / inspect rope wear.",
        install_year="2022",
        equipment_notes="Pad on east side of house.",
        client_id=client.id,
    )
    db.add(prop)
    db.commit()

    job = Job(
        description="Adjusted cover track and tested motor operation.",
        date="2026-04-10",
        quoted_price=450.00,
        labor_cost=150.00,
        material_cost=40.00,
        subcontractor_cost=0.00,
        other_cost=20.00,
        property_id=prop.id,
    )
    db.add(job)

    stop = ServiceStop(
        date="2026-04-12",
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
        invoice_notes="Customer requested emailed copy.",
        status="completed",
        property_id=prop.id,
    )
    db.add(stop)

    task = Task(
        title="Return to check cover",
        status="open",
        job_type="Service Call",
        due_date="2026-04-15",
        notes="Verify cover track alignment and motor operation.",
        assigned_to="Mike",
        reminder_type="weekly",
        is_recurring=True,
        client_id=client.id,
    )
    db.add(task)

    db.commit()

    return {"status": "seeded"}