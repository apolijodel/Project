from flask import Flask
from blueprints.auth import auth_bp
from blueprints.city import city_bp
from blueprints.barangay import barangay_bp

app = Flask(__name__)
app.secret_key = "secret123"

# REGISTER BLUEPRINTS
app.register_blueprint(auth_bp)
app.register_blueprint(city_bp)
app.register_blueprint(barangay_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0")