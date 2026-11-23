"""FastAPI backend - the whole API in one file.

Run:  cd backend && uvicorn main:app --reload
Docs: http://localhost:8000/docs   (FastAPI makes a test page automatically)
"""
import hashlib
import logging
import os
import secrets
import time
from datetime import date, timedelta

import mysql.connector
from mysql.connector import pooling
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- settings + log
DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", "root"),
    database=os.getenv("DB_NAME", "grain_store"),
)
logging.basicConfig(filename="app.log", level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("grain")

app = FastAPI(title="Grain Store API")
SESSIONS = {}  # token -> {"id":..., "username":..., "role":...}   (kept in memory; restart = log in again)


POOL = None
PRODUCTS_VERSION = int(time.time())  # goes up every time products/prices/stock change; cashier screens watch it


def bump():
    global PRODUCTS_VERSION
    PRODUCTS_VERSION += 1


def get_conn():
    """Connections are re-used from a pool (much faster than opening a new one for every query)."""
    global POOL
    try:
        if POOL is None:
            POOL = pooling.MySQLConnectionPool(pool_name="grain", pool_size=8, **DB)
        return POOL.get_connection()
    except mysql.connector.Error:
        log.exception("database connection failed")
        raise HTTPException(503, "Cannot reach the database. Is MySQL running?")


def run(sql, params=(), many=False):
    """Run one SQL query. SELECT returns a list of dicts, other queries return the new id."""
    conn = get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        if sql.strip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            conn.commit()  # end the read, so this pooled connection sees fresh data next time
            return [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items()} for r in rows]
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------- login + roles
def current_user(x_token: str = Header(default="")):
    user = SESSIONS.get(x_token)
    if not user:
        raise HTTPException(401, "Please log in again.")
    return user


def owner_only(user=Depends(current_user)):
    if user["role"] != "owner":
        raise HTTPException(403, "Only the owner can do this.")
    return user


class Login(BaseModel):
    username: str
    password: str


@app.post("/login")
def login(data: Login):
    hashed = hashlib.sha256(data.password.encode()).hexdigest()
    rows = run("SELECT id, username, role FROM users WHERE username=%s AND password=%s", (data.username, hashed))
    if not rows:
        log.warning("failed login for %s", data.username)
        raise HTTPException(401, "Wrong username or password.")
    token = secrets.token_hex(16)
    SESSIONS[token] = rows[0]
    log.info("login: %s (%s)", rows[0]["username"], rows[0]["role"])
    return {"token": token, "user": rows[0]}


# ---------------------------------------------------------------- products
class NewProduct(BaseModel):
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    price: float = Field(gt=0)
    stock: float = Field(ge=0)


@app.get("/products/version")
def products_version(user=Depends(current_user)):
    """Tiny call the cashier screen repeats every few seconds to learn whether products/prices changed."""
    return {"version": PRODUCTS_VERSION}


@app.get("/products")
def products(user=Depends(current_user)):
    return run("SELECT * FROM products ORDER BY name")


@app.post("/products")
def add_product(p: NewProduct, user=Depends(owner_only)):
    try:
        pid = run("INSERT INTO products (name, category, price, stock) VALUES (%s,%s,%s,%s)",
                  (p.name.strip(), p.category.strip(), p.price, p.stock))
    except mysql.connector.IntegrityError:
        raise HTTPException(400, "A product with this name already exists.")
    bump()
    log.info("%s added product %s", user["username"], p.name)
    return {"id": pid}


class Restock(BaseModel):
    qty: float = Field(gt=0)


@app.post("/products/{pid}/restock")
def restock(pid: int, r: Restock, user=Depends(owner_only)):
    run("UPDATE products SET stock = stock + %s WHERE id=%s", (r.qty, pid))
    bump()
    log.info("%s restocked product %s by %s", user["username"], pid, r.qty)
    return {"ok": True}


class NewPrice(BaseModel):
    price: float = Field(gt=0)


@app.put("/products/{pid}/price")
def change_price(pid: int, body: NewPrice, user=Depends(owner_only)):
    """Owner changes a price. Takes effect at once; old bills keep the price they were sold at."""
    old = run("SELECT name, price FROM products WHERE id=%s", (pid,))
    if not old:
        raise HTTPException(404, "Product not found.")
    run("UPDATE products SET price=%s WHERE id=%s", (body.price, pid))
    bump()
    log.info("%s changed price of %s from %s to %s", user["username"], old[0]["name"], old[0]["price"], body.price)
    return {"ok": True, "name": old[0]["name"], "old": old[0]["price"], "new": body.price}


# ---------------------------------------------------------------- billing
class Item(BaseModel):
    product_id: int
    qty: float = Field(gt=0)
    price: float | None = None  # the price the cashier saw; if the owner changed it meanwhile, the bill is refused


class NewBill(BaseModel):
    customer_name: str = "Walk-in"
    items: list[Item] = Field(min_length=1)


