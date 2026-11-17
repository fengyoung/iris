# Isolated release environment

The 0.1.2 release is built in `.venv-release`, never in Caption's working environment.
The exact installed versions are recorded with the release artifacts; dependency scanning uses OSV and must succeed before publication.

Rebuild from the repository root:

```bash
python3.13 -m venv apps/iris-interview/.venv-release
apps/iris-interview/.venv-release/bin/python -m pip install 'pip>=26.2' 'setuptools>=83'
apps/iris-interview/.venv-release/bin/python -m pip install -e 'apps/iris-interview[asr,dev]' pip-audit
apps/iris-interview/.venv-release/bin/python apps/iris-interview/scripts/build_app.py
```

For an exact replay of a particular build, install its attached `installed-lock.txt` first. The environment lock includes build tools and does not assert that every installed package is bundled. Do not treat the older 0.1.1 archive as dependency-audited.
