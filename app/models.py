import json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_premium = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'


class ExtractionTask(db.Model):
    __tablename__ = 'extraction_tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(255), nullable=False)
    location = db.Column(db.String(255), nullable=False)
    source = db.Column(db.String(50), nullable=False)  # 'google_maps' or 'linkedin'
    radius = db.Column(db.Integer, default=5000)  # in meters
    max_results = db.Column(db.Integer, default=50)
    selected_fields = db.Column(db.Text, default=None)  # JSON list of fields to extract/export
    list_type = db.Column(db.String(100), default=None)  # US list type for targeted extraction
    status = db.Column(db.String(50), default='pending')  # pending, running, completed, failed, stopped
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    total_records = db.Column(db.Integer, default=0)
    
    # Relationship
    businesses = db.relationship('Business', backref='task', lazy=True, cascade='all, delete-orphan')

class Business(db.Model):
    __tablename__ = 'businesses'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('extraction_tasks.id'), nullable=False)
    
    # Core business information
    name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    website = db.Column(db.String(500))
    location = db.Column(db.Text)
    
    # AI-enriched fields
    social_links = db.Column(db.Text)        # JSON: {"linkedin": "...", "twitter": "..."}
    confidence_score = db.Column(db.Float)    # AI extraction confidence 0.0-1.0
    
    # Extended business fields
    owner = db.Column(db.String(255))         # Owner / Founder name

    # Government/nonprofit directory enrichment
    organization_type = db.Column(db.String(50))
    parent_organization = db.Column(db.String(200))
    division = db.Column(db.String(200))
    source_url = db.Column(db.String(500))
    extraction_method = db.Column(db.String(50))
    
    # Email validation fields
    llm_validity_score = db.Column(db.Float)          # LLM's email deliverability confidence 0.0-1.0
    email_type = db.Column(db.String(50))              # personal, role_based, generic, obfuscated, unknown
    mx_valid = db.Column(db.Boolean)                   # MX record exists for email domain
    disposable_domain = db.Column(db.Boolean)           # Domain is a known throwaway
    heuristic_score = db.Column(db.Float)               # Syntactic heuristic score
    final_confidence = db.Column(db.Float)              # Weighted combined email quality score
    verification_status = db.Column(db.String(20))      # verified, likely_valid, unverified, invalid
    
    # Metadata
    source = db.Column(db.String(50))  # google_maps, linkedin, ai_extract, manual, yelp, list_crawl
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        social = None
        if self.social_links:
            try:
                social = json.loads(self.social_links) if isinstance(self.social_links, str) else self.social_links
            except (json.JSONDecodeError, TypeError):
                social = None

        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'website': self.website,
            'location': self.location,
            'social_links': social,
            'confidence_score': self.confidence_score,
            'owner': self.owner,
            'organization_type': self.organization_type,
            'parent_organization': self.parent_organization,
            'division': self.division,
            'source_url': self.source_url,
            'extraction_method': self.extraction_method,
            'source': self.source,
            'llm_validity_score': self.llm_validity_score,
            'email_type': self.email_type,
            'mx_valid': self.mx_valid,
            'disposable_domain': self.disposable_domain,
            'heuristic_score': self.heuristic_score,
            'final_confidence': self.final_confidence,
            'verification_status': self.verification_status,
        }


class BusinessEmbedding(db.Model):
    """Stores vector embeddings for semantic search."""
    __tablename__ = 'business_embeddings'

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), unique=True, nullable=False)
    embedding = db.Column(db.Text, nullable=False)  # JSON array of floats
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    business = db.relationship(
        'Business',
        backref=db.backref('embedding_record', uselist=False, cascade='all, delete-orphan'),
        single_parent=True,
    )

