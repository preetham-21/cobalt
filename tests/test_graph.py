from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest
from typer.testing import CliRunner

from cobalt.cli import app
from cobalt.graph.build import build_corpus_graph, build_graph, load_graph, save_graph
from cobalt.graph.export import export, to_mermaid
from cobalt.graph.parser import parse_program
from cobalt.graph.pic import analyze_pic, storage_size
from cobalt.graph.query import context_for, impact, migration_order, resolve
from cobalt.graph.source import apply_replacing, code_lines, expand, parse_replacing
from conftest import ROOT, SYNTHETIC

COPYBOOKS = [SYNTHETIC / "copybooks"]


def fixed(code: str, indicator: str = " ") -> str:
    return f"000100{indicator}{code}"


# --------------------------------------------------------------------------- #
# PIC analysis
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("pic", "usage", "sep", "size", "category", "digits", "scale"),
    [
        ("X(25)", None, False, 25, "alphanumeric", 0, 0),
        ("9(6)", None, False, 6, "numeric", 6, 0),
        ("S9(7)V99", None, False, 9, "numeric", 9, 2),
        ("S9(7)V99", None, True, 10, "numeric", 9, 2),
        ("S9(9)V99", "COMP-3", False, 6, "numeric", 11, 2),
        ("9V9(6)", "COMP-3", False, 4, "numeric", 7, 6),
        ("S9(4)", "COMP", False, 2, "numeric", 4, 0),
        ("S9(9)", "BINARY", False, 4, "numeric", 9, 0),
        ("S9(18)", "COMP-5", False, 8, "numeric", 18, 0),
        ("-Z,ZZZ,ZZ9.99", None, False, 13, "numeric-edited", 9, 2),
        ("$$$,$$$,$$9.99", None, False, 14, "numeric-edited", 10, 2),
        ("Z,ZZZ,ZZ9.99CR", None, False, 14, "numeric-edited", 9, 2),
        ("9999/99/99", None, False, 10, "numeric-edited", 8, 0),
    ],
)
def test_pic_sizes(pic, usage, sep, size, category, digits, scale) -> None:
    info = analyze_pic(pic, sign_separate=sep)
    assert storage_size(info, usage, sign_separate=sep) == size
    assert (info.category, info.digits, info.scale) == (category, digits, scale)


# --------------------------------------------------------------------------- #
# Source handling
# --------------------------------------------------------------------------- #
def test_code_lines_drop_comments_and_mark_area_a() -> None:
    lines = code_lines(
        "\n".join([fixed("COMMENT", "*"), fixed("PARA-1."), fixed("    MOVE A TO B.")]), "t.cbl"
    )
    assert [(line.text, line.area_a, line.line) for line in lines] == [
        ("PARA-1.", True, 2),
        ("    MOVE A TO B.", False, 3),
    ]


def test_continuation_lines_join_literals() -> None:
    text = "\n".join([fixed("    MOVE 'HELLO WOR"), fixed("    'LD' TO X.", "-")])
    (line,) = code_lines(text, "t.cbl")
    assert "'HELLO WORLD'" in line.text


def test_replacing_pseudo_text_and_leading() -> None:
    pairs = parse_replacing("==:TAG:== BY ==WS== LEADING ==ACCT-== BY ==OLD-ACCT-==")
    assert pairs == (("", ":TAG:", "WS"), ("LEADING", "ACCT-", "OLD-ACCT-"))
    assert apply_replacing("05 :TAG:-X PIC 9.", [pairs[0]]) == "05 WS-X PIC 9."
    assert apply_replacing("05 ACCT-ID PIC 9.", [pairs[1]]) == "05 OLD-ACCT-ID PIC 9."


def test_copy_expansion_attributes_copybook(tmp_path: Path) -> None:
    (tmp_path / "MEMBER.cpy").write_text(fixed("       05  :P:-ID PIC 9(4).") + "\n")
    prog = tmp_path / "P.cbl"
    prog.write_text(
        "\n".join(
            [
                fixed("IDENTIFICATION DIVISION."),
                fixed("PROGRAM-ID. P."),
                fixed("DATA DIVISION."),
                fixed("WORKING-STORAGE SECTION."),
                fixed("01  REC."),
                fixed("    COPY MEMBER REPLACING ==:P:== BY ==CUST==."),
                fixed("    COPY MISSING."),
            ]
        )
        + "\n"
    )
    src = expand(prog, [tmp_path])
    assert [c.name for c in src.copies] == ["MEMBER", "MISSING"]
    assert src.copies[0].resolved and not src.copies[1].resolved
    member_line = next(line for line in src.lines if line.copybook == "MEMBER")
    assert "CUST-ID" in member_line.text
    parsed = parse_program(prog, [tmp_path])
    item = parsed.find_item("CUST-ID")
    assert item is not None and item.copybook == "MEMBER" and item.size == 4
    assert parsed.unresolved_copybooks == ["MISSING"]


# --------------------------------------------------------------------------- #
# Parser on the synthetic corpus
# --------------------------------------------------------------------------- #
def parsed(name: str):
    return parse_program(SYNTHETIC / "src" / f"{name}.cbl", COPYBOOKS)


