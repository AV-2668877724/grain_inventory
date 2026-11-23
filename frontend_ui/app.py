"""Streamlit frontend - one file.  Run:  cd frontend && streamlit run app.py"""
import calendar
import html
import io
import os
import time
from xml.sax.saxutils import escape as xml_escape
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
st.set_page_config(page_title="Grain Store", page_icon="🌾", layout="wide")


# ---------------------------------------------------------------- talk to the API
@st.cache_resource
def http():
    """One shared connection: keeps the link to the server open, so calls don't pay the 'connect' cost every time."""
    return requests.Session()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _error_text(r):
    try:
        detail = r.json().get("detail", "Something went wrong.")
    except ValueError:
        return "Something went wrong."
    return "Please check the values you entered." if isinstance(detail, list) else detail  # list = FastAPI validation errors


def call(method, path, **kwargs):
    """One place for all API calls. Returns (ok, data_or_error_message)."""
    try:
        r = http().request(method, API + path, headers={"X-Token": st.session_state.get("token", "")}, timeout=10, **kwargs)
    except requests.RequestException:
        return False, "Cannot reach the server. Please start the backend."
    if r.status_code == 401 and st.session_state.get("token"):
        st.session_state.clear()
        st.rerun()
    return (True, r.json()) if r.ok else (False, _error_text(r))


@st.cache_data(ttl=10, show_spinner=False)
def _cached_get(path, token):
    r = http().get(API + path, headers={"X-Token": token}, timeout=10)
    if not r.ok:
        raise ApiError(r.status_code, _error_text(r))  # errors are never cached
    return r.json()


def cget(path):
    """Like call("GET"), but the answer is remembered for 10 seconds (used for the heavy analytics query)."""
    try:
        return True, _cached_get(path, st.session_state.get("token", ""))
    except ApiError as e:
        if e.status == 401:
            st.session_state.clear()
            st.rerun()
        return False, e.message
    except requests.RequestException:
        return False, "Cannot reach the server. Please start the backend."


def skeleton(cards=0, blocks=1, height=220):
    """Grey shimmering placeholder boxes shown while data loads (looks faster than a blank page)."""
    css = ("<style>@keyframes sk{0%{background-position:-600px 0}100%{background-position:600px 0}}"
           ".sk{border-radius:10px;margin-bottom:14px;background:linear-gradient(90deg,rgba(128,128,128,.10) 25%,"
           "rgba(128,128,128,.24) 40%,rgba(128,128,128,.10) 65%);background-size:1200px 100%;animation:sk 1.3s infinite linear}</style>")
    row = "<div style='display:flex;gap:16px'>" + "<div class='sk' style='flex:1;height:84px'></div>" * cards + "</div>" if cards else ""
    return css + row + f"<div class='sk' style='height:{height}px'></div>" * blocks


def forget_products():
    """Something changed: drop remembered products/answers so the next screen shows fresh data."""
    st.session_state.pop("products", None)
    st.cache_data.clear()


def logout():
    st.session_state.clear()


# ---------------------------------------------------------------- login
if "user" not in st.session_state:
    st.title("🌾 Grain Store")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            with st.spinner("Logging in..."):
                ok, data = call("POST", "/login", json={"username": username, "password": password})
            if ok:
                st.session_state.update(token=data["token"], user=data["user"], cart={})
                st.rerun()
            else:
                st.error(data)
    st.stop()

user = st.session_state.user
is_owner = user["role"] == "owner"

# ---------------------------------------------------------------- top bar
left, right = st.columns([3, 2], vertical_alignment="center")
left.markdown("## 🌾 Grain Store")
with right:
    u1, u2 = st.columns([2, 1], vertical_alignment="center")
    u1.markdown(f"👤 **{user['username']}** ({user['role']})")
    u2.button("Log out", on_click=logout)

pages = ["Billing", "Dashboard", "Products", "Bills"] if is_owner else ["Billing"]
page = st.radio("Menu", pages, horizontal=True, label_visibility="collapsed") if is_owner else "Billing"
st.divider()