@app.post("/bills")
def create_bill(bill: NewBill, user=Depends(current_user)):
    """Checks stock, saves the bill and reduces the stock - all or nothing."""
    conn = get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        lines, total = [], 0.0
        for it in bill.items:
            cur.execute("SELECT id, name, price, stock FROM products WHERE id=%s FOR UPDATE", (it.product_id,))
            p = cur.fetchone()
            if not p:
                raise HTTPException(404, "Product not found.")
            if it.price is not None and abs(float(p["price"]) - it.price) > 0.001:
                raise HTTPException(409, f"Price of {p['name']} just changed to ₹{float(p['price']):g}/kg. Please review the bill.")
            if float(p["stock"]) < it.qty:
                raise HTTPException(400, f"Only {float(p['stock']):g} kg of {p['name']} in stock.")
            amount = round(float(p["price"]) * it.qty, 2)
            total += amount
            lines.append((p["id"], p["name"], it.qty, float(p["price"]), amount))
        cur.execute("INSERT INTO bills (customer_name, total, cashier_id) VALUES (%s,%s,%s)",
                    (bill.customer_name.strip() or "Walk-in", round(total, 2), user["id"]))
        bill_id = cur.lastrowid
        for pid, _, qty, price, _ in lines:
            cur.execute("INSERT INTO bill_items (bill_id, product_id, qty, price) VALUES (%s,%s,%s,%s)", (bill_id, pid, qty, price))
            cur.execute("UPDATE products SET stock = stock - %s WHERE id=%s", (qty, pid))
        conn.commit()
        bump()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        log.exception("bill failed")
        raise HTTPException(500, "Could not save the bill. Nothing was changed.")
    finally:
        conn.close()
    log.info("bill %s Rs.%.2f by %s", bill_id, total, user["username"])
    return {"bill_id": bill_id, "total": round(total, 2),
            "items": [{"name": n, "qty": q, "price": pr, "amount": a} for _, n, q, pr, a in lines]}


@app.get("/bills")
def bills(user=Depends(owner_only)):
    return run("""SELECT b.id, b.bill_date, b.customer_name, b.total, u.username AS cashier
                  FROM bills b JOIN users u ON u.id = b.cashier_id ORDER BY b.id DESC LIMIT 200""")


# ---------------------------------------------------------------- easy analysis (owner)
def period_filter(start, end):
    """SQL piece that limits bills to a date range (both dates included). No dates = all time."""
    sql, params = "", []
    if start:
        sql += " AND b.bill_date >= %s"
        params.append(start)
    if end:
        sql += " AND b.bill_date < %s"
        params.append(end + timedelta(days=1))
    return sql, tuple(params)


def sales_summary(start, end):
    f, p = period_filter(start, end)
    row = run(f"SELECT COALESCE(SUM(total),0) AS total_sales, COUNT(*) AS bills, COALESCE(AVG(total),0) AS avg_bill "
              f"FROM bills b WHERE 1=1 {f}", p)[0]
    row["kg_sold"] = run(f"SELECT COALESCE(SUM(i.qty),0) AS kg FROM bill_items i JOIN bills b ON b.id = i.bill_id WHERE 1=1 {f}", p)[0]["kg"]
    return row


@app.get("/analytics")
def analytics(start: date | None = None, end: date | None = None, user=Depends(owner_only)):
    """Everything the dashboard shows, for the chosen dates (?start=2026-11-01&end=2026-11-30). No dates = all time."""
    if start and end and start > end:
        raise HTTPException(400, "The start date is after the end date.")
    f, p = period_filter(start, end)

    previous = None  # the period right before this one, same length, for "vs previous" arrows
    if start and end:
        days = (end - start).days + 1
        p_end = start - timedelta(days=1)
        p_start = p_end - timedelta(days=days - 1)
        previous = sales_summary(p_start, p_end)
        previous.update(start=p_start, end=p_end, days=days)

    daily = run(f"""SELECT DATE(b.bill_date) AS day, SUM(b.total) AS sales, COUNT(*) AS bills
                    FROM bills b WHERE 1=1 {f} GROUP BY DATE(b.bill_date) ORDER BY day""", p)
    by_hour = run(f"""SELECT HOUR(b.bill_date) AS hour, SUM(b.total) AS sales, COUNT(*) AS bills
                      FROM bills b WHERE 1=1 {f} GROUP BY HOUR(b.bill_date) ORDER BY hour""", p)
    top = run(f"""SELECT p.name, SUM(i.qty) AS kg_sold, SUM(i.qty*i.price) AS revenue
                  FROM bill_items i JOIN bills b ON b.id = i.bill_id JOIN products p ON p.id = i.product_id
                  WHERE 1=1 {f} GROUP BY p.id ORDER BY revenue DESC LIMIT 8""", p)
    category = run(f"""SELECT p.category, SUM(i.qty*i.price) AS revenue
                       FROM bill_items i JOIN bills b ON b.id = i.bill_id JOIN products p ON p.id = i.product_id
                       WHERE 1=1 {f} GROUP BY p.category""", p)
    low = run("SELECT name, stock FROM products WHERE stock < 20 ORDER BY stock")
    return {"summary": sales_summary(start, end), "previous": previous, "daily": daily, "by_hour": by_hour,
            "top_products": top, "by_category": category, "low_stock": low}
