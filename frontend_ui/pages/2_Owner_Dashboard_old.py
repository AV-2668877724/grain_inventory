import streamlit as st
import pandas as pd
from api_client import get_inventory, get_history, create_new_user

st.set_page_config(page_title="Owner Dashboard", layout="wide")

if not st.session_state.get("logged_in") or st.session_state.role != "owner":
    st.error("🚫 Access Denied: Executive clearance required.")
    st.stop()

st.title("📊 Owner Operations Dashboard")

# Fetch all data
inventory = get_inventory()
history = get_history()

tab_inv, tab_reorder, tab_analytics, tab_history, tab_users = st.tabs([
    "📦 Live Inventory", "⚠️ Needed Stock", "📈 Sales Analytics", "📜 Order History", "👥 Users"
])

with tab_inv:
    st.subheader("Master Inventory")
    if inventory:
        df_inv = pd.DataFrame(inventory)
        st.dataframe(df_inv[['pid', 'pname', 'category', 'variety', 'qty', 'price']], use_container_width=True, hide_index=True)
    else:
        st.error("No inventory data found.")

with tab_reorder:
    st.subheader("Low Stock Alerts (Needs Reorder)")
    if inventory:
        # Filter items with quantity less than 20
        needed_stock = [item for item in inventory if item['qty'] < 20]
        if needed_stock:
            st.warning(f"{len(needed_stock)} items are running low and need to be ordered.")
            st.dataframe(pd.DataFrame(needed_stock)[['pid', 'pname', 'qty', 'price']], use_container_width=True, hide_index=True)
        else:
            st.success("All stock levels are healthy! No reorders needed.")

with tab_analytics:
    st.subheader("Sales Analytics")
    if history:
        df_hist = pd.DataFrame(history)
        df_hist['order_date'] = pd.to_datetime(df_hist['order_date'])
        
        # Calculate Revenue Metrics
        total_revenue = df_hist['total_amt'].sum()
        total_orders = len(df_hist)
        
        col1, col2 = st.columns(2)
        col1.metric("Total Revenue", f"${total_revenue:.2f}")
        col2.metric("Total Orders Processed", total_orders)
        
        st.markdown("### Revenue over Time")
        # Group by date for a line chart
        daily_sales = df_hist.groupby(df_hist['order_date'].dt.date)['total_amt'].sum()
        st.line_chart(daily_sales)
    else:
        st.info("Not enough sales data to generate analytics.")

with tab_history:
    st.subheader("Complete Order Records")
    if history:
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
    else:
        st.info("No orders have been placed yet.")

with tab_users:
    st.subheader("Add System Users")
    with st.form("new_user_form"):
        u_name = st.text_input("Username")
        u_pass = st.text_input("Password", type="password")
        u_role = st.selectbox("Role", ["Cashier", "Owner"])
        if st.form_submit_button("Create User"):
            lvl = 1 if u_role == "Owner" else 0
            success, msg = create_new_user(u_name, u_pass, lvl)
            if success:
                st.success(msg)
            else:
                st.error(msg)