import streamlit as st
import pandas as pd
from api_client import get_inventory, place_order

st.set_page_config(page_title="Cashier POS", layout="wide", initial_sidebar_state="collapsed")

if not st.session_state.get("logged_in"):
    st.warning("🔒 Please log in from the Home page first.")
    st.stop()

st.title("🛍️ Cashier Billing System")
st.markdown("---")

if 'cart' not in st.session_state:
    st.session_state.cart = []

inventory = get_inventory()

# Two clean columns: 50% for product entry, 50% for the Bill
col_add, col_bill = st.columns([1, 1], gap="large")

with col_add:
    st.header("1. Add to Bill")
    if inventory:
        prod_map = {f"{p['pname']} (Stock: {p['qty']}) - ${p['price']}": p for p in inventory}
        
        with st.container(border=True):
            selected = st.selectbox("Search Product", options=list(prod_map.keys()))
            qty = st.number_input("Enter Quantity", min_value=1, step=1)
            
            if st.button("➕ Add Item", type="primary", use_container_width=True):
                p_data = prod_map[selected]
                if qty > p_data['qty']:
                    st.error(f"Not enough stock! Only {p_data['qty']} remaining.")
                else:
                    st.session_state.cart.append({
                        "pid": p_data['pid'], 
                        "pname": p_data['pname'], 
                        "qty": qty, 
                        "price": float(p_data['price']),
                        "subtotal": float(p_data['price']) * qty
                    })
                    st.success(f"Added {p_data['pname']} to the bill.")
    else:
        st.error("Cannot connect to inventory database.")

with col_bill:
    st.header("2. Customer Bill")
    with st.container(border=True):
        if st.session_state.cart:
            bill_df = pd.DataFrame(st.session_state.cart)
            st.dataframe(bill_df[['pname', 'qty', 'price', 'subtotal']], use_container_width=True, hide_index=True)
            
            total = sum(i['subtotal'] for i in st.session_state.cart)
            st.markdown(f"<h2 style='text-align: right; color: green;'>Total: ${total:.2f}</h2>", unsafe_allow_html=True)
            
            st.markdown("---")
            cust_id = st.number_input("Customer ID", min_value=1, step=1)
            
            col_pay, col_clear = st.columns(2)
            if col_pay.button("💳 Generate Bill & Pay", type="primary", use_container_width=True):
                payload = {
                    "cust_id": cust_id, 
                    "total_amt": total, 
                    "items": [{"pid": i['pid'], "qty": i['qty']} for i in st.session_state.cart]
                }
                res, status = place_order(payload)
                if status == 200:
                    st.success(f"Bill #{res.get('order_id')} generated successfully!")
                    st.session_state.cart = []
                    st.rerun()
                else:
                    st.error(res.get("detail", "Payment Failed"))
                    
            if col_clear.button("🗑️ Clear Bill", use_container_width=True):
                st.session_state.cart = []
                st.rerun()
        else:
            st.info("Bill is empty. Add items from the left.")