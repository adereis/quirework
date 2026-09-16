"""Execute a frozen run with its preserved configuration."""

import json
from pathlib import Path
from quirework.project import Project, project_scope
from quirework.runs.commands import main

record = Path(__file__).resolve().parents[3] / "project.json"
if record.is_file():
    data = json.loads(record.read_text())
    project = Project.from_settings(data["root"], data["settings"])
else:
    project = Project.load()
with project_scope(project):
    raise SystemExit(main())
