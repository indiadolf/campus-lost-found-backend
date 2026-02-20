from flask import Flask, jsonify, request
import firebase_admin
from firebase_admin import credentials, firestore
from flask_cors import CORS
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta


ADMINS = ["f20250692@pilani.bits-pilani.ac.in"]
MAX_IMAGE_MB = 5


cloudinary.config(
    cloud_name="dkeemuyc5",
    api_key="618542112391133",
    api_secret="ayZ_faPMSKAMgnp9N01FN7b0sBs",
    secure=True
)


cred = credentials.Certificate("firebase_key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()


app = Flask(__name__)
CORS(app)
print("SERVER RUNNING")

def too_soon_to_post(email):
    docs = db.collection("items").where("postedBy", "==", email).stream()
    latest = None

    for d in docs:
        ts = d.to_dict().get("createdAtEpoch")
        if ts and (latest is None or ts > latest):
            latest = ts

    if not latest:
        return False

    return datetime.utcnow() - datetime.utcfromtimestamp(latest / 1000) < timedelta(minutes=1)


def create_notification(to_email, item_id, item_title):
    db.collection("notifications").add({
        "to": to_email,
        "itemId": item_id,
        "itemTitle": item_title,
        "message": "Someone replied to your post",
        "read": False,
        "createdAtEpoch": int(datetime.utcnow().timestamp())
    })

@app.route("/items", methods=["GET"])
def get_items():
    docs = db.collection("items").where("status", "==", "active").stream()
    return jsonify([{**d.to_dict(), "id": d.id} for d in docs])


@app.route("/items/returned", methods=["GET"])
def get_returned_items():
    docs = db.collection("items").where("status", "==", "returned").stream()
    return jsonify([{**d.to_dict(), "id": d.id} for d in docs])


@app.route("/items", methods=["POST"])
def add_item():
    data = request.json or {}

    title = data.get("title", "").strip()
    desc = data.get("location", "").strip()
    email = data.get("postedBy")

    if not title or not desc or not email:
        return {"error": "Missing fields"}, 400

    if too_soon_to_post(email):
        return {"error": "Wait 1 minute before posting again"}, 429

    db.collection("items").add({
        "itemType": title,
        "description": desc,
        "imageUrl": data.get("imageUrl", ""),
        "postedBy": email,
        "status": "active",
        "createdAtEpoch": int(datetime.utcnow().timestamp() * 1000)
    })

    return {"status": "added"}, 201


@app.route("/items/<item_id>/returned", methods=["POST"])
def mark_returned(item_id):
    user = (request.json or {}).get("userEmail")
    ref = db.collection("items").document(item_id)
    doc = ref.get()

    if not doc.exists:
        return {"error": "Not found"}, 404

    if doc.to_dict()["postedBy"] != user:
        return {"error": "Forbidden"}, 403

    ref.update({"status": "returned"})
    return {"status": "ok"}, 200


@app.route("/items/<item_id>", methods=["DELETE"])
def delete_item(item_id):
    user = (request.json or {}).get("userEmail")
    if user not in ADMINS:
        return {"error": "Admin only"}, 403

    db.collection("items").document(item_id).delete()
    return {"status": "deleted"}, 200

@app.route("/items/<item_id>/replies", methods=["POST"])
def add_reply(item_id):
    data = request.json or {}
    msg = data.get("message", "").strip()
    replier = data.get("repliedBy")

    if not msg or not replier:
        return {"error": "Missing fields"}, 400

    item_ref = db.collection("items").document(item_id)
    item_doc = item_ref.get()

    if not item_doc.exists:
        return {"error": "Item not found"}, 404

    item = item_doc.to_dict()
    owner = item.get("postedBy")

    item_ref.collection("replies").add({
        "message": msg,
        "repliedBy": replier,
        "createdAtEpoch": int(datetime.utcnow().timestamp())
    })

    if owner and owner != replier:
        create_notification(owner, item_id, item.get("itemType", "item"))

    return {"status": "reply added"}, 201


@app.route("/items/<item_id>/replies", methods=["GET"])
def get_replies(item_id):
    docs = (
        db.collection("items")
        .document(item_id)
        .collection("replies")
        .order_by("createdAtEpoch")
        .stream()
    )
    return jsonify([{**d.to_dict(), "id": d.id} for d in docs])


@app.route("/upload", methods=["POST"])
def upload_image():
    if "image" not in request.files:
        return {"error": "No image"}, 400

    img = request.files["image"]
    img.seek(0, 2)

    if img.tell() / (1024 * 1024) > MAX_IMAGE_MB:
        return {"error": "Image too large"}, 400

    img.seek(0)
    result = cloudinary.uploader.upload(img, folder="campus-lost-found")
    return {"imageUrl": result["secure_url"]}, 201


@app.route("/notifications", methods=["GET"])
def get_notifications():
    email = request.args.get("email")
    if not email:
        return {"error": "Email required"}, 400

    docs = (
        db.collection("notifications")
        .where("to", "==", email)
        .order_by("createdAtEpoch", direction=firestore.Query.DESCENDING)
        .limit(20)
        .stream()
    )

    return jsonify([{**d.to_dict(), "id": d.id} for d in docs])


@app.route("/notifications/<notif_id>/read", methods=["POST"])
def mark_notification_read(notif_id):
    db.collection("notifications").document(notif_id).update({"read": True})
    return {"status": "ok"}, 200


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port)