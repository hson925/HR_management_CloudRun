from flask import Blueprint, render_template, session, redirect

retired_bp = Blueprint('retired', __name__)


@retired_bp.route('/retired')
def retired_home():
    if not session.get('admin_auth'):
        return redirect('/login')
    if session.get('admin_code') not in ('retired', '퇴사'):
        return redirect('/')
    return render_template('retired/index.html')
