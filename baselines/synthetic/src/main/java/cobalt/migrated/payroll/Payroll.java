package cobalt.migrated.payroll;

import cobalt.migrated.support.LineFiles;
import cobalt.migrated.support.Pic;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * PAYROLL - biweekly payroll register.
 *
 * <p>Input PAYIN is a header, detail records and a trailer (PAYREC, the body
 * REDEFINES per record type). Hourly staff earn 1.5x beyond 40 hours; salaried
 * staff get salary / 26. Gross is ROUNDED; tax is deliberately truncated. The
 * detail count and EMP-ID hash must match the trailer.
 */
public final class Payroll {
    private static final BigDecimal FORTY = BigDecimal.valueOf(40);
    private static final BigDecimal OVERTIME = new BigDecimal("1.5");
    private static final BigDecimal PERIODS = BigDecimal.valueOf(26);

    /** PAY-DETAIL-BODY. */
    record Employee(String id, String name, char payType, BigDecimal rate, BigDecimal hours,
                    char taxCode, BigDecimal deduction) {
        static Employee parse(String r) {
            return new Employee(
                    r.substring(1, 7), r.substring(7, 27), r.charAt(27),
                    Pic.unsigned(r.substring(28, 39), 4),
                    Pic.unsigned(r.substring(39, 44), 2),
                    r.charAt(44),
                    Pic.unsigned(r.substring(45, 52), 2));
        }
    }

    private long detailCount;
    private long paidCount;
    private long errorCount;
    private long unknownCount;
    private long hashTotal;
    private BigDecimal totalGross = BigDecimal.ZERO;
    private BigDecimal totalTax = BigDecimal.ZERO;
    private BigDecimal totalNet = BigDecimal.ZERO;
    private boolean trailerSeen;
    private long expectedCount;
    private long expectedHash;

    public static void main(String[] args) throws IOException {
        System.exit(new Payroll().run(Path.of("PAYIN"), Path.of("PAYRPT")));
    }

    int run(Path input, Path output) throws IOException {
        if (!Files.isRegularFile(input)) {
            System.out.println("PAYROLL: OPEN PAYIN FAILED, STATUS 35");
            return 16;
        }
        int rc = 0;
        try (LineFiles.Reader in = new LineFiles.Reader(input, 60);
             LineFiles.Writer out = new LineFiles.Writer(output)) {
            String header = in.hasNext() ? in.next() : null;
            if (header == null || header.charAt(0) != 'H') {
                System.out.println("PAYROLL: FIRST RECORD IS NOT A HEADER");
                return 16;
            }
            out.write("PAYROLL REGISTER  PERIOD ENDING "
                    + Pic.edit(Pic.unsigned(header.substring(1, 9), 0), "9999/99/99")
                    + "  " + header.substring(9, 39));
            out.write(Pic.alpha("EMP-ID", 7) + Pic.alpha("NAME", 21) + Pic.alpha("T", 2)
                    + "          GROSS" + "        TAX" + "           NET");
            out.write("-".repeat(80));
            while (in.hasNext()) {
                String r = in.next();
                switch (r.charAt(0)) {
                    case 'D' -> processEmployee(out, Employee.parse(r));
                    case 'T' -> {
                        trailerSeen = true;
                        expectedCount = Pic.integer(r.substring(1, 7));
                        expectedHash = Pic.integer(r.substring(7, 19));
                    }
                    default -> unknownCount++;
                }
            }
            out.write("-".repeat(80));
            out.write(totalLine("TOTAL GROSS", totalGross));
            out.write(totalLine("TOTAL TAX", totalTax));
            out.write(totalLine("TOTAL NET", totalNet));
            out.write(countLine("EMPLOYEES PAID", paidCount));
            out.write(countLine("DETAIL RECORDS IN ERROR", errorCount));
            out.write(countLine("UNKNOWN RECORD TYPES", unknownCount));
            if (!trailerSeen) {
                out.write("CONTROL CHECK: TRAILER MISSING");
                rc = 4;
            } else if (detailCount == expectedCount && hashTotal == expectedHash) {
                out.write("CONTROL CHECK: OK");
            } else {
                out.write("CONTROL CHECK: MISMATCH");
                rc = 4;
            }
        }
        return rc;
    }

    private void processEmployee(LineFiles.Writer out, Employee e) throws IOException {
        detailCount++;
        hashTotal = (hashTotal + Pic.integer(e.id())) % 1_000_000_000_000L;   // 9(12)
        String error = null;
        BigDecimal taxRate = BigDecimal.ZERO;
        if (e.payType() != 'H' && e.payType() != 'S') {
            error = "INVALID PAY TYPE";
        } else {
            switch (e.taxCode()) {
                case 'A' -> taxRate = new BigDecimal("0.100");
                case 'B' -> taxRate = new BigDecimal("0.220");
                case 'C' -> taxRate = new BigDecimal("0.300");
                case 'X' -> taxRate = BigDecimal.ZERO;
                default -> error = "INVALID TAX CODE";
            }
        }
        if (error != null) {
            errorCount++;
            out.write(e.id() + " " + e.name() + " " + error);
            return;
        }

        BigDecimal gross;
        if (e.payType() == 'H') {
            BigDecimal regular = e.hours().min(FORTY);
            BigDecimal overtime = e.hours().compareTo(FORTY) > 0
                    ? e.hours().subtract(FORTY) : BigDecimal.ZERO;
            gross = Pic.rounded(regular.multiply(e.rate())
                    .add(overtime.multiply(e.rate()).multiply(OVERTIME)), 2);
        } else {
            gross = Pic.rounded(Pic.divide(e.rate(), PERIODS), 2);
        }
        gross = Pic.truncate(gross, 7, 2);

        BigDecimal taxable = gross.subtract(e.deduction());
        if (taxable.signum() < 0) {
            taxable = BigDecimal.ZERO;
        }
        // no ROUNDED phrase in the COBOL: the tax is truncated to the cent
        BigDecimal tax = taxable.multiply(taxRate).setScale(2, RoundingMode.DOWN);
        BigDecimal net = Pic.truncate(gross.subtract(e.deduction()).subtract(tax), 7, 2);

        paidCount++;
        totalGross = Pic.truncate(totalGross.add(gross), 9, 2);
        totalTax = Pic.truncate(totalTax.add(tax), 9, 2);
        totalNet = Pic.truncate(totalNet.add(net), 9, 2);

        out.write(e.id() + " " + e.name() + " " + e.payType() + " "
                + Pic.edit(gross, "$$$,$$$,$$9.99") + " "
                + Pic.edit(tax, "ZZZ,ZZ9.99") + " "
                + Pic.edit(net, "Z,ZZZ,ZZ9.99CR"));
    }

    private static String totalLine(String label, BigDecimal amount) {
        return Pic.alpha(label, 30) + Pic.edit(amount, "$$$,$$$,$$$,$$9.99CR");
    }

    private static String countLine(String label, long count) {
        return Pic.alpha(label, 30) + Pic.edit(BigDecimal.valueOf(count), "ZZZ,ZZ9");
    }
}
