from flask import Blueprint, render_template, send_from_directory
from flask_login import login_required
import os

main_bp = Blueprint('main', __name__)

@main_bp.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(main_bp.root_path, '..', 'static', 'img'),
        'favicon.ico', mimetype='image/vnd.microsoft.icon'
    )

@main_bp.route('/')
@login_required
def index():
    from app.extraction.us_list_types import get_list_type_names
    return render_template('index.html', list_types=get_list_type_names())

@main_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@main_bp.route('/results')
@main_bp.route('/results/<int:task_id>')
@login_required
def results(task_id=None):
    return render_template('results.html')
