"""情报命令 facade 接口。"""
from iris.app.cli.helpers import _emit_output

__all__ = ['INTELLIGENCE_HANDLERS', 'handle_decisions', 'handle_briefing', 'handle_signals', 'handle_okr_evidence', 'handle_okr_check']


def handle_decisions(args, bundle, logger):
    from iris.decisions.cli import execute
    _emit_output('decisions', execute(args, bundle), pretty=args.pretty)
    return 0


INTELLIGENCE_HANDLERS = {'decisions': handle_decisions}


def handle_briefing(args, bundle, logger):
    from iris.briefing.cli import execute
    _emit_output('briefing', execute(args, bundle), pretty=args.pretty)
    return 0


INTELLIGENCE_HANDLERS['briefing'] = handle_briefing


def handle_signals(args, bundle, logger):
    from iris.signals.service import run
    from iris.taskpanel.reporter import TaskReporter
    with TaskReporter('signals', command='signals'):
        result = run(bundle, dry_run=args.dry_run)
    _emit_output('signals', result, pretty=args.pretty)
    return 0 if not result['errors'] and result['delivery']['status'] in {'sent', 'dry_run'} else 1


INTELLIGENCE_HANDLERS['signals'] = handle_signals


def handle_okr_evidence(args, bundle, logger):
    from iris.okr_evidence.service import tag, evidence_context
    from iris.okr_evidence.store import OKREvidenceStore
    from iris.intelligence.context import data_root
    from iris.taskpanel.reporter import TaskReporter
    if args.workspace_action == 'migrate':
        result = {'schema_version': OKREvidenceStore(data_root(bundle) / 'okr_evidence').migrate()['schema_version']}
    elif args.workspace_action == 'tag':
        with TaskReporter('okr-evidence', command='okr-evidence'):
            result = tag(bundle)
    else:
        krs, evidence = evidence_context(bundle)
        result = {'krs': krs, 'evidence': evidence}
    _emit_output('okr-evidence', result, pretty=args.pretty)
    return 0


def handle_okr_check(args, bundle, logger):
    from iris.okr_evidence.service import check
    from iris.taskpanel.reporter import TaskReporter
    from iris.utils.shared import atomic_write_text
    from pathlib import Path
    with TaskReporter('okr-check', command='okr-check'):
        result = check(bundle, days=args.days or 14, kr_filter=args.kr)
        if args.output:
            atomic_write_text(Path(args.output), '\n\n'.join('# ' + r['kr_id'] + '\n' + r['summary'] for r in result['results']))
    _emit_output('okr-check', result, pretty=args.pretty)
    return 0


INTELLIGENCE_HANDLERS.update({'okr-evidence': handle_okr_evidence, 'okr-check': handle_okr_check})
