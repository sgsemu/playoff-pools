from flask import Blueprint, render_template
from routes.auth import login_required

scores_bp = Blueprint("scores", __name__)


@scores_bp.route("/pool/<pool_id>/scores")
@login_required
def game_scores(pool_id):
    return render_template("pool/scores.html", pool_id=pool_id, games=[])
