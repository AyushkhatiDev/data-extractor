import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required
from app.models import ExtractionTask, Business, BusinessEmbedding
from app import db
from datetime import datetime
import threading
import traceback
from sqlalchemy import func
from app.extraction.task_manager import task_manager, build_list_extractor
from app.extraction.us_list_types import get_list_type_config
from app.extraction.email_scraper import EmailScraper
from app.utils.validators import validate_email

extraction_bp = Blueprint('extraction', __name__)


ALLOWED_SELECTED_FIELDS = {
    'name',
    'email',
    'phone',
    'website',
    'location',
    'owner',
}
DEFAULT_SELECTED_FIELDS = [
    'name', 'email', 'phone', 'website', 'location', 'owner',
]


def _normalize_selected_fields(raw_selected_fields):
    """Normalize and validate user-selected fields."""
    values = []
    if isinstance(raw_selected_fields, str):
        values = [v.strip().lower() for v in raw_selected_fields.split(',') if v and v.strip()]
    elif isinstance(raw_selected_fields, (list, tuple, set)):
        values = [str(v).strip().lower() for v in raw_selected_fields if str(v).strip()]

    normalized = []
    for value in values:
        if value in ALLOWED_SELECTED_FIELDS and value not in normalized:
            normalized.append(value)

    return normalized or DEFAULT_SELECTED_FIELDS.copy()


def _task_selected_fields(task):
    """Read selected fields from task row with sane defaults."""
    if not task.selected_fields:
        return DEFAULT_SELECTED_FIELDS.copy()
    try:
        parsed = json.loads(task.selected_fields)
    except (TypeError, json.JSONDecodeError):
        return DEFAULT_SELECTED_FIELDS.copy()
    return _normalize_selected_fields(parsed)


def _count_businesses_for_tasks(task_ids):
    """Return {task_id: business_count} for the provided task ids."""
    if not task_ids:
        return {}

    rows = (
        db.session.query(Business.task_id, func.count(Business.id))
        .filter(Business.task_id.in_(task_ids))
        .group_by(Business.task_id)
        .all()
    )
    return {int(task_id): int(count) for task_id, count in rows}


@extraction_bp.route('/start', methods=['POST'])
@login_required
def start_extraction():
    data = request.form or request.json or {}
    keyword = data.get('keyword', '').strip()
    location = data.get('location', '').strip()
    source = data.get('source', '').strip()
    try:
        radius = int(data.get('radius', 5000))
    except (TypeError, ValueError):
        radius = 5000
    radius = max(100, min(radius, 50000))

    try:
        max_results = int(data.get('max_results', 50))
    except (TypeError, ValueError):
        max_results = 50
    max_results = max(1, min(max_results, 500))

    selected_fields = _normalize_selected_fields(data.get('selected_fields'))
    enable_validation = data.get('enable_validation', True)
    # Accept string 'false' from form submissions
    if isinstance(enable_validation, str):
        enable_validation = enable_validation.lower() not in ('false', '0', 'no', '')

    # AI options are backend-managed by default; API callers may still override.
    requested_provider = (data.get('ai_provider') or '').strip().lower()
    requested_model = (data.get('ai_model') or '').strip()
    requested_fallback_model = (data.get('ai_fallback_model') or '').strip()

    configured_provider = (current_app.config.get('AI_LLM_PROVIDER') or 'auto').strip().lower()
    allowed_providers = {'auto', 'langextract', 'openai_compatible', 'onprem', 'regex'}
    ai_provider = requested_provider or configured_provider
    if ai_provider not in allowed_providers:
        ai_provider = configured_provider if configured_provider in allowed_providers else 'auto'

    ai_model = requested_model or (current_app.config.get('AI_PRIMARY_MODEL') or None)
    ai_fallback_model = requested_fallback_model or (current_app.config.get('AI_FALLBACK_MODEL') or None)

    # Try to resolve keyword to a list type (for both list_crawl and regular sources)
    from app.extraction.us_list_types import resolve_list_type
    list_type = None
    auto_detected_list_type = False
    
    # For explicit list_crawl source, keyword MUST be a valid list type
    if source == 'list_crawl':
        if not keyword:
            return jsonify({'error': 'For list-based crawl, please specify the target list type (e.g., "NC Area Agencies on Aging" or "NC aging")'}), 400
        list_type = resolve_list_type(keyword)
        if not list_type:
            from app.extraction.us_list_types import get_list_type_names
            available = ', '.join(get_list_type_names())
            return jsonify({'error': f'Unknown list type: {keyword}. Available types: {available}'}), 400
        location = location or 'US'
    else:
        # For regular sources (google_maps, yelp, both, etc.), try to auto-detect list type
        # If keyword matches a known list type, we'll run BOTH the list extractor AND the regular source
        potential_list_type = resolve_list_type(keyword)
        if potential_list_type:
            list_type = potential_list_type
            auto_detected_list_type = True
    
    if not keyword or not location or not source:
        return jsonify({'error': 'keyword, location and source are required'}), 400

    task = ExtractionTask(
        keyword=keyword,
        location=location,
        source=source,
        radius=radius,
        max_results=max_results,
        selected_fields=json.dumps(selected_fields),
        list_type=list_type,
        status='pending'
    )
    db.session.add(task)
    db.session.commit()

    # Launch extraction in background thread
    ai_options = {
        'provider': ai_provider,
        'primary_model': ai_model,
        'fallback_model': ai_fallback_model,
        'selected_fields': selected_fields,
        'enable_validation': enable_validation,
        'list_type': list_type,
        'auto_detected_list_type': auto_detected_list_type,  # Flag: list type was auto-detected
    }

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=run_extraction_in_background,
        args=(app, task.id, source, ai_options)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'message': 'Task created',
        'task_id': task.id,
        'status': 'success',
        'task_status': task.status,
        'max_results': task.max_results,
        'selected_fields': selected_fields,
        'list_type': list_type,
        'enable_validation': bool(enable_validation),
    }), 201


