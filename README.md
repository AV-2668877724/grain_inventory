# Grain Store – Mini Project

Python + SQL (MySQL) + FastAPI + Streamlit.

```
grain_mini_project/
├── schema.sql          # 4 tables + starter data
├── backend/main.py     # FastAPI: login, products, bills, analytics
├── frontend/app.py     # Streamlit: login, billing, dashboard, products, bills
└── requirements.txt
```

## Run it
1. `mysql -u root -p < schema.sql`
2. `pip install -r requirements.txt`
3. Backend (terminal 1): `cd backend` then `uvicorn main:app --reload`
   (DB password: set `DB_PASSWORD`, default is `root`. Test page: http://localhost:8000/docs)
4. Frontend (terminal 2): `cd frontend` then `streamlit run app.py`

Logins: **owner / admin123** (everything) and **cashier / cash123** (billing only).

## Database (4 tables)
`users` → `bills` (cashier_id) → `bill_items` ← `products`

## Analysis shown to the owner
Total / today's sales, number of bills, average bill, 30-day sales line, top 5 products, sales by category, low-stock warning. Each one is a single SQL query in `backend/main.py` (`/analytics`).

## Roles
The backend checks the role on every request (`owner_only`), so a cashier cannot open owner features even by calling the API directly.
Every login, bill and stock change is written to `backend/app.log`.