# ---------------------------------------------------------------- Billing (cashier + owner)
def money(x):
    return f"₹{x:,.2f}"


def get_products(force=False):
    """Products are loaded once and kept in memory, so adding/removing items needs no server call (fast)."""
    ss = st.session_state
    if force or "products" not in ss or time.time() - ss.get("products_at", 0) > 120:
        ph = st.empty()
        ph.markdown(skeleton(0, 2, 90), unsafe_allow_html=True)
        ok, data = call("GET", "/products")
        ph.empty()
        if not ok:
            return None, data
        ss.products, ss.products_at = data, time.time()
    return ss.products, None


def add_item(p, qty_key):
    """Button callback: put the chosen quantity into the bill (checks stock first)."""
    ss = st.session_state
    qty = ss.get(qty_key, 1.0)
    have = ss.cart.get(p["id"], {}).get("qty", 0)
    if qty <= 0:
        ss.flash = ("error", "Quantity must be more than 0.")
    elif have + qty > p["stock"]:
        ss.flash = ("error", f"Only {p['stock']:g} kg of {p['name']} in stock" + (f" ({have:g} kg already on the bill)." if have else "."))
    else:
        ss.cart[p["id"]] = {"name": p["name"], "price": p["price"], "stock": p["stock"], "qty": have + qty}
        ss[f"q{p['id']}"] = have + qty  # keep the bill's quantity box in step
        ss.flash = ("success", f"Added {qty:g} kg {p['name']}")
        ss.confirm = False
        ss["pick"] = None  # clear the search box, ready for the next item


def remove_item(pid):
    st.session_state.cart.pop(pid, None)
    st.session_state.pop(f"q{pid}", None)
    st.session_state.confirm = False


def clear_bill():
    for pid in list(st.session_state.cart):
        st.session_state.pop(f"q{pid}", None)
    st.session_state.cart = {}
    st.session_state.confirm = False


def new_bill():
    st.session_state.pop("last_bill", None)
    st.session_state["pick"] = None  # back to the clean search screen
    st.session_state["cat"] = None


def stock_badge(stock):
    if stock <= 0:
        return "🔴 Out of stock"
    return f"🟠 Low: {stock:g} kg" if stock < 20 else f"🟢 {stock:g} kg"


def make_pdf(b):
    """Bill as a PDF (A5, like a printed receipt). Returns the file as bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    esc = xml_escape
    base = getSampleStyleSheet()["Normal"]
    centre = ParagraphStyle("c", parent=base, alignment=1, fontSize=9, textColor=colors.grey)
    title = ParagraphStyle("t", parent=base, alignment=1, fontSize=18, leading=22, fontName="Helvetica-Bold")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A5, leftMargin=34, rightMargin=34, topMargin=34, bottomMargin=34, title=f"Bill {b['bill_id']}")
    rows = [["#", "Item", "Kg", "Rate", "Amount"]]
    rows += [[str(i), it["name"], f"{it['qty']:g}", f"{it['price']:,.2f}", f"{it['amount']:,.2f}"] for i, it in enumerate(b["items"], 1)]
    rows.append(["", "", "", "TOTAL (Rs.)", f"{b['total']:,.2f}"])
    t = Table(rows, colWidths=[22, 140, 44, 66, 76], repeatRows=1)
    last = len(rows) - 1
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e9e5b")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("ROWBACKGROUNDS", (0, 1), (-1, last - 1), [colors.white, colors.HexColor("#f2f8f4")]),
        ("LINEABOVE", (0, last), (-1, last), 1, colors.black), ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
        ("FONTSIZE", (0, last), (-1, last), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    doc.build([
        Paragraph("Grain Store", title), Paragraph("Tax invoice / cash bill", centre), Spacer(1, 12),
        Paragraph(f"<b>Bill no:</b> {b['bill_id']} &nbsp;&nbsp; <b>Date:</b> {esc(b['date'])}", base),
        Paragraph(f"<b>Customer:</b> {esc(b['customer'])} &nbsp;&nbsp; <b>Cashier:</b> {esc(b['cashier'])}", base),
        Spacer(1, 12), t, Spacer(1, 18), Paragraph("Thank you! Visit again.", centre),
    ])
    return buf.getvalue()


def receipt_html(b):
    rows = "".join(f"<tr><td>{i}</td><td>{html.escape(it['name'])}</td><td class='r'>{it['qty']:g}</td>"
                   f"<td class='r'>{it['price']:g}</td><td class='r'>{it['amount']:,.2f}</td></tr>" for i, it in enumerate(b["items"], 1))
    return f"""
