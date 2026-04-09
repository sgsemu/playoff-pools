from flask import Flask

def create_app():
    app = Flask(__name__)
    app.secret_key = __import__("config").SECRET_KEY

    from routes.auth import auth_bp
    # from routes.pools import pools_bp   # Task 19
    # from routes.scores import scores_bp # Task 19

    app.register_blueprint(auth_bp)
    # app.register_blueprint(pools_bp)    # Task 19
    # app.register_blueprint(scores_bp)   # Task 19

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
