from flask import Blueprint, render_template
from app.auth_utils import admin_required

users_bp = Blueprint('users', __name__)


@users_bp.route('/users')
@admin_required
def user_management():
    return render_template('users/index.html')