def test_custrpt_record_layout() -> None:
    p = parsed("CUSTRPT")
    rec = p.find_item("CUSTOMER-RECORD")
    assert rec.size == 80 and rec.copybook == "CUSTREC"
    layout = {c.name: (c.offset, c.size) for c in rec.children}
    assert layout["CUST-BALANCE"] == (33, 10)
    assert layout["CUST-STATUS"] == (52, 1)
    status = p.find_item("CUST-STATUS")
    assert ("CUST-ACTIVE", "'A'") in status.conditions
    assert p.find_item("WS-DETAIL-LINE").size == 80


def test_redefines_share_offsets() -> None:
    rec = parsed("PAYROLL").find_item("PAY-RECORD")
    assert rec.size == 60
    bodies = {c.name: (c.offset, c.size) for c in rec.children}
    assert bodies["PAY-HEADER-BODY"] == bodies["PAY-DETAIL-BODY"] == (1, 59)
    detail = next(c for c in rec.children if c.name == "PAY-DETAIL-BODY")
    assert {c.name: c.offset for c in detail.children}["EMP-RATE"] == 28


def test_files_and_io_modes() -> None:
    p = parsed("TXNPOST")
    assigns = {f.name: (f.assign, f.organization) for f in p.files}
    assert assigns["MASTER-FILE"] == ("ACCTIN", "line-sequential")
    modes = {(o.file, o.mode) for o in p.file_ops if o.verb == "OPEN"}
    assert ("NEW-MASTER-FILE", "OUTPUT") in modes and ("TXN-FILE", "INPUT") in modes
    assert {o.file for o in p.file_ops if o.verb == "WRITE"} == {"NEW-MASTER-FILE", "ERR-FILE"}


def test_paragraphs_and_performs() -> None:
    p = parsed("PAYROLL")
    paras = {q.name: q for q in p.paragraphs}
    assert next(iter(paras)) == "0000-MAIN"
    assert paras["2100-PROCESS-EMPLOYEE"].performs == [
        "2150-VALIDATE",
        "2200-CALC-HOURLY",
        "2300-CALC-NET",
        "2400-WRITE-DETAIL",
        "2900-WRITE-ERROR",
    ]


def test_static_call_with_using_and_linkage() -> None:
    call = parsed("INTCALC").calls[0]
    assert (call.target, call.kind) == ("RATECALC", "static")
    assert call.using == ["ACCT-TYPE", "WS-BALANCE", "WS-RATE", "WS-INTEREST", "WS-RC"]
    rate = parsed("RATECALC")
    assert rate.using[0] == "LK-ACCT-TYPE" and len(rate.using) == 5
    assert rate.find_item("LK-BALANCE").section == "LINKAGE"


def test_perform_thru_and_goto(tmp_path: Path) -> None:
    prog = tmp_path / "G.cbl"
    prog.write_text(
        "\n".join(
            fixed(x)
            for x in [
                "PROGRAM-ID. G.",
                "PROCEDURE DIVISION.",
                "MAIN-PARA.",
                "    PERFORM A-PARA THRU C-PARA",
                "    PERFORM 3 TIMES DISPLAY 'X' END-PERFORM",
                "    GO TO Z-PARA.",
                "A-PARA.",
                "    DISPLAY 'A'.",
                "B-PARA.",
                "    DISPLAY 'B'.",
                "C-PARA.",
                "    EXIT.",
                "Z-PARA.",
                "    STOP RUN.",
            ]
        )
        + "\n"
    )
    main = parse_program(prog, []).paragraphs[0]
    assert main.performs == ["A-PARA", "B-PARA", "C-PARA"]
    assert main.goes_to == ["Z-PARA"]


def test_cics_link_and_dynamic_call(tmp_path: Path) -> None:
    prog = tmp_path / "C.cbl"
    prog.write_text(
        "\n".join(
            fixed(x)
            for x in [
                "PROGRAM-ID. C.",
                "DATA DIVISION.",
                "WORKING-STORAGE SECTION.",
                "01  WS-PGM PIC X(8) VALUE 'DATEUTIL'.",
                "PROCEDURE DIVISION.",
                "MAIN-PARA.",
                "    EXEC CICS LINK PROGRAM('SUBPGM') COMMAREA(X) END-EXEC",
                "    CALL WS-PGM USING BY REFERENCE A B",
                "    GOBACK.",
            ]
        )
        + "\n"
    )
    p = parse_program(prog, [])
    assert {(c.target, c.kind) for c in p.calls} == {
        ("SUBPGM", "cics-link"),
        ("DATEUTIL", "dynamic"),
    }
    assert p.exec_kinds == ["CICS"]


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def graph() -> nx.MultiDiGraph:
    g, _ = build_corpus_graph("synthetic", ROOT / "corpus", ROOT)
    return g


