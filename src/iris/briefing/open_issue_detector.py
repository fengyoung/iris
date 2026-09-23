"""未闭合事项保守检测：提及不等于关闭。"""
import re


class OpenIssueDetector:
    def detect(self, documents):
        issues = []
        ordered = sorted(documents, key=lambda d: d['date'])
        for index, doc in enumerate(ordered):
            for line in doc['snippet'].splitlines():
                if not re.search(r'待定|后续确认|下次讨论|需要.+跟进', line):
                    continue
                key = re.sub(r'待定|后续确认|下次讨论|需要|跟进|[\W_]', '', line)
                if len(key) < 3:
                    continue
                closed = any(key in sentence and re.search(r'已解决|已关闭|已确认|已完成', sentence)
                             and not re.search(r'未|尚未|没有', sentence)
                             for later in ordered[index + 1:]
                             for sentence in re.split(r'[。！？\n]', later['snippet']))
                if not closed:
                    issues.append({'question': line, 'path': doc['path'], 'date': doc['date'],
                                   'status': '待确认：检索范围内未发现关闭证据'})
        return issues[:15]
