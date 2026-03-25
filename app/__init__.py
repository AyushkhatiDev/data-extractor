from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from flask_login import LoginManager, current_user
from flask import request, jsonify
from config import config
from app.utils.demo_access import is_demo_locked_user, DEMO_LOCK_MESSAGE

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    CORS(app)

    # User loader for flask-login
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import request, jsonify, redirect, url_for
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('auth.login', next=request.url))

    @app.before_request
    def enforce_demo_one_time_limit():
        """Only block NEW extraction requests for demo-locked users.

        Locked users can still browse pages, view past results, and export.
        """
        if not current_user.is_authenticated:
            return None

        if not is_demo_locked_user(current_user):
            return None

        # Only block starting new extractions.
        if request.path == '/api/extraction/start' and request.method == 'POST':
            return jsonify({'error': DEMO_LOCK_MESSAGE, 'code': 'demo_locked'}), 403

        return None
    
    # Register blueprints
    from app.routes.main import main_bp
    from app.routes.extraction import extraction_bp
    from app.routes.export import export_bp
    from app.routes.auth import auth_bp
    from app.routes.ai_extraction import ai_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(extraction_bp, url_prefix='/api/extraction')
    app.register_blueprint(export_bp, url_prefix='/api/export')
    app.register_blueprint(ai_bp, url_prefix='/api/ai')
    
    # Create database tables
    with app.app_context():
        db.create_all()
    
    return app
