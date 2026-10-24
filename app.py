import os
import requests
import uuid
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from models import db, User, SusuGroup, GroupMember, Transaction

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default_dev_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///susu.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app)
db.init_app(app)

# Initialize Database tables
with app.app_context():
    db.create_all()

# --- HELPER FUNCTIONS ---

def send_sms_notification(phone_number, message):
    """Sends SMS using Arkesel / Hubtel SMS Gateway API"""
    api_key = os.getenv('ARKESEL_API_KEY')
    if not api_key or api_key == 'your_arkesel_sms_api_key':
        print(f"[SMS MOCK] To {phone_number}: {message}")
        return
    
    url = "https://sms.arkesel.com/api/v2/sms/send"
    headers = {"api-key": api_key}
    payload = {
        "sender": "SusuPay",
        "recipients": [phone_number],
        "message": message
    }
    try:
        requests.post(url, json=payload, headers=headers, timeout=5)
    except Exception as e:
        print(f"SMS Error: {e}")

# --- ROUTES ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/users/register', methods=['POST'])
def register_user():
    data = request.json
    if User.query.filter_by(phone_number=data['phone_number']).first():
        return jsonify({"error": "Phone number already registered"}), 400
    
    user = User(
        full_name=data['full_name'],
        phone_number=data['phone_number'],
        role=data.get('role', 'member')
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "User registered successfully", "user_id": user.id}), 201

@app.route('/api/groups/create', methods=['POST'])
def create_group():
    data = request.json
    group = SusuGroup(
        name=data['name'],
        contribution_amount=data['contribution_amount'],
        cycle_type=data.get('cycle_type', 'daily')
    )
    db.session.add(group)
    db.session.commit()
    return jsonify({"message": "Susu group created", "group_id": group.id}), 201

@app.route('/api/deposit/initialize', methods=['POST'])
def initialize_deposit():
    data = request.json
    user = User.query.get(data['user_id'])
    if not user:
        return jsonify({"error": "User not found"}), 404

    ref = f"SUSU-{uuid.uuid4().hex[:8].upper()}"
    amount = float(data['amount'])
    
    # Save pending transaction to local ledger
    txn = Transaction(
        user_id=user.id,
        group_id=data.get('group_id'),
        amount=amount,
        transaction_type='deposit',
        status='pending',
        reference=ref
    )
    db.session.add(txn)
    db.session.commit()

    # Paystack MoMo integration step
    paystack_key = os.getenv('PAYSTACK_SECRET_KEY')
    if paystack_key and paystack_key != 'sk_test_your_paystack_secret_key':
        paystack_url = "https://api.paystack.co/transaction/initialize"
        headers = {"Authorization": f"Bearer {paystack_key}"}
        paystack_data = {
            "amount": int(amount * 100), # Paystack accepts pesewas
            "email": f"{user.phone_number}@susupay.local",
            "reference": ref,
            "currency": "GHS"
        }
        res = requests.post(paystack_url, json=paystack_data, headers=headers).json()
        return jsonify({"status": "success", "paystack": res['data'], "reference": ref})

    # Dev/Testing fallback: Direct confirmation when API keys are not added yet
    txn.status = 'success'
    db.session.commit()
    send_sms_notification(user.phone_number, f"SusuPay: Received GHS {amount:.2f}. Ref: {ref}")
    return jsonify({"status": "success", "message": "Deposit recorded successfully", "reference": ref})

@app.route('/api/ledger/<int:user_id>', methods=['GET'])
def get_user_ledger(user_id):
    txns = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.timestamp.desc()).all()
    output = []
    total_savings = 0.0
    for t in txns:
        if t.status == 'success':
            total_savings += t.amount
        output.append({
            "reference": t.reference,
            "amount": t.amount,
            "type": t.transaction_type,
            "status": t.status,
            "date": t.timestamp.strftime("%Y-%m-%d %H:%M")
        })
    return jsonify({"total_savings": total_savings, "transactions": output})

if __name__ == '__main__':
    app.run(debug=True, port=5000)


