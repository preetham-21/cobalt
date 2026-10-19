package cobalt.migrated.custrpt;

import cobalt.migrated.support.LineFiles;
import cobalt.migrated.support.Pic;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * CUSTRPT - customer balance report with region control breaks.
 *
 * <p>Reads CUSTIN (CUSTREC, sorted by region and id) and writes the 80-column
 * report RPTOUT. Closed customers are counted, not printed; a balance above the
 * credit limit is flagged "OVER LIMIT".
 */
public final class Custrpt {
    static final int RECORD_LENGTH = 80;

    /** CUSTREC layout. */
    record Customer(String id, String name, String region, BigDecimal balance,
                    BigDecimal creditLimit, char status) {
        static Customer parse(String r) {
            return new Customer(
                    r.substring(0, 6),
                    r.substring(6, 31),
                    r.substring(31, 33),
                    Pic.signedLeading(r.substring(33, 43), 2),
                    Pic.unsigned(r.substring(43, 52), 2),
                    r.charAt(52));
        }

        boolean closed() {
            return status == 'C';
        }
    }

    private long readCount;
    private long printCount;
    private long closedCount;
    private long overCount;
    private BigDecimal regionTotal = BigDecimal.ZERO;   // S9(9)V99
    private BigDecimal grandTotal = BigDecimal.ZERO;    // S9(11)V99
    private String previousRegion = "  ";
    private boolean first = true;

    public static void main(String[] args) throws IOException {
        System.exit(new Custrpt().run(Path.of("CUSTIN"), Path.of("RPTOUT")));
    }

    int run(Path input, Path output) throws IOException {
        if (!Files.isRegularFile(input)) {
            System.out.println("CUSTRPT: OPEN CUSTIN FAILED, STATUS 35");
            return 16;
        }
        try (LineFiles.Reader in = new LineFiles.Reader(input, RECORD_LENGTH);
             LineFiles.Writer out = new LineFiles.Writer(output)) {
            out.write(Pic.alpha("CUSTRPT", 20) + Pic.alpha("CUSTOMER BALANCE REPORT BY REGION", 40));
            out.write(Pic.alpha("CUST-ID", 8) + Pic.alpha("NAME", 27) + Pic.alpha("RG", 4)
                    + Pic.alpha("      BALANCE", 15) + "FLAG");
            out.write("-".repeat(80));
            while (in.hasNext()) {
                Customer c = Customer.parse(in.next());
                readCount++;
                if (c.closed()) {
                    closedCount++;
                    continue;
                }
                if (!first && !c.region().equals(previousRegion)) {
                    regionBreak(out);
                }
                first = false;
                previousRegion = c.region();
                printDetail(out, c);
            }
            if (!first) {
                regionBreak(out);
            }
            out.write(Pic.alpha("GRAND TOTAL BALANCE", 24) + " ".repeat(10)
                    + Pic.edit(grandTotal, "-ZZ,ZZZ,ZZZ,ZZ9.99"));
            out.write(countLine("RECORDS READ", readCount));
            out.write(countLine("CUSTOMERS PRINTED", printCount));
            out.write(countLine("CLOSED ACCOUNTS SKIPPED", closedCount));
            out.write(countLine("OVER CREDIT LIMIT", overCount));
        }
        return 0;
    }

    private void printDetail(LineFiles.Writer out, Customer c) throws IOException {
        String flag = "";
        if (c.balance().compareTo(c.creditLimit()) > 0) {
            flag = "OVER LIMIT";
            overCount++;
        }
        out.write(c.id() + "  " + c.name() + "  " + c.region() + "  "
                + Pic.edit(c.balance(), "-Z,ZZZ,ZZ9.99") + "  " + flag);
        printCount++;
        regionTotal = Pic.truncate(regionTotal.add(c.balance()), 9, 2);
        grandTotal = Pic.truncate(grandTotal.add(c.balance()), 11, 2);
    }

    private void regionBreak(LineFiles.Writer out) throws IOException {
        out.write(" ".repeat(10) + Pic.alpha("REGION TOTAL", 14) + previousRegion
                + " ".repeat(11) + Pic.edit(regionTotal, "-ZZZ,ZZZ,ZZ9.99"));
        out.write("");
        regionTotal = BigDecimal.ZERO;
    }

    private static String countLine(String label, long count) {
        return Pic.alpha(label, 30) + Pic.edit(BigDecimal.valueOf(count), "ZZZ,ZZ9");
    }
}
