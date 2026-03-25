import threading
import re
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from datetime import datetime
from sqlalchemy import or_

from app.models import Business, ExtractionTask, db
from app.utils.validators import validate_email, validate_phone, validate_url

class BaseExtractor(ABC):
    DEFAULT_SELECTED_FIELDS = {
        'name',
        'email',
        'phone',
        'website',
        'location',
        'owner',
    }
    ALWAYS_PRESERVED_FIELDS = {
        'source',
        'social_links',
        'confidence_score',
        'llm_validity_score',
        'email_type',
        'mx_valid',
        'disposable_domain',
        'heuristic_score',
        'final_confidence',
        'verification_status',
        'organization_type',
        'parent_organization',
        'division',
        'source_url',
        'extraction_method',
    }
    FOREIGN_LOCATION_HINTS = {
        'india', 'west bengal', 'kolkata', 'bangladesh', 'pakistan',
        'nepal', 'sri lanka', 'nigeria', 'kenya', 'philippines',
        'indonesia', 'china', 'vietnam', 'thailand', 'ukraine',
        'russia', 'brazil', 'mexico', 'uae', 'dubai', 'saudi arabia',
    }
    NON_US_TLDS = {
        '.in', '.bd', '.pk', '.lk', '.ng', '.ke', '.ph', '.id',
        '.cn', '.vn', '.th', '.ru', '.br', '.mx', '.ae', '.sa',
    }

    def __init__(self, task_id, stop_event: threading.Event = None, ai_options=None):
        self.task_id = task_id
        self.task = ExtractionTask.query.get(task_id)
        self._saved_count = 0
        self._initial_count = Business.query.filter_by(task_id=task_id).count()
        self.stop_event = stop_event
        self.ai_options = ai_options or {}
        self.selected_fields = self._normalize_selected_fields(self.ai_options.get('selected_fields'))

    

    @property
    def should_stop(self):
        """Check if the extraction has been cancelled."""
        if self.stop_event and self.stop_event.is_set():
            return True
        return False

    @abstractmethod
    def extract(self):
        """Main extraction method to be implemented by child classes"""
        pass

    def get_ai_options(self):
        """Return normalized AI overrides for downstream extractors."""
        return {
            'provider': self.ai_options.get('provider'),
            'primary_model': self.ai_options.get('primary_model'),
            'fallback_model': self.ai_options.get('fallback_model'),
            'selected_fields': sorted(self.selected_fields),
        }

    def _normalize_selected_fields(self, raw_fields):
        """Normalize field selection passed from request options."""
        if isinstance(raw_fields, str):
            values = [v.strip().lower() for v in raw_fields.split(',') if v and v.strip()]
        elif isinstance(raw_fields, (list, tuple, set)):
            values = [str(v).strip().lower() for v in raw_fields if str(v).strip()]
        else:
            values = []

        normalized = {
            value for value in values if value in self.DEFAULT_SELECTED_FIELDS
        }
        return normalized or set(self.DEFAULT_SELECTED_FIELDS)

    def wants_field(self, field_name):
        return field_name in self.selected_fields

    def _apply_field_selection(self, business_data):
        """Keep only fields requested by the user, plus required metadata."""
        if not business_data:
            return business_data

        allowed = set(self.selected_fields) | set(self.ALWAYS_PRESERVED_FIELDS)
        return {k: v for k, v in business_data.items() if k in allowed}

    def _has_any_selected_value(self, business_data):
        """Return True if at least one requested field has a non-empty value."""
        for field in self.selected_fields:
            value = business_data.get(field)
            if value not in (None, '', [], {}):
                return True
        return False

    def validate_business_data(self, business_data):
        """Clean and validate business data before saving."""
        # Validate Email
        if business_data.get('email'):
            email = str(business_data['email']).strip().lower()
            if not validate_email(email):
                business_data['email'] = None
            else:
                business_data['email'] = email
        
        # Basic Phone validation stripping
        if business_data.get('phone'):
            phone = str(business_data['phone']).strip()
            # Just verify it has some digits
            if not any(c.isdigit() for c in phone):
                business_data['phone'] = None
            else:
                business_data['phone'] = phone[:50]
                
        # Validate URLs
        for url_field in ['website']:
            if business_data.get(url_field):
                url = str(business_data[url_field]).strip()
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                if not validate_url(url):
                    business_data[url_field] = None
                else:
                    business_data[url_field] = url[:500]
                    
        return business_data

    def save_business(self, business_data):
        """Save extracted business data to database with deduplication"""
        import json
        
        try:
            # Clean and validate data first
            business_data = self.validate_business_data(business_data)

            # Enforce basic geo relevance against the requested task location.
            if not self._is_location_relevant(business_data):
                return None

            business_data = self._apply_field_selection(business_data)
            if not self._has_any_selected_value(business_data):
                return None

            # Deduplicate by any core identifier we have
            filters = []
            if business_data.get('email'):
                filters.append(Business.email == business_data['email'])
            if business_data.get('website'):
                filters.append(Business.website == business_data['website'])
            if business_data.get('phone'):
                filters.append(Business.phone == business_data['phone'])
            if business_data.get('location') and business_data.get('name'):
                # Name + Location is a good deduplicator
                filters.append((Business.location == business_data['location']) & (Business.name == business_data['name']))

                

            existing = None
            if filters:
                existing = Business.query.filter(
                    Business.task_id == self.task_id,
                    or_(*filters),
                ).first()

            # Handle social_links JSON serialization
            social_links_json = None
            if business_data.get('social_links'):
                social_links_json = json.dumps(business_data['social_links'])

            if existing:
                business_data['social_links'] = social_links_json
                return self._update_business(existing, business_data)

            business = Business(
                task_id=self.task_id,
                name=business_data.get('name'),
                email=business_data.get('email'),
                phone=business_data.get('phone'),
                website=business_data.get('website'),
                location=business_data.get('location'),
                social_links=social_links_json,
                confidence_score=business_data.get('confidence_score'),
                owner=business_data.get('owner'),
                source=business_data.get('source', self.task.source),
                llm_validity_score=business_data.get('llm_validity_score'),
                email_type=business_data.get('email_type'),
                mx_valid=business_data.get('mx_valid'),
                disposable_domain=business_data.get('disposable_domain'),
                heuristic_score=business_data.get('heuristic_score'),
                final_confidence=business_data.get('final_confidence'),
                verification_status=business_data.get('verification_status'),
                organization_type=business_data.get('organization_type'),
                parent_organization=business_data.get('parent_organization'),
                division=business_data.get('division'),
                source_url=business_data.get('source_url'),
                extraction_method=business_data.get('extraction_method'),
            )
            db.session.add(business)

            self._saved_count += 1
            # Keep totals additive when multiple extractors run on one task.
            self.task.total_records = self._initial_count + self._saved_count
            db.session.commit()

            return business
        except Exception as e:
            db.session.rollback()
            print(f"Error saving business: {e}")
            return None

    def _is_location_relevant(self, business_data):
        """Lightweight guard to reduce obvious cross-country mismatches."""
        if not self.task:
            return True

        task_location = str(self.task.location or '').strip().lower()
        result_location = str(business_data.get('location') or '').strip().lower()
        result_source = str(business_data.get('source') or '').strip().lower()
        website = str(business_data.get('website') or '').strip().lower()

        # If extractor could not infer a location, don't block the record.
        if not task_location or not result_location:
            wants_florida = ('florida' in task_location) or bool(re.search(r'\bfl\b', task_location))
            wants_us = wants_florida or ('united states' in task_location) or ('usa' in task_location) or bool(re.search(r'\bus\b', task_location))

            # For strict geo tasks, require explicit location for noisy sources.
            if wants_us and result_source in {'linkedin'}:
                return False

            # Block obvious non-US websites when task is US-centric.
            if wants_us and website:
                try:
                    host = urlparse(website).netloc.lower()
                    if any(host.endswith(tld) for tld in self.NON_US_TLDS):
                        return False
                except Exception:
                    pass

            return True

        wants_florida = ('florida' in task_location) or bool(re.search(r'\bfl\b', task_location))
        wants_us = wants_florida or ('united states' in task_location) or ('usa' in task_location) or bool(re.search(r'\bus\b', task_location))

        if wants_florida:
            has_florida = ('florida' in result_location) or bool(re.search(r'\bfl\b', result_location))
            if not has_florida:
                return False

        if wants_us and any(term in result_location for term in self.FOREIGN_LOCATION_HINTS):
            return False

        return True

    def _update_business(self, existing, new_data):
        """Update an existing business record with new non-null data"""
        updated = False
        mergeable_fields = [
            'name', 'email', 'phone', 'website', 'location',
            'social_links', 'confidence_score', 'owner',
            'llm_validity_score', 'email_type', 'mx_valid',
            'disposable_domain', 'heuristic_score', 'final_confidence',
            'verification_status', 'organization_type',
            'parent_organization', 'division', 'source_url',
            'extraction_method',
        ]
        for field in mergeable_fields:
            new_val = new_data.get(field)
            if new_val is not None:
                current_val = getattr(existing, field)
                # Overwrite if empty, OR if confidence_score is better
                if current_val in [None, '']:
                    setattr(existing, field, new_val)
                    updated = True
                elif field == 'confidence_score' and new_val > float(current_val or 0):
                    setattr(existing, field, new_val)
                    updated = True
                # If confidence improved, prefer stronger name/social evidence.
                elif new_data.get('confidence_score', 0) > (existing.confidence_score or 0):
                    if field in ['social_links', 'name']:
                        setattr(existing, field, new_val)
                        updated = True

        if updated:
            db.session.commit()
        return existing

    def update_task_status(self, status, total_records=None):
        """Update extraction task status"""
        self.task.status = status
        if status == 'completed':
            self.task.completed_at = datetime.utcnow()

        actual_count = None
        if total_records is not None or status in ('completed', 'stopped'):
            actual_count = Business.query.filter_by(task_id=self.task_id).count()

        if total_records is not None:
            if actual_count is None:
                self.task.total_records = total_records
            else:
                self.task.total_records = max(int(total_records), int(actual_count))
        elif actual_count is not None:
            self.task.total_records = int(actual_count)

        db.session.commit()
