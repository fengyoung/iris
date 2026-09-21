"""会前情报包编排。"""
import hashlib
from datetime import date
from iris.core.exceptions import IrisValueError
from iris.decisions.store import DecisionStore
from iris.intelligence.context import data_root, local_today, settings
from iris.taskpanel.reporter import TaskReporter
from iris.utils.shared import atomic_write_text
from .doc_retriever import RecentDocsRetriever
from .topic_resolver import TopicResolver
from .person_retriever import PersonRetriever
from .open_issue_detector import OpenIssueDetector
from .synthesizer import BriefingSynthesizer


def execute(args, bundle):
    from pathlib import Path
    from iris.llm import LLMService
    if not args.topic.strip():
        raise IrisValueError('briefing 需要 --topic')
    meeting_date = date.fromisoformat(args.date) if args.date else local_today()
    days = args.days or 45
    if days < 1:
        raise IrisValueError('--days 必须为正数')
    with TaskReporter('briefing', command='briefing') as reporter:
        reporter.report_phase('retrieve', '检索主题与近期证据', progress=0.2)
        docs = RecentDocsRetriever(bundle)
        recent = docs.retrieve(args.topic, days=days)
        participants = [n.strip() for n in args.participants.replace('，', ',').split(',') if n.strip()]
        context = {'wiki': TopicResolver(bundle).resolve(args.topic), 'recent_documents': recent,
                   'decisions': DecisionStore(data_root(bundle) / 'decisions').list(query=args.topic)[:20],
                   'open_issues': OpenIssueDetector().detect(recent),
                   'participants': PersonRetriever(bundle, docs.retriever).retrieve(participants, args.topic)}
        reporter.report_phase('synthesize', '生成情报包', progress=0.7)
        text = BriefingSynthesizer(LLMService(bundle), settings(bundle)['max_prompt_chars']).synthesize(
            args.topic, meeting_date.isoformat(), context)
        filename = f'{hashlib.sha256(args.topic.encode()).hexdigest()[:12]}-{meeting_date}.md'
        output = Path(args.output) if args.output else data_root(bundle) / 'briefings' / filename
        atomic_write_text(output, text)
        return {'output': str(output), 'briefing': text}