def run_extraction_in_background(app, task_id, source, ai_options=None):
    """Run extraction in a background thread with Flask app context."""
    ai_options = ai_options or {}
    stop_event = task_manager.register_task(task_id)
    with app.app_context():
        try:
            # If a list type was auto-detected (keyword matched a list type like "Nursing Homes"),
            # run the list extractor FIRST, then run the regular source extractor afterward
            list_type = ai_options.get('list_type')
            auto_detected = ai_options.get('auto_detected_list_type', False)
            original_source = source
            
            if auto_detected and list_type and source != 'list_crawl':
                # Run list extractor first
                print(f"[AutoDetect] Detected list type '{list_type}' in keyword. Running list extraction first.")
                extractor = build_list_extractor(
                    task_id=task_id,
                    list_type=list_type,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
                
                # If stopped or extraction failed, skip regular source
                if stop_event.is_set():
                    raise ValueError('Extraction stopped by user')
                
                task = ExtractionTask.query.get(task_id)
                if task and task.status == 'failed':
                    raise ValueError('List extraction failed')
            
            # Now run the regular source extraction (for non-list_crawl sources)
            # or skip it if source was originally list_crawl
            if original_source == 'list_crawl':
                # Already handled above or this is the explicit list_crawl path
                list_type_name = ai_options.get('list_type', '')
                if list_type_name and not auto_detected:
                    # Original source was list_crawl and no auto-detect happened
                    extractor = build_list_extractor(
                        task_id=task_id,
                        list_type=list_type_name,
                        stop_event=stop_event,
                        ai_options=ai_options,
                    )
                    extractor.extract()
                else:
                    raise ValueError('list_type is required for list_crawl source')
            elif original_source == 'google_maps':
                from app.extraction.google_maps import GoogleMapsExtractor
                extractor = GoogleMapsExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
            elif original_source == 'truelancer':
                from app.extraction.truelancer import TruelancerExtractor
                extractor = TruelancerExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
            elif original_source == 'indeed':
                from app.extraction.indeed import IndeedExtractor
                extractor = IndeedExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
            elif original_source == 'linkedin':
                from app.extraction.linkedin import LinkedInExtractor
                extractor = LinkedInExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
            elif original_source == 'freelancer':
                from app.extraction.freelancer import FreelancerExtractor
                extractor = FreelancerExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
            elif original_source == 'yelp':
                from app.extraction.yelp import YelpExtractor
                extractor = YelpExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.extract()
            elif original_source == 'ai_extract':
                from app.ai.pipeline import AIExtractionPipeline
                extractor = AIExtractionPipeline(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                extractor.run_extraction()
            elif original_source == 'both':
                # Sequential fallback with quota sharing across both sources.
                # First check if list type was auto-detected (already ran above)
                # Then run GoogleMaps + LinkedIn
                from app.extraction.google_maps import GoogleMapsExtractor
                from app.extraction.linkedin import LinkedInExtractor

                task = ExtractionTask.query.get(task_id)
                original_max = int(task.max_results or 0) if task else 0

                ex1 = GoogleMapsExtractor(
                    task_id,
                    stop_event=stop_event,
                    ai_options=ai_options,
                )
                ex1.extract()

                if not stop_event.is_set():
                    task = ExtractionTask.query.get(task_id)
                    if task and task.status not in ('failed', 'stopped'):
                        current_total = Business.query.filter_by(task_id=task_id).count()
                        remaining = max(0, original_max - current_total)

                        if remaining > 0:
                            # Temporarily reduce max_results so source-2 only fills the gap.
                            task.max_results = remaining
                            db.session.commit()

                            ex2 = LinkedInExtractor(
                                task_id,
                                stop_event=stop_event,
                                ai_options=ai_options,
                            )
                            ex2.extract()

                        # Restore configured max_results and sync final count.
                        task = ExtractionTask.query.get(task_id)
                        if task:
                            task.max_results = original_max
                            actual_total = Business.query.filter_by(task_id=task_id).count()
                            task.total_records = actual_total
                            if task.status not in ('failed', 'stopped'):
                                task.status = 'completed'
                                task.completed_at = datetime.utcnow()
                            db.session.commit()

            # Keep persisted record count aligned with actual rows.
            task = ExtractionTask.query.get(task_id)
            if task:
                actual_total = Business.query.filter_by(task_id=task_id).count()
                if (task.total_records or 0) != actual_total:
                    task.total_records = actual_total
                    db.session.commit()

            # Global email completion pass (all sources): fill missing emails from websites.
            selected_fields = ai_options.get('selected_fields') or []
            wants_email = (not selected_fields) or ('email' in selected_fields)
            if wants_email:
                _run_email_enrichment_for_task(task_id, ai_options=ai_options)

            # Run email validation pipeline on all records if enabled
            enable_validation = ai_options.get('enable_validation', True)
            if enable_validation:
                _run_email_validation_for_task(task_id)

        except Exception as e:
            traceback.print_exc()
            task = ExtractionTask.query.get(task_id)
            if task and task.status not in ('completed', 'stopped'):
                task.status = 'failed'
                db.session.commit()
            print(f"Background extraction error: {e}")
        finally:
            task_manager.cleanup_task(task_id)


def _run_email_validation_for_task(task_id):
    """Run the email validation pipeline on all businesses for a task."""
    try:
        from app.services.email_validator import validate_email_record
    except ImportError:
        print("[EmailValidation] email_validator service not available, skipping")
        return

    businesses = Business.query.filter_by(task_id=task_id).all()
    validated_count = 0

    for biz in businesses:
        if not biz.email:
            continue

        # Skip if already validated
        if biz.verification_status is not None:
            continue

        llm_score = biz.llm_validity_score or 0.0
        email_type = biz.email_type or 'unknown'

        result = validate_email_record(
            email=biz.email,
            llm_score=llm_score,
            email_type=email_type,
        )

        biz.llm_validity_score = result['llm_validity_score']
        biz.email_type = result['email_type']
        biz.mx_valid = result['mx_valid']
        biz.disposable_domain = result['disposable_domain']
        biz.heuristic_score = result['heuristic_score']
        biz.final_confidence = result['final_confidence']
        biz.verification_status = result['verification_status']
        validated_count += 1

    if validated_count > 0:
        db.session.commit()
        print(f"[EmailValidation] Validated {validated_count} emails for task {task_id}")


def _run_email_enrichment_for_task(task_id, ai_options=None):
    """Backfill missing emails from business websites across all extraction sources."""
    ai_options = ai_options or {}
    candidates = (
        Business.query.filter_by(task_id=task_id)
        .filter(Business.email.is_(None))
        .filter(Business.website.isnot(None))
        .all()
    )

    if not candidates:
        return

    provider = ai_options.get('provider')
    primary_model = ai_options.get('primary_model')
    fallback_model = ai_options.get('fallback_model')

    # Keep this pass efficient; avoid overloading local model/runtime.
    max_ai_fallback = 20
    ai_used = 0

    def _enrich_one(biz):
        nonlocal ai_used

        website = (biz.website or '').strip()
        if not website:
            return None

        email = None
        try:
            scraper = EmailScraper(timeout=8)
            email = scraper.scrape_emails(website)
        except Exception:
            email = None

        if email and validate_email(email):
            return {'id': biz.id, 'email': email.lower()}

        # Optional AI fallback for stubborn websites (capped for performance).
        if ai_used >= max_ai_fallback:
            return None

        try:
            from app.ai.pipeline import extract_from_url
            ai_data = extract_from_url(
                website,
                provider=provider,
                primary_model=primary_model,
                fallback_model=fallback_model,
            )
            emails = ai_data.get('emails') if isinstance(ai_data, dict) else []
            if emails:
                for e in emails:
                    candidate = (e or '').strip().lower()
                    if validate_email(candidate):
                        ai_used += 1
                        return {'id': biz.id, 'email': candidate}
        except Exception:
            return None

        return None

    updated = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_enrich_one, b): b.id for b in candidates}
        for fut in as_completed(futures):
            try:
                res = fut.result(timeout=90)
            except Exception:
                continue

            if not res:
                continue

            biz = Business.query.get(res['id'])
            if not biz or biz.email:
                continue

            biz.email = res['email']
            updated += 1

    if updated > 0:
        db.session.commit()
        print(f"[EmailEnrichment] Backfilled {updated} emails for task {task_id}")


