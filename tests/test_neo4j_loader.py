"""
Neo4j loader logic, tested without a Neo4j server.

A running database is not available in this environment, so the *live* load
path is unverified and the README says so. What is verified here is
everything that doesn't need a server and is where the real bugs live:
that writes are idempotent MERGEs rather than CREATEs, that interpolated
Cypher identifiers are validated before they reach a query string, that
null properties are stripped, and that every BINDS edge carries the
provenance the QC gate checks for.

The fake session records queries instead of executing them - enough to
assert the shape of what would be sent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from target_intel.engine import TargetIntelligenceEngine

_LOADER = Path(__file__).parent.parent / "scripts" / "neo4j" / "load_graph.py"
_spec = importlib.util.spec_from_file_location("load_graph", _LOADER)
load_graph_mod = importlib.util.module_from_spec(_spec)
sys.modules["load_graph"] = load_graph_mod
_spec.loader.exec_module(load_graph_mod)


class FakeSession:
    """Records queries. `single()` returns a canned count so QC can run."""

    def __init__(self, counts=None):
        self.queries: list[tuple[str, dict]] = []
        self._counts = counts or {}

    def run(self, query, **params):
        self.queries.append((query, params))
        session = self

        class _Result:
            def single(self):
                for key, value in session._counts.items():
                    if key in query:
                        return {"c": value}
                return {"c": 0}

        return _Result()


@pytest.fixture(scope="module")
def her2_kg():
    engine = TargetIntelligenceEngine(mock=True)
    kg = engine.get_target_context("comp-her2-human").knowledge_graph
    engine.close()
    return kg


def test_writes_are_merges_not_creates(her2_kg):
    """CREATE would duplicate the whole graph on every re-run."""
    session = FakeSession()
    load_graph_mod.load_graph(session, her2_kg)

    assert session.queries
    for query, _ in session.queries:
        assert "MERGE" in query
        assert "CREATE" not in query


def test_values_are_bound_parameters_not_string_interpolated(her2_kg):
    session = FakeSession()
    load_graph_mod.load_graph(session, her2_kg)

    for query, params in session.queries:
        assert "$" in query  # values arrive as parameters
        assert set(params) <= {"id", "props", "source", "target"}


def test_node_and_edge_counts_match_the_graph(her2_kg):
    session = FakeSession()
    nodes, edges = load_graph_mod.load_graph(session, her2_kg)
    assert nodes == len(her2_kg.nodes)
    assert edges == len(her2_kg.edges)
    assert len(session.queries) == nodes + edges


def test_every_binds_edge_carries_pmid_provenance(her2_kg):
    """An edge with no source is an unciteable claim."""
    session = FakeSession()
    load_graph_mod.load_graph(session, her2_kg)

    binds = [p for q, p in session.queries if ":BINDS" in q]
    assert binds
    for params in binds:
        assert params["props"]["pmids"]


def test_unsafe_labels_are_rejected_before_reaching_cypher():
    """Labels can't be parameterised, so they're an injection surface."""
    for bad in ["Target) DETACH DELETE (n", "has-dash", "1leading", ""]:
        with pytest.raises(ValueError):
            load_graph_mod._check(bad)


def test_safe_labels_pass():
    for good in ["Target", "Epitope", "BINDS", "HAS_EPITOPE", "_private"]:
        assert load_graph_mod._check(good) == good


def test_null_properties_are_stripped():
    """Neo4j rejects nulls; storing the string 'None' would be worse."""
    assert load_graph_mod._clean({"a": 1, "b": None, "c": "x"}) == {"a": 1, "c": "x"}


def test_qc_flags_edges_missing_provenance():
    session = FakeSession(counts={"r.pmids IS NULL": 3})
    assert load_graph_mod.qc_checks(session)["binds_edges_without_provenance"] == 3


def test_qc_passes_when_all_edges_have_provenance():
    session = FakeSession(counts={"r.pmids IS NULL": 0})
    assert load_graph_mod.qc_checks(session)["binds_edges_without_provenance"] == 0
