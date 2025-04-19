from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
import bcrypt
from datetime import datetime, timedelta
import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import zipfile
import io

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["MONGO_URI"] = "mongodb://localhost:27017/billing_system"
app.permanent_session_lifetime = timedelta(minutes=30)

# MongoDB Setup
mongo = PyMongo(app)
users_collection = mongo.db.users
transactions_collection = mongo.db.transactions
config_collection = mongo.db.config
items_collection = mongo.db.items

# Directory for logo uploads
UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Initialize sample items in MongoDB if empty
if items_collection.count_documents({}) == 0:
    initial_items = [
        {"category": "Pizza", "name": "Margherita", "price": 8.99},
        {"category": "Pizza", "name": "Pepperoni", "price": 10.99},
        {"category": "Pizza", "name": "Veggie Supreme", "price": 9.99},
        {"category": "Pizza", "name": "BBQ Chicken", "price": 11.99}
    ]
    items_collection.insert_many(initial_items)


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = request.form["user_id"]
        password = request.form["password"]
        user = users_collection.find_one({"user_id": user_id})

        if user and bcrypt.checkpw(password.encode("utf-8"), user["password"]):
            session.permanent = True
            session["user_id"] = user_id
            # Initialize tables in session
            session["tables"] = {
                f"table_{i}": {"customers": [], "cart": []} for i in range(1, 6)
            }
            flash("Login successful! Welcome to the billing system.", "success")
            return redirect(url_for("catalog"))
        else:
            flash("Invalid ID or password. Try again.", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        user_id = request.form["user_id"]
        password = request.form["password"]
        if users_collection.find_one({"user_id": user_id}):
            flash("User ID already exists!", "danger")
        else:
            hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
            users_collection.insert_one({"user_id": user_id, "password": hashed_password})
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/config", methods=["GET", "POST"])
def config():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    config_data = config_collection.find_one({"user_id": user_id}) or {}

    if request.method == "POST":
        shop_name = request.form.get("shop_name", config_data.get("shop_name", ""))
        if not shop_name:
            flash("Shop name is required.", "danger")
            return redirect(url_for("config"))

        logo = request.files.get("logo")
        logo_path = config_data.get("logo_path", "")

        if logo and logo.filename:
            logo_filename = f"{user_id}_{logo.filename}"
            logo_path = os.path.join(app.config["UPLOAD_FOLDER"], logo_filename)
            logo.save(logo_path)
            logo_path = f"uploads/{logo_filename}"

        config_collection.update_one(
            {"user_id": user_id},
            {"$set": {"shop_name": shop_name, "logo_path": logo_path}},
            upsert=True
        )
        flash("Configuration saved successfully!", "success")
        return redirect(url_for("catalog"))

    return render_template("config.html", config=config_data)


@app.route("/catalog", methods=["GET", "POST"])
def catalog():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    config_data = config_collection.find_one({"user_id": user_id}) or {}
    tables = session.get("tables", {f"table_{i}": {"customers": [], "cart": []} for i in range(1, 6)})
    selected_table = session.get("selected_table", "table_1")

    try:
        items = items_collection.find({"category": "Pizza"})
        catalog = {"Pizza": [{"name": item["name"], "price": item["price"], "_id": str(item["_id"])} for item in items]}
    except Exception as e:
        flash("Error loading catalog. Please try again.", "danger")
        catalog = {"Pizza": []}

    if request.method == "POST":
        try:
            if "table_id" in request.form:
                table_id = request.form["table_id"]
                if table_id in tables:
                    session["selected_table"] = table_id
                    return jsonify({"message": f"Switched to {table_id.replace('table_', 'Table ')}"})
                return jsonify({"error": "Invalid table."}), 400
            elif "customers" in request.form:
                table_id = request.form["table_id"]
                if table_id not in tables:
                    return jsonify({"error": "Invalid table."}), 400
                customers = [name.strip() for name in request.form["customers"].split(",") if name.strip()]
                tables[table_id]["customers"] = customers
                session["tables"] = tables
                return jsonify({"message": f"Updated customers for {table_id.replace('table_', 'Table ')}"})
            else:
                table_id = session.get("selected_table", "table_1")
                item_name = request.form["item_name"]
                price = float(request.form["price"])
                quantity = int(request.form["quantity"])
                tables[table_id]["cart"].append({
                    "name": item_name,
                    "price": price,
                    "quantity": quantity,
                    "total": price * quantity
                })
                session["tables"] = tables
                return jsonify({"message": f"Added {item_name} to {table_id.replace('table_', 'Table ')}!",
                                "cart": tables[table_id]["cart"]})
        except KeyError as e:
            return jsonify({"error": f"Missing field: {str(e)}"}), 400

    return render_template("catalog.html", catalog=catalog, tables=tables, selected_table=selected_table,
                           config=config_data)


@app.route("/add_item", methods=["POST"])
def add_item():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    data = request.get_json()
    name = data.get("name")
    price = float(data.get("price"))
    category = data.get("category", "Pizza")

    if not name or price <= 0:
        return jsonify({"error": "Invalid item name or price."}), 400

    item_id = items_collection.insert_one({"category": category, "name": name, "price": price}).inserted_id
    return jsonify(
        {"message": f"Added {name} to catalog!", "item": {"_id": str(item_id), "name": name, "price": price}})


@app.route("/edit_item/<item_id>", methods=["POST"])
def edit_item(item_id):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    data = request.get_json()
    name = data.get("name")
    price = float(data.get("price"))

    if not name or price <= 0:
        return jsonify({"error": "Invalid item name or price."}), 400

    items_collection.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"name": name, "price": price}}
    )
    return jsonify({"message": f"Updated {name} in catalog!", "item": {"_id": item_id, "name": name, "price": price}})