@extraction_bp.route('/stop/<int:task_id>', methods=['POST'])
@login_required
def stop_extraction(task_id):
    """Signal an extraction task to stop."""
    task = ExtractionTask.query.get_or_404(task_id)
    if task.status not in ('running', 'enriching'):
        return jsonify({'message': f'Task is {task.status}, cannot stop'}), 400
        
    if task_manager.stop_task(task_id):
        # We also optimistically update the state here
        task.status = 'stopped'
        db.session.commit()
        return jsonify({'message': 'Task stop signal sent', 'status': 'success'})
    
    return jsonify({'message': 'Task not found in running threads', 'status': 'error'}), 404


@extraction_bp.route('/tasks', methods=['GET'])
@login_required
def list_tasks():
    tasks = ExtractionTask.query.order_by(ExtractionTask.created_at.desc()).all()
    task_counts = _count_businesses_for_tasks([t.id for t in tasks])
    return jsonify([{
        'id': t.id,
        'keyword': t.keyword,
        'location': t.location,
        'source': t.source,
        'status': t.status,
        'total_records': task_counts.get(t.id, 0),
        'selected_fields': _task_selected_fields(t),
        'created_at': t.created_at.isoformat() if t.created_at else None
    } for t in tasks])

@extraction_bp.route('/status/<int:task_id>', methods=['GET'])
@login_required
def get_task_status(task_id):
    task = ExtractionTask.query.get_or_404(task_id)
    actual_total = Business.query.filter_by(task_id=task.id).count()

    # Keep task counter self-healing when old runs wrote stale values.
    if (task.total_records or 0) != actual_total:
        task.total_records = actual_total
        db.session.commit()

    return jsonify({
        'id': task.id,
        'status': task.status,
        'total_records': actual_total,
        'selected_fields': _task_selected_fields(task),
    })

