"""决策命令组。"""
from datetime import timedelta
import json
from iris.core.exceptions import IrisValueError
from iris.intelligence.context import data_root, local_today
from iris.utils.shared import atomic_write_text
from .schema import Decision
from .store import DecisionStore
from .reporter import render_report


def execute(args, bundle):
    store = DecisionStore(data_root(bundle) / 'decisions')
    action = args.workspace_action
    if action in {'list', 'search', 'report'}:
        since = args.since or ((local_today() - timedelta(days=args.days)).isoformat() if args.days else '')
        rows = store.list(status=args.status, owner=args.owner, kr=args.kr, since=since,
                          query=args.subject if action == 'search' else getattr(args, 'query', ''))
        if action == 'report':
            report = render_report(rows)
            if args.output:
                from pathlib import Path
                atomic_write_text(Path(args.output), report)
            return {'report': report, 'count': len(rows)}
        return {'decisions': rows}
    if action == 'show':
        return store.get(args.subject)
    if action == 'update':
        return store.update(args.subject, status=args.status)
    if action == 'pending':
        return {'candidates': store.pending()}
    if action in {'approve', 'reject'}:
        patch = json.loads(args.patch) if args.patch else None
        return store.review(args.subject, approve=action == 'approve', reason=args.reason, patch=patch)
    if action == 'export-wiki':
        from pathlib import Path
        from .wiki import export_pages
        return {'paths': export_pages(store, Path(bundle.wiki['wiki_root']))}
    if action == 'migrate':
        return {'schema_version': store.migrate()['schema_version']}
    if action == 'add':
        if args.input_file:
            from pathlib import Path
            fields = json.loads(Path(args.input_file).read_text(encoding='utf-8'))
        elif args.interactive:
            fields = {'title': input('决策标题：'), 'outcome': input('决定内容：'),
                      'decided_at': input('决策日期 YYYY-MM-DD：')}
        else:
            raise IrisValueError('add 需要 --interactive 或 --input-file')
        fields['review_status'] = 'approved'
        return store.save(Decision(**fields))
    raise IrisValueError('未知 decisions 子命令')
