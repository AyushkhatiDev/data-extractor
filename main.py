import argparse
import os
import sys

from app import create_app, db
from app.models import ExtractionTask


def run_extractor(task_id, source):
    if source == 'google_maps':
        from app.extraction.google_maps import GoogleMapsExtractor
        GoogleMapsExtractor(task_id).extract()
    elif source == 'indeed':
        from app.extraction.indeed import IndeedExtractor
        IndeedExtractor(task_id).extract()
    elif source == 'linkedin':
        from app.extraction.linkedin import LinkedInExtractor
        LinkedInExtractor(task_id).extract()
    elif source == 'truelancer':
        from app.extraction.truelancer import TruelancerExtractor
        TruelancerExtractor(task_id).extract()
    elif source == 'freelancer':
        from app.extraction.freelancer import FreelancerExtractor
        FreelancerExtractor(task_id).extract()
    elif source == 'yelp':
        from app.extraction.yelp import YelpExtractor
        YelpExtractor(task_id).extract()
    else:
        raise ValueError(f"Unknown source: {source}")


def cmd_extract(args):
    """Run a single extraction task."""
    app = create_app()
    with app.app_context():
        task = ExtractionTask(
            keyword=args.keyword.strip(),
            location=args.location.strip(),
            source=args.source,
            radius=args.radius,
            max_results=args.max_results,
            status='pending'
        )
        db.session.add(task)
        db.session.commit()

        run_extractor(task.id, args.source)


def cmd_update_disposable_domains(args):
    """Download the latest disposable email domain blocklist."""
    import requests

    url = (
        "https://raw.githubusercontent.com/"
        "disposable-email-domains/disposable-email-domains/"
        "master/disposable_email_blocklist.conf"
    )

    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(data_dir, exist_ok=True)
    target_file = os.path.join(data_dir, 'disposable_domains.txt')

    print(f"Downloading disposable domain list from:\n  {url}")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error downloading: {exc}")
        sys.exit(1)

    domains = sorted(set(
        line.strip().lower()
        for line in resp.text.splitlines()
        if line.strip() and not line.strip().startswith('#')
    ))

    with open(target_file, 'w') as f:
        f.write('\n'.join(domains) + '\n')

    print(f"Saved {len(domains)} domains to {target_file}")

    # Reload cache if the validator is importable
    try:
        from app.services.email_validator import reload_disposable_domains
        reload_disposable_domains()
        print("In-memory cache reloaded.")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description='DataExtractor CLI')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # extract command
    extract_parser = subparsers.add_parser('extract', help='Run a single extraction task')
    extract_parser.add_argument('--source', required=True, choices=[
        'google_maps', 'indeed', 'linkedin', 'truelancer', 'freelancer', 'yelp'
    ])
    extract_parser.add_argument('--keyword', required=True)
    extract_parser.add_argument('--location', required=True)
    extract_parser.add_argument('--radius', type=int, default=5000)
    extract_parser.add_argument('--max_results', type=int, default=50)

    # update-disposable-domains command
    subparsers.add_parser(
        'update-disposable-domains',
        help='Download latest disposable email domain blocklist'
    )

    args = parser.parse_args()

    if args.command == 'extract':
        cmd_extract(args)
    elif args.command == 'update-disposable-domains':
        cmd_update_disposable_domains(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