def test_graph_node_and_edge_types(graph) -> None:
    assert graph.nodes["program:INTCALC"]["type"] == "program"
    edges = {(u, v, d["type"]) for u, v, d in graph.edges(data=True)}
    assert ("program:INTCALC", "program:RATECALC", "CALLS") in edges
    assert ("program:TXNPOST", "copybook:TXNREC", "COPIES") in edges
    assert ("program:TXNPOST", "file:ACCTIN", "READS") in edges
    assert ("program:TXNPOST", "file:ACCTNEW", "WRITES") in edges
    assert ("copybook:ACCTREC", "data:ACCTREC:ACCT-ID", "DEFINES") in edges
    assert ("paragraph:CUSTRPT:0000-MAIN", "paragraph:CUSTRPT:1000-INITIALIZE", "PERFORMS") in edges


def test_migration_order_puts_callees_first(graph) -> None:
    order = [(e.program, e.wave) for e in migration_order(graph)]
    names = [n for n, _ in order]
    assert names.index("RATECALC") < names.index("INTCALC")
    assert dict(order)["INTCALC"] == 1
    assert {w for n, w in order if n != "INTCALC"} == {0}


def test_migration_order_collapses_cycles() -> None:
    g = nx.MultiDiGraph(corpus="t")
    for name in ("A", "B", "C"):
        g.add_node(f"program:{name}", type="program", name=name)
    g.add_edge("program:A", "program:B", type="CALLS", kind="static")
    g.add_edge("program:B", "program:A", type="CALLS", kind="static")
    g.add_edge("program:C", "program:A", type="CALLS", kind="static")
    g.add_edge("program:C", "program:B", type="CALLS", kind="cics-xctl")
    entries = {e.program: e for e in migration_order(g)}
    assert entries["A"].cycle == ["B"] and entries["A"].wave == 0
    assert entries["C"].wave == 1


def test_context_and_impact(graph) -> None:
    ctx = context_for(graph, "INTCALC")
    assert [c["name"] for c in ctx["callees"]] == ["RATECALC"]
    assert ctx["callees"][0]["parameters"][0] == "LK-ACCT-TYPE"
    assert ctx["copybooks"][0]["name"] == "ACCTREC"
    assert ctx["copybooks"][0]["used_by"] == ["INTCALC", "TXNPOST"]
    acct = next(f for f in ctx["files"] if f["assign"] == "ACCTIN")
    assert acct["shared_with"] == ["TXNPOST"]
    assert impact(graph, "ACCTREC") == ["INTCALC", "TXNPOST"]
    assert impact(graph, "RATECALC") == ["INTCALC"]
    assert impact(graph, "ACCTIN") == ["INTCALC", "TXNPOST"]
    assert resolve(graph, "custrpt") == "program:CUSTRPT"


def test_save_load_roundtrip(graph, tmp_path: Path) -> None:
    _, programs = build_corpus_graph("synthetic", ROOT / "corpus", ROOT)
    path = save_graph(graph, programs, tmp_path)
    again = load_graph(path)
    assert set(again.nodes) == set(graph.nodes)
    assert again.number_of_edges() == graph.number_of_edges()
    parsed_json = json.loads((tmp_path / "synthetic.parsed.json").read_text())
    assert {p["program_id"] for p in parsed_json} == {
        "CUSTRPT",
        "INTCALC",
        "PAYROLL",
        "RATECALC",
        "TXNPOST",
    }


@pytest.mark.parametrize("fmt", ["json", "graphml", "dot", "mermaid"])
def test_exports(graph, tmp_path: Path, fmt: str) -> None:
    path = export(graph, fmt, tmp_path / f"g.{fmt}")
    text = path.read_text(encoding="utf-8")
    assert "RATECALC" in text


def test_mermaid_is_program_level(graph) -> None:
    text = to_mermaid(graph)
    assert "paragraph" not in text and "|CALLS|" in text


def test_empty_program_graph() -> None:
    assert build_graph([], corpus="empty").number_of_nodes() == 0


def test_graph_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["graph", "order", "--json"])
    assert result.exit_code == 0, result.output
    order = [e["program"] for e in json.loads(result.stdout)]
    assert order[-1] == "INTCALC"
    result = runner.invoke(app, ["graph", "context", "TXNPOST"])
    assert json.loads(result.stdout)["program"] == "TXNPOST"
    result = runner.invoke(app, ["graph", "impact", "TXNREC"])
    assert result.stdout.strip() == "TXNPOST"
    result = runner.invoke(app, ["graph", "export", "-f", "dot", "--out", str(tmp_path / "g.dot")])
    assert result.exit_code == 0 and (tmp_path / "g.dot").is_file()
    result = runner.invoke(app, ["graph", "show", "NOPE"])
    assert result.exit_code == 1


@pytest.mark.skipif(not (ROOT / "corpus" / "carddemo" / "app").is_dir(), reason="no carddemo")
def test_carddemo_graph_smoke() -> None:
    g, programs = build_corpus_graph("carddemo", ROOT / "corpus", ROOT)
    assert len(programs) == 44
    order = {e.program: e for e in migration_order(g)}
    assert order["CBSTM03A"].depends_on == ["CBSTM03B"]
    trn = context_for(g, "CBTRN02C")
    assert {f["assign"] for f in trn["files"]} >= {"DALYTRAN", "ACCTFILE", "XREFFILE"}
    assert any(f["organization"] == "indexed" for f in trn["files"])