@app.route("/delete_item", methods=["POST"])
def delete_item():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    try:
        table_id = request.form["table_id"]
        index = int(request.form["index"])
        tables = session.get("tables", {})
        if table_id not in tables:
            return jsonify({"error": "Invalid table."}), 400
        cart = tables[table_id]["cart"]
        if 0 <= index < len(cart):
            item = cart.pop(index)
            session["tables"] = tables
            return jsonify(
                {"message": f"Removed {item['name']} from {table_id.replace('table_', 'Table ')}.", "cart": cart})
        else:
            return jsonify({"error": "Invalid item index."}), 400
    except (KeyError, ValueError):
        return jsonify({"error": "Error removing item."}), 400


@app.route("/generate_bill", methods=["POST"])
def generate_bill():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    tables = session.get("tables", {})
    config_data = config_collection.find_one({"user_id": user_id}) or {}
    shop_name = config_data.get("shop_name", "Pizza Billing System")
    table_id = request.form.get("table_id")
    generate_all = request.form.get("generate_all") == "true"
    timestamp = datetime.now()

    if generate_all:
        # Generate PDFs for all tables with items
        pdf_files = []
        for tid, table_data in tables.items():
            if not table_data["cart"]:
                continue
            total = sum(item["total"] for item in table_data["cart"])
            customers = ", ".join(table_data["customers"]) or "Guests"
            transaction = {
                "user_id": user_id,
                "table_id": tid,
                "customers": table_data["customers"],
                "items": table_data["cart"],
                "total": total,
                "timestamp": timestamp
            }
            transactions_collection.insert_one(transaction)

            pdf_filename = f"bill_{user_id}_{tid}_{timestamp.strftime('%Y%m%d_%H%M%S')}.pdf"
            c = canvas.Canvas(pdf_filename, pagesize=letter)
            c.drawString(100, 780, f"{shop_name}")
            c.drawString(100, 760, f"Table: {tid.replace('table_', '')}")
            c.drawString(100, 740, f"Bill for: {customers}")
            c.drawString(100, 720, f"Date: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
            y = 690
            for item in table_data["cart"]:
                c.drawString(100, y, f"{item['name']} x {item['quantity']}: ₹{item['total']:.2f}")
                y -= 20
            c.drawString(100, y, f"Grand Total: ₹{total:.2f}")
            y -= 20
            c.drawString(100, y, "Come back soon for more pizza perfection!")
            c.save()
            pdf_files.append((tid, pdf_filename))

        if not pdf_files:
            flash("No tables have items to bill!", "danger")
            return redirect(url_for("catalog"))

        # Create a zip file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for tid, pdf_filename in pdf_files:
                zip_file.write(pdf_filename, f"{tid}_bill.pdf")
                os.remove(pdf_filename)  # Clean up
        zip_buffer.seek(0)

        # Clear all carts
        for tid in tables:
            tables[tid]["cart"] = []
            tables[tid]["customers"] = []
        session["tables"] = tables
        flash("All bills generated successfully!", "success")
        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"bills_{timestamp.strftime('%Y%m%d_%H%M%S')}.zip"
        )
    else:
        # Generate bill for a single table
        if table_id not in tables or not tables[table_id]["cart"]:
            flash("Selected table is empty!", "danger")
            return redirect(url_for("catalog"))

        total = sum(item["total"] for item in tables[table_id]["cart"])
        customers = ", ".join(tables[table_id]["customers"]) or "Guests"
        transaction = {
            "user_id": user_id,
            "table_id": table_id,
            "customers": tables[table_id]["customers"],
            "items": tables[table_id]["cart"],
            "total": total,
            "timestamp": timestamp
        }
        transactions_collection.insert_one(transaction)

        pdf_filename = f"bill_{user_id}_{table_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}.pdf"
        c = canvas.Canvas(pdf_filename, pagesize=letter)
        c.drawString(100, 780, f"{shop_name}")
        c.drawString(100, 760, f"Table: {table_id.replace('table_', '')}")
        c.drawString(100, 740, f"Bill for: {customers}")
        c.drawString(100, 720, f"Date: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        y = 690
        for item in tables[table_id]["cart"]:
            c.drawString(100, y, f"{item['name']} x {item['quantity']}: ₹{item['total']:.2f}")
            y -= 20
        c.drawString(100, y, f"Grand Total: ₹{total:.2f}")
        y -= 20
        c.drawString(100, y, "Come back soon for more pizza perfection!")
        c.save()

        tables[table_id]["cart"] = []
        tables[table_id]["customers"] = []
        session["tables"] = tables
        flash("Bill generated successfully!", "success")
        return send_file(pdf_filename, as_attachment=True)


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


if __name__ == "__main__":
    print("Starting Flask App...")
    app.run(debug=True)