"""
Smoke tests for GovNonprofitExtractor.

Run:
    python -m pytest tests/test_gov_extractor.py -v

Manual smoke run:
    python tests/test_gov_extractor.py
"""

import os
import sys
import pytest

from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.extraction.gov_nonprofit_extractor import GovNonprofitExtractor
from app.models import ExtractionTask, Business


def test_url_normalization_helpers():
    url = GovNonprofitExtractor._normalize_url('https://ElderAffairs.org/contact-us/?x=1#top')
    assert url == 'https://elderaffairs.org/contact-us'

    email = GovNonprofitExtractor._normalize_email('info [at] elderaffairs [dot] org')
    assert email == 'info@elderaffairs.org'


def test_domain_filtering_for_target_domains():
    app = create_app('default')
    with app.app_context():
        task = ExtractionTask(
            keyword='test',
            location='US',
            source='list_crawl',
            status='pending',
        )
        db.session.add(task)
        db.session.commit()

        try:
            ex = GovNonprofitExtractor(
                task_id=task.id,
                seed_urls=['https://elderaffairs.org/contact-us/'],
                target_domains=['elderaffairs.org'],
                follow_links=False,
                max_depth=0,
                ai_options={'enable_validation': False},
            )
        except OperationalError as exc:
            pytest.skip(f'Database schema not migrated for gov fields yet: {exc}')

        assert ex._is_allowed_url('https://elderaffairs.org/programs-and-services') is True
        assert ex._is_allowed_url('https://example.com/contact') is False


def test_manual_smoke_extract_limited():
    """
    Non-strict smoke test: executes a tiny crawl and asserts no crash.
    Uses max_depth=0 to hit only one URL.
    """
    app = create_app('default')
    with app.app_context():
        task = ExtractionTask(
            keyword='Florida Aging Services',
            location='US',
            source='list_crawl',
            status='pending',
        )
        db.session.add(task)
        db.session.commit()

        try:
            ex = GovNonprofitExtractor(
                task_id=task.id,
                seed_urls=['https://elderaffairs.org/contact-us/'],
                target_domains=['elderaffairs.org'],
                follow_links=False,
                max_depth=0,
                ai_options={'enable_validation': True},
            )
            ex.extract()
        except OperationalError as exc:
            pytest.skip(f'Database schema not migrated for gov fields yet: {exc}')

        # We only assert extraction completed without raising and task row exists.
        refreshed = ExtractionTask.query.get(task.id)
        assert refreshed is not None
        assert refreshed.status in ('completed', 'stopped', 'failed')


if __name__ == '__main__':
    app = create_app('default')
    with app.app_context():
        task = ExtractionTask(
            keyword='Florida Aging Services',
            location='US',
            source='list_crawl',
            status='pending',
        )
        db.session.add(task)
        db.session.commit()

        extractor = GovNonprofitExtractor(
            task_id=task.id,
            seed_urls=['https://elderaffairs.org/contact-us/'],
            target_domains=['elderaffairs.org'],
            follow_links=False,
            max_depth=0,
            ai_options={'enable_validation': True},
        )
        extractor.extract()

        rows = Business.query.filter_by(task_id=task.id).limit(10).all()
        print(f"Task {task.id} status: {ExtractionTask.query.get(task.id).status}, records: {len(rows)}")
        for b in rows:
            print({
                'name': b.name,
                'email': b.email,
                'division': b.division,
                'organization_type': b.organization_type,
                'verification_status': b.verification_status,
                'source_url': b.source_url,
                'method': b.extraction_method,
            })
