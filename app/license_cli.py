"""Install and inspect offline licenses without the web UI."""

import argparse
import json
import sys

from app.core.database_models import db
from app.core.license_service import LicenseError, install_license, serialize_status
from app.setup_database import verify_database_schema


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Video BA Pipe license manager')
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('status', help='show effective license status')
    install_parser = subparsers.add_parser('install', help='install a signed license file')
    install_parser.add_argument('file')
    install_parser.add_argument('--installed-by', default='cli')
    args = parser.parse_args(argv)

    try:
        verify_database_schema()
        if args.command == 'install':
            with open(args.file, 'r', encoding='utf-8') as license_file:
                token = license_file.read()
            install_license(token, installed_by=args.installed_by)
        print(json.dumps(serialize_status(include_details=True), ensure_ascii=False, indent=2))
        return 0
    except (LicenseError, OSError, RuntimeError) as exc:
        code = exc.code if isinstance(exc, LicenseError) else 'license_cli_error'
        print(json.dumps({'success': False, 'code': code, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if not db.is_closed():
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
