"""Derive the CardDemo inputs that its JCL would have built before a step.

* ``data/tranrept-input.txt`` - TRANREPT.jcl's SORT step over the *posted*
  transaction file (CBTRN02C's TRANFILE output, taken from its golden run):
  records with TRAN-PROC-TS within the report's date range, sorted by
  TRAN-CARD-NUM (CVTRA05Y: card number at offset 262, length 16; processed
  timestamp at offset 304). DFSORT is stable, so equal keys keep input order.
* ``data/dateparm.txt`` - the DATEPARM dataset (start and end date).
* ``data/intcalc.parm`` - INTCALC.jcl's ``PARM='2022071800'``.

Run after ``make corpus`` and ``cobalt golden -c carddemo CBTRN02C``:
    python corpus/carddemo-support/prepare.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
START, END = "2022-01-01", "2022-07-06"
CARD = slice(262, 278)
PROC_DATE = slice(304, 314)


def main() -> None:
    out = HERE / "data"
    out.mkdir(exist_ok=True)
    posted = HERE / "golden" / "CBTRN02C" / "default" / "out" / "TRANFILE"
    if not posted.is_file():
        raise SystemExit("capture CBTRN02C golden first: cobalt golden -c carddemo CBTRN02C")
    lines = posted.read_text(encoding="latin-1").splitlines()
    records = [line.ljust(350) for line in lines]
    picked = [r for r in records if START <= r[PROC_DATE] <= END]
    picked.sort(key=lambda r: r[CARD])
    (out / "tranrept-input.txt").write_text(
        "".join(r.rstrip() + "\n" for r in picked), encoding="latin-1", newline="\n"
    )
    (out / "dateparm.txt").write_text(f"{START} {END}\n", encoding="ascii", newline="\n")
    (out / "intcalc.parm").write_text("2022071800\n", encoding="ascii", newline="\n")
    print(f"tranrept-input.txt: {len(picked)} of {len(records)} transactions")


if __name__ == "__main__":
    main()
