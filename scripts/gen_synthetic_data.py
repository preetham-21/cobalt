"""Generate the fixed-width input data for the synthetic COBOL corpus.

The output is fully deterministic: a fixed seed drives the random records and
every edge case is listed explicitly. Never hand-edit the generated ``.dat``
files; change this script and regenerate::

    python scripts/gen_synthetic_data.py            # writes corpus/synthetic/data
    python scripts/gen_synthetic_data.py --out DIR  # writes elsewhere (tests)

Record layouts mirror the copybooks in ``corpus/synthetic/copybooks``.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SEED = 42
SYNTHETIC = Path(__file__).resolve().parent.parent / "corpus" / "synthetic"
DEFAULT_OUT = SYNTHETIC / "data"
DEFAULT_CASES = SYNTHETIC / "cases"

FIRST_NAMES = [
    "ADA",
    "GRACE",
    "ALAN",
    "EDSGER",
    "BARBARA",
    "DONALD",
    "FRANCES",
    "KEN",
    "MARGARET",
    "NIKLAUS",
    "RADIA",
    "JOHN",
    "JEAN",
    "TONY",
    "LYNN",
    "DENNIS",
]
LAST_NAMES = [
    "LOVELACE",
    "HOPPER",
    "TURING",
    "DIJKSTRA",
    "LISKOV",
    "KNUTH",
    "ALLEN",
    "THOMPSON",
    "HAMILTON",
    "WIRTH",
    "PERLMAN",
    "BACKUS",
    "SAMMET",
    "HOARE",
    "CONWAY",
    "RITCHIE",
]


# --------------------------------------------------------------------------- #
# Field formatters (COBOL DISPLAY usage)
# --------------------------------------------------------------------------- #
def _scaled(value: Decimal, decimals: int) -> int:
    scaled = value.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value} has more than {decimals} decimal places")
    return int(scaled)


def unsigned(value: Decimal | int, digits: int, decimals: int = 0) -> str:
    """Format ``PIC 9(digits)V9(decimals)`` (unsigned, implied decimal point)."""
    n = _scaled(Decimal(value), decimals)
    if n < 0:
        raise ValueError(f"negative value {value} for unsigned field")
    text = f"{n:0{digits + decimals}d}"
    if len(text) != digits + decimals:
        raise ValueError(f"{value} does not fit 9({digits})V9({decimals})")
    return text


def signed_leading(value: Decimal | int, digits: int, decimals: int = 0) -> str:
    """Format ``PIC S9(digits)V9(decimals) SIGN LEADING SEPARATE``."""
    value = Decimal(value)
    sign = "-" if value < 0 else "+"
    return sign + unsigned(abs(value), digits, decimals)


def alpha(text: str, width: int) -> str:
    """Format ``PIC X(width)``: left-justified, space-padded, never truncated."""
    if len(text) > width:
        raise ValueError(f"{text!r} longer than X({width})")
    return text.ljust(width)


def cents(rng: random.Random, low: int, high: int) -> Decimal:
    """Random money amount in [low, high] with two decimals."""
    return Decimal(rng.randint(low * 100, high * 100)) / 100


def person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


# --------------------------------------------------------------------------- #
# CUSTOMERS (CUSTREC, 80 bytes) - sorted by region, id
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Customer:
    cust_id: int
    name: str
    region: str
    balance: Decimal
    limit: Decimal
    status: str

    def record(self) -> str:
        rec = (
            unsigned(self.cust_id, 6)
            + alpha(self.name, 25)
            + alpha(self.region, 2)
            + signed_leading(self.balance, 7, 2)
            + unsigned(self.limit, 7, 2)
            + alpha(self.status, 1)
            + " " * 27
        )
        assert len(rec) == 80
        return rec


def customers(rng: random.Random) -> list[Customer]:
    d = Decimal
    rows = [
        # edge cases
        Customer(100001, "FIRST RECORD CLOSED", "MW", d("150.00"), d("1000.00"), "C"),
        Customer(100002, "ZERO BALANCE", "MW", d("0.00"), d("500.00"), "A"),
        Customer(100003, "ONE CENT CREDIT", "MW", d("-0.01"), d("500.00"), "A"),
        Customer(200001, "MAX BALANCE AT LIMIT", "NE", d("9999999.99"), d("9999999.99"), "A"),
        Customer(200002, "ONE CENT OVER LIMIT", "NE", d("2500.01"), d("2500.00"), "A"),
        Customer(200003, "MAX CREDIT BALANCE", "NE", d("-9999999.99"), d("0.00"), "A"),
        Customer(300001, "CLOSED AT REGION START", "NW", d("75.50"), d("100.00"), "C"),
        Customer(500001, "ALL CLOSED REGION A", "SW", d("10.00"), d("100.00"), "C"),
        Customer(500002, "ALL CLOSED REGION B", "SW", d("20.00"), d("100.00"), "C"),
    ]
    used = {c.cust_id for c in rows}
    for region, base in (("MW", 100000), ("NE", 200000), ("NW", 300000), ("SE", 400000)):
        for _ in range(7):
            cust_id = base + rng.randint(10, 999)
            while cust_id in used:
                cust_id += 1
            used.add(cust_id)
            limit = Decimal(rng.choice([500, 1000, 2500, 5000, 10000, 25000]))
            balance = cents(rng, -2000, 30000)
            status = "C" if rng.random() < 0.12 else "A"
            rows.append(Customer(cust_id, person(rng), region, balance, limit, status))
    return sorted(rows, key=lambda c: (c.region, c.cust_id))


# --------------------------------------------------------------------------- #
# ACCOUNTS (ACCTREC, 40 bytes) - sorted by id
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Account:
    acct_id: int
    acct_type: str
    balance: Decimal
    open_date: int
    status: str

    def record(self) -> str:
        rec = (
            unsigned(self.acct_id, 8)
            + alpha(self.acct_type, 1)
            + signed_leading(self.balance, 9, 2)
            + unsigned(self.open_date, 8)
            + alpha(self.status, 1)
            + " " * 10
        )
        assert len(rec) == 40
        return rec


def accounts(rng: random.Random) -> list[Account]:
    d = Decimal
    rows = [
        # savings tier boundaries (999.99 / 1000.00 / 49999.99 / 50000.00)
        Account(10000100, "S", d("999.99"), 20190115, "A"),
        Account(10000200, "S", d("1000.00"), 20190115, "A"),
        Account(10000300, "S", d("49999.99"), 20200301, "A"),
        Account(10000400, "S", d("50000.00"), 20200301, "A"),
        Account(10000500, "S", d("0.00"), 20210610, "A"),
        Account(10000600, "S", d("-50.00"), 20210610, "A"),
        # interest would overflow S9(9)V99
        Account(10000700, "S", d("999999999.99"), 20150101, "A"),
        Account(10000800, "C", d("-1234.56"), 20180820, "A"),
        Account(10000900, "C", d("1200.12"), 20180820, "A"),
        Account(10001000, "L", d("25000.00"), 20230405, "A"),
        # unknown account type
        Account(10001100, "X", d("500.00"), 20230405, "A"),
        # opened in the processing month (2026-10) vs same month last year
        Account(10001200, "S", d("8000.00"), 20261005, "A"),
        Account(10001300, "S", d("8000.00"), 20251015, "A"),
        Account(10001400, "C", d("640.00"), 20200202, "C"),
    ]
    acct_id = 10002000
    for _ in range(18):
        acct_id += rng.randint(50, 400)
        acct_type = rng.choice("SSSCCL")
        low, high = (1000, 400000) if acct_type == "L" else (-500, 120000)
        balance = cents(rng, low, high)
        year = rng.randint(2005, 2026)
        month = rng.randint(1, 9) if year == 2026 else rng.randint(1, 12)
        open_date = year * 10000 + month * 100 + rng.randint(1, 28)
        status = "C" if rng.random() < 0.1 else "A"
        rows.append(Account(acct_id, acct_type, balance, open_date, status))
    return sorted(rows, key=lambda a: a.acct_id)


# --------------------------------------------------------------------------- #
# TRANSACTIONS (TXNREC, 40 bytes) - sorted by account, seq
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Txn:
    acct_id: int
    code: str
    amount: Decimal
    seq: int = 0

    def record(self) -> str:
        rec = (
            unsigned(self.acct_id, 8)
            + alpha(self.code, 2)
            + unsigned(self.amount, 7, 2)
            + unsigned(self.seq, 6)
            + " " * 15
        )
        assert len(rec) == 40
        return rec


def transactions(rng: random.Random, accts: list[Account]) -> list[Txn]:
    d = Decimal
    rows = [
        Txn(9999999, "DP", d("10.00")),  # before the first account
        Txn(10000150, "DP", d("10.00")),  # between two accounts
        Txn(99999999, "DP", d("10.00")),  # after the last account
        Txn(10000700, "DP", d("0.01")),  # overflows max balance
        Txn(10000500, "WD", d("0.01")),  # insufficient funds on zero balance
        Txn(10000900, "WD", d("1200.12")),  # withdraw to exactly zero
        Txn(10000900, "WD", d("0.01")),  # then one cent too far
        Txn(10000800, "FE", d("25.00")),  # fee drives negative further
        Txn(10001000, "WD", d("100.00")),  # withdrawal not allowed on loan
        Txn(10001000, "DP", d("30000.00")),  # loan overpayment goes negative
        Txn(10001100, "XX", d("1.00")),  # invalid transaction code
        Txn(10001400, "DP", d("100.00")),  # closed account
    ]
    for acct in accts[14:]:
        for _ in range(rng.randint(0, 4)):
            code = rng.choice(["DP", "DP", "WD", "WD", "FE"])
            amount = cents(rng, 5, 5000) if code != "FE" else cents(rng, 1, 40)
            rows.append(Txn(acct.acct_id, code, amount))
    # stable sort keeps the intended order within one account
    rows.sort(key=lambda t: t.acct_id)
    return [Txn(t.acct_id, t.code, t.amount, seq) for seq, t in enumerate(rows, start=1)]


# --------------------------------------------------------------------------- #
# PAYROLL (PAYREC, 60 bytes) - header, details, trailer
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Employee:
    emp_id: int
    name: str
    pay_type: str
    rate: Decimal
    hours: Decimal
    tax_code: str
    deduction: Decimal

    def record(self) -> str:
        rec = (
            "D"
            + unsigned(self.emp_id, 6)
            + alpha(self.name, 20)
            + alpha(self.pay_type, 1)
            + unsigned(self.rate, 7, 4)
            + unsigned(self.hours, 3, 2)
            + alpha(self.tax_code, 1)
            + unsigned(self.deduction, 5, 2)
            + " " * 8
        )
        assert len(rec) == 60
        return rec


def payroll(rng: random.Random) -> list[str]:
    d = Decimal
    emps = [
        Employee(700001, "EXACTLY FORTY HOURS", "H", d("18.3750"), d("40.00"), "A", d("0.00")),
        Employee(700002, "ONE CENT OF OVERTIME", "H", d("18.3750"), d("40.01"), "A", d("0.00")),
        Employee(700003, "ZERO HOURS", "H", d("22.0000"), d("0.00"), "B", d("0.00")),
        Employee(700004, "LONG FORTNIGHT", "H", d("31.3333"), d("99.75"), "C", d("150.00")),
        Employee(700005, "EVEN SALARY", "S", d("52000.0000"), d("0.00"), "B", d("100.00")),
        Employee(700006, "REPEATING SALARY", "S", d("100000.0000"), d("0.00"), "C", d("0.00")),
        Employee(700007, "DEDUCTION OVER PAY", "H", d("15.0000"), d("10.00"), "A", d("400.00")),
        Employee(700008, "TAX EXEMPT", "S", d("41600.0000"), d("0.00"), "X", d("0.00")),
        Employee(700009, "BAD PAY TYPE", "Q", d("20.0000"), d("40.00"), "A", d("0.00")),
        Employee(700010, "BAD TAX CODE", "H", d("20.0000"), d("40.00"), "Z", d("0.00")),
    ]
    emp_id = 710000
    for _ in range(14):
        emp_id += rng.randint(1, 90)
        if rng.random() < 0.6:
            rate = Decimal(rng.randint(150000, 950000)) / 10000
            hours = Decimal(rng.randint(0, 8000)) / 100
            emps.append(
                Employee(
                    emp_id, person(rng), "H", rate, hours, rng.choice("ABC"), cents(rng, 0, 120)
                )
            )
        else:
            salary = Decimal(rng.randint(30000, 250000) * 100 + rng.randint(0, 99)) / 100
            emps.append(
                Employee(
                    emp_id,
                    person(rng),
                    "S",
                    salary,
                    d("0.00"),
                    rng.choice("ABCX"),
                    cents(rng, 0, 400),
                )
            )

    header = "H" + unsigned(20260925, 8) + alpha("COBALT SYNTHETIC INDUSTRIES", 30) + " " * 21
    unknown = "Z" + alpha("UNKNOWN RECORD TYPE - MUST BE COUNTED, NOT PAID", 59)
    detail_lines = [e.record() for e in emps]
    detail_lines.insert(5, unknown)
    trailer = "T" + unsigned(len(emps), 6) + unsigned(sum(e.emp_id for e in emps), 12) + " " * 41
    lines = [header, *detail_lines, trailer]
    assert all(len(line) == 60 for line in lines)
    return lines


# --------------------------------------------------------------------------- #
def generate(out_dir: Path) -> dict[str, Path]:
    """Write every synthetic data file into ``out_dir`` and return their paths."""
    rng = random.Random(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)
    accts = accounts(rng)
    files = {
        "customers.dat": [c.record() for c in customers(rng)],
        "accounts.dat": [a.record() for a in accts],
        "transactions.dat": [t.record() for t in transactions(rng, accts)],
        "payroll.dat": payroll(rng),
    }
    written: dict[str, Path] = {}
    for name, lines in files.items():
        path = out_dir / name
        with path.open("w", encoding="ascii", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        written[name] = path
    return written


def edge_cases(out_root: Path) -> dict[str, Path]:
    """Extra equivalence test cases: ``<PROGRAM>/<case>/<LOGICAL>`` files.

    Inputs a case does not provide fall back to the program's default data.
    """
    d = Decimal
    good = payroll(random.Random(SEED + 1))
    details = [line for line in good if line.startswith("D")]
    acct = Account(10000100, "S", d("999.99"), 20190115, "A").record()
    cases: dict[str, list[str]] = {
        "CUSTRPT/empty/CUSTIN": [],
        "CUSTRPT/all-closed/CUSTIN": [
            Customer(900001, "ONLY CLOSED", "NE", d("5.00"), d("10.00"), "C").record()
        ],
        "INTCALC/empty/ACCTIN": [],
        "TXNPOST/no-transactions/TXNIN": [],
        "TXNPOST/no-masters/ACCTIN": [],
        "TXNPOST/single-account/ACCTIN": [acct],
        "PAYROLL/no-header/PAYIN": details[:3],
        "PAYROLL/no-trailer/PAYIN": good[:-1],
        "PAYROLL/bad-hash/PAYIN": [*good[:-1], good[-1][:7] + "000000000001" + good[-1][19:]],
        "PAYROLL/header-only/PAYIN": good[:1],
    }
    written = {}
    for rel, lines in cases.items():
        path = out_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="ascii", newline="\n") as fh:
            fh.write("".join(line + "\n" for line in lines))
        written[rel] = path
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="edge-case directory")
    args = parser.parse_args()
    for name, path in generate(args.out).items():
        count = sum(1 for _ in path.open(encoding="ascii"))
        print(f"{name:<18} {count:>4} records  -> {path}")
    for rel, path in edge_cases(args.cases).items():
        count = sum(1 for _ in path.open(encoding="ascii"))
        print(f"case {rel:<32} {count:>4} records")


if __name__ == "__main__":
    main()
