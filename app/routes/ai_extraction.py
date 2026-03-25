"""
AI Extraction Routes — API endpoints for AI-powered extraction,
semantic search, and pipeline status.
"""

import json
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required
from app import db

ai_bp = Blueprint('ai', __name__)


@ai_bp.route('/status', methods=['GET'])
@login_required
def ai_status():
    """Check availability of all AI services."""
    from app.ai.smart_crawler import CRAWL4AI_AVAILABLE
    from app.ai.content_cleaner import TRAFILATURA_AVAILABLE, HTML_TEXT_AVAILABLE
    from app.ai.llm_extractor import ONPREM_AVAILABLE, LANGEXTRACT_AVAILABLE
    from app.ai.semantic_search import SBERT_AVAILABLE

    ai_enabled = current_app.config.get('AI_EXTRACTION_ENABLED', False)

    return jsonify({
        'ai_enabled': ai_enabled,
        'services': {
            'crawl4ai': CRAWL4AI_AVAILABLE,
            'trafilatura': TRAFILATURA_AVAILABLE,
            'html_text': HTML_TEXT_AVAILABLE,
            'langextract': LANGEXTRACT_AVAILABLE,
            'onprem_llm': ONPREM_AVAILABLE,
            'sentence_transformers': SBERT_AVAILABLE,
        },
        'provider': current_app.config.get('AI_LLM_PROVIDER', 'auto'),
        'primary_model': current_app.config.get('AI_PRIMARY_MODEL', ''),
        'fallback_model': current_app.config.get('AI_FALLBACK_MODEL', ''),
        'api_base_url': current_app.config.get('AI_LLM_API_BASE_URL', ''),
        'api_keys_disabled': current_app.config.get('AI_DISABLE_API_KEYS', True),
        'model_configured': bool(current_app.config.get('AI_PRIMARY_MODEL')),
    })


@ai_bp.route('/extract', methods=['POST'])
@login_required
def ai_extract_url():
    """One-shot AI extraction from a single URL (no task creation)."""
    data = request.json or {}
    url = data.get('url', '').strip()
    provider = (data.get('ai_provider') or data.get('provider') or '').strip().lower() or None
    primary_model = (data.get('ai_model') or data.get('primary_model') or '').strip() or None
    fallback_model = (data.get('ai_fallback_model') or data.get('fallback_model') or '').strip() or None

    if not url:
        return jsonify({'error': 'url is required'}), 400

    try:
        from app.ai.pipeline import extract_from_url
        result = extract_from_url(
            url,
            provider=provider,
            primary_model=primary_model,
            fallback_model=fallback_model,
        )
        return jsonify({'status': 'success', 'data': result})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@ai_bp.route('/search', methods=['POST'])
@login_required
def ai_search():
    """Semantic search across extracted businesses."""
    data = request.json or {}
    query = data.get('query', '').strip()
    task_id = data.get('task_id')
    top_k = int(data.get('top_k', 10))

    if not query:
        return jsonify({'error': 'query is required'}), 400

    try:
        from app.ai.semantic_search import SemanticSearchEngine
        engine = SemanticSearchEngine()

        if not engine.is_available:
            return jsonify({'error': 'Semantic search model not available'}), 503

        if task_id:
            results = engine.search_task(int(task_id), query, top_k)
        else:
            # Search across all embeddings
            from app.models import BusinessEmbedding
            all_embs = BusinessEmbedding.query.all()
            embeddings = []
            for row in all_embs:
                try:
                    vec = json.loads(row.embedding)
                    embeddings.append((row.business_id, vec))
                except (json.JSONDecodeError, TypeError):
                    continue
            results = engine.search(query, embeddings, top_k)

        # Fetch full business data for results
        from app.models import Business
        enriched = []
        for r in results:
            biz = Business.query.get(r['business_id'])
            if biz:
                biz_dict = biz.to_dict()
                biz_dict['relevance_score'] = r['score']
                enriched.append(biz_dict)

        return jsonify({'status': 'success', 'results': enriched})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@ai_bp.route('/embed', methods=['POST'])
@login_required
def ai_embed_task():
    """Generate embeddings for all businesses in a task."""
    data = request.json or {}
    task_id = data.get('task_id')

    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    try:
        from app.ai.semantic_search import SemanticSearchEngine
        engine = SemanticSearchEngine()

        if not engine.is_available:
            return jsonify({'error': 'Embedding model not available'}), 503

        count = engine.embed_and_store_for_task(int(task_id))
        return jsonify({
            'status': 'success',
            'message': f'Generated {count} embeddings',
            'embedded_count': count,
        })
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
