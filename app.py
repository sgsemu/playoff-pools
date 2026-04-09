from flask import Flask

def create_app():
    app = Flask(__name__)
    app.secret_key = __import__("config").SECRET_KEY

    from routes.auth import auth_bp
    from routes.pools import pools_bp
    from routes.scores import scores_bp
    from routes.draft import draft_bp
    from routes.auction import auction_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pools_bp)
    app.register_blueprint(scores_bp)
    app.register_blueprint(draft_bp)
    app.register_blueprint(auction_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
