from flask import Flask

def create_app():
    app = Flask(__name__)
    import config
    app.secret_key = config.SECRET_KEY
    app.config["APP_URL"] = config.APP_URL

    from routes.auth import auth_bp
    from routes.pools import pools_bp
    from routes.scores import scores_bp
    from routes.draft import draft_bp
    from routes.auction import auction_bp
    from routes.roster import roster_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pools_bp)
    app.register_blueprint(scores_bp)
    app.register_blueprint(draft_bp)
    app.register_blueprint(auction_bp)
    app.register_blueprint(roster_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