<style>
.rc{{background:#fff;color:#222;max-width:460px;margin:auto;padding:22px 24px;border-radius:8px;box-shadow:0 4px 18px rgba(0,0,0,.25);
    font-family:'Courier New',monospace;font-size:.92rem}}
.rc h3{{text-align:center;margin:0 0 2px;color:#222}} .rc .s{{text-align:center;color:#777;font-size:.8rem}}
.rc hr{{border:none;border-top:1px dashed #aaa;margin:10px 0}} .rc table{{width:100%;border-collapse:collapse}}
.rc th{{text-align:left;border-bottom:1px solid #333;padding:3px 2px;font-size:.82rem}} .rc td{{padding:3px 2px}} .r{{text-align:right}}
.rc .t td{{font-weight:700;font-size:1.1rem;border-top:1px solid #333;padding-top:6px}}
</style>
<div class='rc'><h3>🌾 Grain Store</h3><div class='s'>Bill #{b['bill_id']} · {b['date']}</div><hr>
<div><b>Customer:</b> {html.escape(b['customer'])}</div><div><b>Cashier:</b> {html.escape(b['cashier'])}</div><hr>
<table><tr><th>#</th><th>Item</th><th class='r'>Kg</th><th class='r'>Rate</th><th class='r'>Amount</th></tr>{rows}
<tr class='t'><td colspan='4' class='r'>TOTAL</td><td class='r'>₹{b['total']:,.2f}</td></tr></table>
<hr><div style='text-align:center;color:#555'>Thank you! Visit again.</div></div>"""


def product_row(p):
    c1, c2, c3, c4 = st.columns([3, 1.7, 1.2, 1.4], vertical_alignment="center")
    c1.markdown(f"**{p['name']}**  \n<span style='opacity:.7'>₹{p['price']:g}/kg</span>", unsafe_allow_html=True)
    c2.caption(stock_badge(p["stock"]))
    qk = f"add{p['id']}"
    c3.number_input("kg", min_value=0.5, step=0.5, value=1.0, key=qk, label_visibility="collapsed", disabled=p["stock"] <= 0)
    c4.button("➕ Add", key=f"b{p['id']}", on_click=add_item, args=(p, qk), disabled=p["stock"] <= 0, width="stretch")


@st.fragment(run_every=3)  # every 3 seconds: "did the owner change a price or stock?" (a tiny request)
def watch_changes():
    ss = st.session_state
    ok, d = call("GET", "/products/version")
    if not ok:
        return
    if ss.get("pv") is None:       # first look: just remember the number
        ss.pv = d["version"]
    elif d["version"] != ss.pv:    # something changed: reload products and redraw
        ss.pv = d["version"]
        forget_products()
        st.rerun()


@st.fragment  # a fragment re-runs only itself, so clicks here don't reload the whole page
def billing_page():
    ss = st.session_state
    ss.setdefault("cart", {})
    ss.setdefault("confirm", False)

    # ---- after the bill is made: preview + PDF download
    if "last_bill" in ss:
        b = ss.last_bill
        st.success(f"✅ Bill #{b['bill_id']} created. Stock has been updated.")
        _, mid, _ = st.columns([1, 2, 1])
        with mid:
            st.markdown(receipt_html(b), unsafe_allow_html=True)
            st.write("")
            d1, d2 = st.columns(2)
            d1.download_button("⬇ Download PDF", b["pdf"], file_name=f"bill_{b['bill_id']}.pdf", mime="application/pdf",
                               type="primary", width="stretch", on_click="ignore")
            d2.button("➕ Start new bill", on_click=new_bill, width="stretch")
        return

    products, err = get_products()
    if err:
        return st.error(err)
    by_name = {p["name"]: p for p in products}
    stock_now = {p["id"]: p for p in products}

    if "flash" in ss:
        kind, msg = ss.pop("flash")
        {"success": st.success, "error": st.error, "info": st.info}[kind](msg)

    left, right = st.columns([1.15, 1], gap="large")

    # ================= LEFT: find and add products (nothing is listed until you search or pick a category)
    with left:
        st.subheader("1 · Find products")
        pick = st.selectbox("Search", list(by_name), index=None, key="pick", placeholder="🔍  Type a product name...", label_visibility="collapsed")
        cat = st.pills("Or browse by category", sorted({p["category"] for p in products}), key="cat")
        if pick:
            shown = [by_name[pick]]
        elif cat:
            shown = [p for p in products if p["category"] == cat]
        else:
            shown = []
        with st.container(border=True):
            if not shown:
                st.markdown("<div style='text-align:center;padding:34px 8px;opacity:.65'>🌾<br><b>Start typing a product name</b><br>"
                            "or choose a category above</div>", unsafe_allow_html=True)
            for p in shown:
                product_row(p)

    # ================= RIGHT: the bill
    with right:
        st.subheader("2 · Bill")
        cart = ss.cart
        if not cart:
            st.info("🧾 The bill is empty. Search a product on the left and press Add.")
            return
        total, changed = 0.0, []
        with st.container(border=True):
            for pid, it in list(cart.items()):
                live = stock_now.get(pid)
                if not live:
                    cart.pop(pid)
                    continue
                if abs(it["price"] - live["price"]) > 0.001:  # the owner changed this price: use the new one
                    changed.append(f"**{it['name']}**: ₹{it['price']:g} → ₹{live['price']:g} per kg")
                    it["price"] = live["price"]
                it["stock"] = live["stock"]
                qk = f"q{pid}"
                if qk not in ss or ss[qk] > live["stock"]:
                    ss[qk] = min(it["qty"], live["stock"])
                c1, c2, c3, c4 = st.columns([3, 1.7, 1.6, 0.7], vertical_alignment="center")
                c1.markdown(f"**{it['name']}**  \n<span style='opacity:.7'>₹{it['price']:g}/kg</span>", unsafe_allow_html=True)
                it["qty"] = c2.number_input("kg", min_value=0.5, max_value=max(live["stock"], 0.5), step=0.5, key=qk, label_visibility="collapsed")
                amount = it["qty"] * it["price"]
                total += amount
                c3.markdown(f"**{money(amount)}**")
                c4.button("", key=f"rm{pid}", icon=":material/close:", on_click=remove_item, args=(pid,), help="Remove")
        if changed:
            st.warning("💲 Price updated by the owner, total recalculated:  \n" + "  \n".join(changed))
        st.markdown(f"<div style='display:flex;justify-content:space-between;padding:12px 16px;border-radius:10px;"
                    f"background:rgba(46,160,67,.13);border:1px solid rgba(46,160,67,.4);font-size:1.3rem;font-weight:700'>"
                    f"<span>Total · {len(cart)} item(s)</span><span>{money(total)}</span></div>", unsafe_allow_html=True)
        st.write("")
        customer = st.text_input("Customer name (optional)", placeholder="Walk-in customer")

        if not ss.confirm:
            b1, b2 = st.columns([2, 1])
            b1.button("Review & generate bill →", type="primary", width="stretch", on_click=lambda: ss.update(confirm=True))
            b2.button("Clear", width="stretch", on_click=clear_bill)
        else:
            with st.container(border=True):
                st.markdown(f"**Please confirm**  \nBill for **{customer or 'Walk-in customer'}**, total **{money(total)}**.  \n"
                            "Stock will be reduced once you confirm.")
                y, n = st.columns(2)
                go = y.button("✅ Confirm & generate", type="primary", width="stretch")
                n.button("Keep editing", width="stretch", on_click=lambda: ss.update(confirm=False))
            if go:
                payload = {"customer_name": customer or "Walk-in", "items": [{"product_id": pid, "qty": it["qty"], "price": it["price"]} for pid, it in cart.items()]}
                with st.spinner("Saving bill..."):
                    ok, data = call("POST", "/bills", json=payload)
                if ok:
                    data.update(customer=customer or "Walk-in customer", date=datetime.now().strftime("%d %b %Y, %I:%M %p"),
                                cashier=ss.user["username"])
                    data["pdf"] = make_pdf(data)
                    clear_bill()
                    forget_products()  # stock changed: reload next time
                    ss.pv = None       # (our own change must not trigger a refresh)
                    ss.last_bill = data
                    st.rerun(scope="fragment")
                else:
                    ss.confirm = False
                    forget_products()  # maybe a price/stock changed: reload and show it
                    ss.flash = ("error", data)
                    st.rerun(scope="fragment")


# ---------------------------------------------------------------- Dashboard (owner): easy analysis
GREEN, ORANGE = "#2e9e5b", "#f2a23a"


PRESETS = ["Today", "Last 7 days", "Last 30 days", "This month", "Last month", "This year",
           "Pick a month", "Pick a year", "Custom dates", "All time"]


def period_picker():
    """Lets the owner choose the dates to analyse. Returns (start, end) - both None for 'All time' - or None while still choosing."""
    today = date.today()
    choice = st.pills("Show sales for", PRESETS, default="Last 30 days", key="dp_widget") or "Last 30 days"
    if choice == "Today":
        return today, today
    if choice == "Last 7 days":
        return today - timedelta(days=6), today
    if choice == "Last 30 days":
        return today - timedelta(days=29), today
    if choice == "This month":
        return today.replace(day=1), today
    if choice == "Last month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if choice == "This year":
        return date(today.year, 1, 1), today
    if choice == "Pick a month":
        months, y, m = [], today.year, today.month
        for _ in range(36):
            months.append((y, m))
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        y, m = st.selectbox("Month", months, format_func=lambda ym: f"{calendar.month_name[ym[1]]} {ym[0]}", key="dp_month")
        return date(y, m, 1), min(today, date(y, m, calendar.monthrange(y, m)[1]))
    if choice == "Pick a year":
        year = st.selectbox("Year", list(range(today.year, today.year - 8, -1)), key="dp_year")
        return date(year, 1, 1), min(today, date(year, 12, 31))
    if choice == "Custom dates":
        r = st.date_input("From - To", value=(today - timedelta(days=29), today), max_value=today, key="dp_range")
        if len(r) != 2:
            st.info("Now pick the end date.")
            return None
        return r[0], r[1]
    return None, None  # All time


def money0(x):
    return f"₹{x:,.0f}"


def change(x):  # a leading "+" / "-" makes the arrow green / red correctly
    return f"{'-' if x < 0 else '+'}₹{abs(x):,.0f}"


def dashboard_page():
    t1, t2 = st.columns([6, 1], vertical_alignment="center")
    t1.caption("Choose the period you want to look at. Numbers are kept for 10 seconds; press Refresh for the latest.")
    t2.button("🔄 Refresh", on_click=st.cache_data.clear, width="stretch")
    picked = period_picker()
    if picked is None:
        return
    start, end = picked

    ph = st.empty()
    ph.markdown(skeleton(4, 2, 260), unsafe_allow_html=True)
    query = f"?start={start}&end={end}" if start else ""
    ok, a = cget(f"/analytics{query}")
    ph.empty()
    if not ok:
        return st.error(a)
    s, prev = a["summary"], a["previous"]

    if a["low_stock"]:
        st.warning("⚠ **Running low:** " + ", ".join(f"{x['name']} ({x['stock']:g} kg left)" for x in a["low_stock"]) + " - time to restock.")
    if not a["daily"]:
        return st.info("No sales in this period. Try a longer period.")

    # one row per day, 0 for days without sales
    got = pd.DataFrame(a["daily"])
    got["day"] = pd.to_datetime(got["day"])
    first = pd.Timestamp(start) if start else got.day.min()
    last = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    d = pd.DataFrame({"day": pd.date_range(first, last)}).merge(got[["day", "sales"]], on="day", how="left").fillna({"sales": 0.0})
    span = len(d)
    label = f"{first:%d %b %Y} - {last:%d %b %Y}" if span > 1 else f"{first:%d %b %Y}"

    # ---- key numbers (with a comparison to the period just before, when there is one)
    st.markdown(f"#### 📊 {label}  <span style='opacity:.6;font-size:.9rem'>({span} day{'s' if span != 1 else ''})</span>", unsafe_allow_html=True)
    vs = f"vs previous {prev['days']} day(s)" if prev else ""
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total sales", money0(s["total_sales"]), f"{change(s['total_sales'] - prev['total_sales'])} {vs}" if prev else None)
    k2.metric("Number of bills", int(s["bills"]), f"{int(s['bills'] - prev['bills']):+d} {vs}" if prev else None)
    k3.metric("Average bill", money0(s["avg_bill"]), f"{change(s['avg_bill'] - prev['avg_bill'])} {vs}" if prev else None)
    k4.metric("Grain sold", f"{s['kg_sold']:,.0f} kg", f"{s['kg_sold'] - prev['kg_sold']:+,.0f} kg {vs}" if prev else None)

    # ---- group the days into days / weeks / months so the chart stays readable
    if span <= 31:
        freq, unit, fmt = "D", "day", "%d %b"
    elif span <= 190:
        freq, unit, fmt = "W-MON", "week", "%d %b"
    else:
        freq, unit, fmt = "MS", "month", "%b %Y"
    if freq == "D":
        trend_df = d.copy()
    else:
        trend_df = d.set_index("day").sales.resample(freq, label="left", closed="left").sum().reset_index()
    trend_df["label"] = trend_df.day.dt.strftime(fmt)
    best = trend_df.loc[trend_df.sales.idxmax()]

    top = a["top_products"][0] if a["top_products"] else None
    msg = f"📅 **Best {unit}:** {'week of ' if unit == 'week' else ''}{best.label} with {money0(best.sales)}"
    if top:
        msg += f"   ·   🏆 **Best seller:** {top['name']} ({money0(top['revenue'])}, {top['kg_sold']:g} kg)"
    if a["by_hour"]:
        h = max(a["by_hour"], key=lambda r: r["sales"])["hour"]
        msg += f"   ·   ⏰ **Busiest hour:** {h % 12 or 12} {'AM' if h < 12 else 'PM'}"
    st.info(msg)

    # ---- 1. sales over time
    st.subheader(f"📈 Sales per {unit}")
    avg = trend_df.sales.mean()
    tip = [alt.Tooltip("label:N", title=unit.capitalize() if unit != "week" else "Week starting"), alt.Tooltip("sales:Q", title="Sales ₹", format=",.0f")]
    if freq == "D":
        base = alt.Chart(trend_df).encode(x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b", labelAngle=0, tickCount=8)),
                                          y=alt.Y("sales:Q", title="Sales (₹)"), tooltip=tip)
        trend = base.mark_area(opacity=0.15, color=GREEN, line={"color": GREEN, "strokeWidth": 2.5}) + base.mark_point(filled=True, size=45, color=GREEN)
    else:
        trend = alt.Chart(trend_df).mark_bar(color=GREEN, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("label:N", sort=None, title=None, axis=alt.Axis(labelAngle=-45 if len(trend_df) > 12 else 0)),
            y=alt.Y("sales:Q", title="Sales (₹)"), tooltip=tip)
    avg_line = alt.Chart(pd.DataFrame({"avg": [avg]})).mark_rule(strokeDash=[6, 4], color=ORANGE, strokeWidth=2).encode(y="avg:Q")
    st.altair_chart((trend + avg_line).properties(height=300), width="stretch")
    st.caption(f"Green = sales per {unit}.  Orange dotted line = average ({money0(avg)} per {unit}). Anything above the line was a good {unit}.")

    c1, c2 = st.columns(2, gap="large")

    # ---- 2. best sellers
    with c1:
        st.subheader("🏆 Which products earn the most?")
        t = pd.DataFrame(a["top_products"])
        bars = alt.Chart(t).mark_bar(color=GREEN, cornerRadiusEnd=4).encode(
            y=alt.Y("name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
            x=alt.X("revenue:Q", title="Total sales (₹)", scale=alt.Scale(domain=[0, float(t.revenue.max()) * 1.25])),
            tooltip=[alt.Tooltip("name:N", title="Product"), alt.Tooltip("revenue:Q", title="Sales ₹", format=",.0f"),
                     alt.Tooltip("kg_sold:Q", title="Kg sold", format=",.1f")])
        labels = bars.mark_text(align="left", dx=5).encode(text=alt.Text("revenue:Q", format=",.0f"))
        st.altair_chart((bars + labels).properties(height=max(120, 36 * len(t) + 40)), width="stretch")
        st.caption("Products ranked by money earned in this period.")

    # ---- 3. category share
    with c2:
        st.subheader("🥧 What kind of grain sells?")
        c = pd.DataFrame(a["by_category"])
        c["share"] = c.revenue / c.revenue.sum()
        c["label"] = c.apply(lambda r: f"{r.category} - {r.share:.0%}", axis=1)
        donut = alt.Chart(c).mark_arc(innerRadius=65, outerRadius=115).encode(
            theta=alt.Theta("revenue:Q"),
            color=alt.Color("label:N", legend=alt.Legend(title=None, orient="right", labelFontSize=13),
                            scale=alt.Scale(range=[GREEN, "#4a90d9", ORANGE, "#9b6fd0", "#d9534f", "#8a8a8a"])),
            tooltip=[alt.Tooltip("category:N", title="Category"), alt.Tooltip("revenue:Q", title="Sales ₹", format=",.0f"),
                     alt.Tooltip("share:Q", title="Share", format=".0%")])
        st.altair_chart(donut.properties(height=280), width="stretch")
        st.caption("Share of sales by category.")

    # ---- 4. timing: busiest hours (+ busiest weekday when the period is long enough)
    show_weekday = span >= 14
    h_col, w_col = st.columns(2, gap="large") if show_weekday else (st.container(), None)
    with h_col:
        st.subheader("⏰ Which hours are busiest?")
        hrs = pd.DataFrame(a["by_hour"])
        hrs["time"] = hrs.hour.map(lambda h: f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}")
        hbars = alt.Chart(hrs).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("time:N", sort=alt.EncodingSortField("hour"), title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("sales:Q", title="Sales (₹)"),
            color=alt.condition(alt.datum.sales == float(hrs.sales.max()), alt.value(GREEN), alt.value("#a9d9bb")),
            tooltip=[alt.Tooltip("time:N", title="Hour"), alt.Tooltip("sales:Q", title="Sales ₹", format=",.0f"), alt.Tooltip("bills:Q", title="Bills")])
        st.altair_chart(hbars.properties(height=240), width="stretch")
        st.caption("Dark green = your busiest hour. Good to know when to keep more cashiers ready.")
    if show_weekday:
        with w_col:
            st.subheader("🗓 Which day of the week is busiest?")
            wd = d.assign(weekday=d.day.dt.day_name()).groupby("weekday", as_index=False).sales.mean()
            order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            wbars = alt.Chart(wd).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("weekday:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelExpr="slice(datum.label,0,3)")),
                y=alt.Y("sales:Q", title="Average sales (₹)"),
                color=alt.condition(alt.datum.sales == float(wd.sales.max()), alt.value(GREEN), alt.value("#a9d9bb")),
                tooltip=[alt.Tooltip("weekday:N", title="Day"), alt.Tooltip("sales:Q", title="Average sales ₹", format=",.0f")])
            st.altair_chart(wbars.properties(height=240), width="stretch")
            st.caption("Average sales on each weekday. Useful for deciding when to stock up.")


# ---------------------------------------------------------------- Products (owner)
def products_page():
    ss = st.session_state
    if "pflash" in ss:
        st.success(ss.pop("pflash"))
    ph = st.empty()
    ph.markdown(skeleton(0, 1, 300), unsafe_allow_html=True)
    ok, products = call("GET", "/products")  # always fresh here
    ph.empty()
    if not ok:
        return st.error(products)

    st.subheader("Prices & stock")
    st.caption("✏️ Double-click a price to change it, then press Save. Cashiers see the new price within a few seconds, and old bills keep their old price.")
    df = pd.DataFrame(products)[["id", "name", "category", "price", "stock"]]
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key=f"price_editor{ss.get('ed_n', 0)}",
        disabled=["name", "category", "stock"],
        column_config={"id": None, "name": "Product", "category": "Category",
                       "price": st.column_config.NumberColumn("Price ₹/kg ✏️", min_value=0.01, step=0.5, format="₹%.2f", required=True),
                       "stock": st.column_config.NumberColumn("Stock (kg)", format="%.1f")})
    edited = edited.dropna(subset=["price"])
    changes = edited[edited.price.round(2) != df.set_index("id").price.reindex(edited.id).round(2).values]
    if len(changes):
        old = df.set_index("id").price
        st.info("**Not saved yet:**  \n" + "  \n".join(f"{r['name']}: ₹{old[r['id']]:g} → ₹{r['price']:g}" for _, r in changes.iterrows()))
        if st.button(f"💾 Save {len(changes)} price change(s)", type="primary"):
            done, errors = [], []
            with st.spinner("Saving..."):
                for _, r in changes.iterrows():
                    ok, res = call("PUT", f"/products/{int(r['id'])}/price", json={"price": float(r["price"])})
                    (done if ok else errors).append(f"{r['name']}: ₹{res['old']:g} → ₹{res['new']:g}" if ok else f"{r['name']}: {res}")
            forget_products()
            ss.ed_n = ss.get("ed_n", 0) + 1  # reset the editor
            for e in errors:
                st.error(e)
            if done:
                ss.pflash = "Price updated - " + ";  ".join(done)
                st.rerun()

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Restock")
        with st.form("restock", clear_on_submit=True):
            name = st.selectbox("Product", [p["name"] for p in products])
            qty = st.number_input("Add quantity (kg)", min_value=0.0, step=5.0)
            if st.form_submit_button("Add stock"):
                pid = next(p["id"] for p in products if p["name"] == name)
                ok, msg = call("POST", f"/products/{pid}/restock", json={"qty": qty})
                if ok:
                    forget_products()
                    ss.pflash = f"Added {qty:g} kg to {name}."
                    st.rerun()
                st.error(msg)
    with c2:
        st.subheader("Add new product")
        with st.form("newprod", clear_on_submit=True):
            name = st.text_input("Name")
            category = st.text_input("Category", placeholder="Rice, Wheat, Pulses...")
            price = st.number_input("Price per kg (₹)", min_value=0.0, step=1.0)
            stock = st.number_input("Opening stock (kg)", min_value=0.0, step=5.0)
            if st.form_submit_button("Add product"):
                ok, msg = call("POST", "/products", json={"name": name, "category": category, "price": price, "stock": stock})
                if ok:
                    forget_products()
                    ss.pflash = f"Product '{name}' added."
                    st.rerun()
                st.error(msg)


# ---------------------------------------------------------------- Bills (owner)
def bills_page():
    ph = st.empty()
    ph.markdown(skeleton(0, 1, 300), unsafe_allow_html=True)
    ok, bills = call("GET", "/bills")
    ph.empty()
    if not ok:
        return st.error(bills)
    if not bills:
        return st.info("No bills yet.")
    df = pd.DataFrame(bills).rename(columns={"id": "Bill no", "bill_date": "Date", "customer_name": "Customer", "total": "Total ₹", "cashier": "Cashier"})
    st.dataframe(df, hide_index=True, width="stretch")


if page == "Billing":
    watch_changes()
{"Billing": billing_page, "Dashboard": dashboard_page, "Products": products_page, "Bills": bills_page}[page]()
