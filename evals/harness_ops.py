"""In-memory backends for the tools the harness could not execute.

Production Studio tools run against DataProjectService with a real user;
production read_document runs against ReportService. The harness has
neither — which meant it OFFERED neither, and measured a narrower tool
surface than any real turn sees. P16-P18 exist precisely to measure the
Studio loop and the document loop, so the harness needs stand-ins:

* An in-memory project store duck-typing what StudioOps calls on the
  service. The ops layer, validation and — critically — run_query stay the
  REAL code: run_query still posts to the real fontem-api proxies, so what
  is measured is the model driving real queries at a real store; only the
  project persistence is a dict.
* A fixture-backed document, shaped exactly like DocOps.read()'s envelope,
  so a scenario can hand the model a draft to revise.
"""
from __future__ import annotations

import itertools
import json
from types import SimpleNamespace


class InMemoryProjects:
    """The slice of DataProjectService that StudioOps touches."""

    def __init__(self) -> None:
        self._projects: dict[str, SimpleNamespace] = {}
        self._ids = (f"p-{i}" for i in itertools.count(1))
        self._qids = (f"q-{i}" for i in itertools.count(1))
        self._pids = (f"pl-{i}" for i in itertools.count(1))

    async def list_projects(self, _user):
        return list(self._projects.values())

    async def get_project(self, _user, project_id):
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"no project {project_id!r}")
        return project

    async def create_project(self, _user, name, investigation_id=None):
        project = SimpleNamespace(id=next(self._ids), name=name,
                                  investigation_id=investigation_id,
                                  queries=[], plots=[])
        self._projects[project.id] = project
        return project

    async def rename_project(self, user, project_id, name):
        project = await self.get_project(user, project_id)
        project.name = name
        return project

    async def add_query(self, user, project_id, name, lang, query):
        project = await self.get_project(user, project_id)
        q = SimpleNamespace(id=next(self._qids), name=name, lang=lang,
                            query=query)
        project.queries.append(q)
        return q

    async def update_query(self, user, project_id, query_id,
                           name=None, lang=None, query=None):
        project = await self.get_project(user, project_id)
        q = next(x for x in project.queries if x.id == query_id)
        if name is not None:
            q.name = name
        if lang is not None:
            q.lang = lang
        if query is not None:
            q.query = query
        return q

    async def add_plot(self, user, project_id, name, spec):
        project = await self.get_project(user, project_id)
        p = SimpleNamespace(id=next(self._pids), name=name, spec=spec)
        project.plots.append(p)
        return p

    async def update_plot(self, user, project_id, plot_id, name=None, spec=None):
        project = await self.get_project(user, project_id)
        p = next(x for x in project.plots if x.id == plot_id)
        if name is not None:
            p.name = name
        if spec is not None:
            p.spec = spec
        return p


class HarnessDoc:
    """A fixture draft, in DocOps.read()'s exact envelope."""

    def __init__(self, draft: dict) -> None:
        self._draft = draft or {}

    def read_json(self) -> str:
        return json.dumps({
            "report_id": "eval-draft",
            "title": self._draft.get("title", "Untitled draft"),
            "abstract": self._draft.get("abstract"),
            "sections": json.dumps(self._draft.get("sections", [])),
            "note": ("This is the last SAVED version. The user's editor "
                     "buffer may contain newer unsaved text."),
        })