@extraction_bp.route('/tasks/recent', methods=['GET'])
@login_required
def recent_tasks():
    tasks = ExtractionTask.query.order_by(ExtractionTask.created_at.desc()).limit(20).all()
    task_counts = _count_businesses_for_tasks([t.id for t in tasks])
    return jsonify({'tasks': [{
        'id': t.id,
        'keyword': t.keyword,
        'location': t.location,
        'source': t.source,
        'status': t.status,
        'total_records': task_counts.get(t.id, 0),
        'selected_fields': _task_selected_fields(t),
        'created_at': t.created_at.isoformat() if t.created_at else None
    } for t in tasks]})

@extraction_bp.route('/tasks/<int:task_id>', methods=['GET'])
@login_required
def get_task(task_id):
    task = ExtractionTask.query.get_or_404(task_id)
    businesses = [b.to_dict() for b in task.businesses]
    total_records = len(businesses)

    if (task.total_records or 0) != total_records:
        task.total_records = total_records
        db.session.commit()

    return jsonify({
        'id': task.id,
        'keyword': task.keyword,
        'location': task.location,
        'source': task.source,
        'status': task.status,
        'total_records': total_records,
        'selected_fields': _task_selected_fields(task),
        'businesses': businesses
    })

@extraction_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@login_required
def delete_task(task_id):
    task = ExtractionTask.query.get_or_404(task_id)
    try:
        # Delete embeddings first to avoid FK nulling on Business deletion.
        business_ids_subquery = db.session.query(Business.id).filter(Business.task_id == task_id)
        BusinessEmbedding.query.filter(
            BusinessEmbedding.business_id.in_(business_ids_subquery)
        ).delete(synchronize_session=False)

        if task.status in ('running', 'enriching'):
            task_manager.stop_task(task_id)

        db.session.delete(task)
        db.session.commit()
        return jsonify({'message': 'Task deleted'})
    except Exception as exc:
        db.session.rollback()
        return jsonify({'message': f'Failed to delete task: {exc}'}), 500
