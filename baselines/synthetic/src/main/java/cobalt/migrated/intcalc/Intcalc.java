package cobalt.migrated.intcalc;

import cobalt.migrated.ratecalc.Ratecalc;
import cobalt.migrated.support.LineFiles;
import cobalt.migrated.support.Pic;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * INTCALC - month-end interest accrual.
 *
 * <p>For every account in ACCTIN (ACCTREC) writes one INTOUT result record and
 * finally a trailer. Accounts opened in the processing month earn nothing
 * (result 04); an interest posting that would overflow S9(9)V99 leaves the
 * balance unchanged (result 12).
 */
public final class Intcalc {
    /** Fixed processing date keeps runs reproducible (WS-RUN-DATE). */
    static final String RUN_DATE = "20261031";

    record Account(String id, char type, BigDecimal balance, String openDate) {
        static Account parse(String r) {
            return new Account(r.substring(0, 8), r.charAt(8),
                    Pic.signedLeading(r.substring(9, 21), 2), r.substring(21, 29));
        }
    }

    public static void main(String[] args) throws IOException {
        System.exit(run(Path.of("ACCTIN"), Path.of("INTOUT")));
    }

    static int run(Path input, Path output) throws IOException {
        if (!Files.isRegularFile(input)) {
            System.out.println("INTCALC: OPEN ACCTIN FAILED, STATUS 35");
            return 16;
        }
        long count = 0;
        long errors = 0;
        BigDecimal totalInterest = BigDecimal.ZERO;
        try (LineFiles.Reader in = new LineFiles.Reader(input, 40);
             LineFiles.Writer out = new LineFiles.Writer(output)) {
            while (in.hasNext()) {
                Account a = Account.parse(in.next());
                count++;
                BigDecimal rate;
                BigDecimal interest;
                int rc;
                if (a.openDate().substring(0, 6).equals(RUN_DATE.substring(0, 6))) {
                    rate = BigDecimal.ZERO;
                    interest = BigDecimal.ZERO;
                    rc = 4;
                } else {
                    Ratecalc.Result r = Ratecalc.calculate(a.type(), a.balance());
                    rate = r.annualRate();
                    interest = r.monthlyInterest();
                    rc = r.resultCode();
                }
                BigDecimal newBalance = a.balance().add(interest);
                if (Pic.sizeError(newBalance, 9, 2)) {
                    newBalance = a.balance();
                    interest = BigDecimal.ZERO;
                    rc = 12;
                }
                if (rc >= 8) {
                    errors++;
                }
                totalInterest = Pic.truncate(totalInterest.add(interest), 11, 2);
                out.write(a.id() + a.type()
                        + Pic.signedLeading(a.balance(), 9, 2)
                        + Pic.digits(rate, 1, 6)
                        + Pic.signedLeading(interest, 9, 2)
                        + Pic.signedLeading(newBalance, 9, 2)
                        + Pic.digits(BigDecimal.valueOf(rc), 2, 0));
            }
            out.write("TRAILER " + Pic.digits(BigDecimal.valueOf(count), 6, 0)
                    + Pic.signedLeading(totalInterest, 11, 2)
                    + Pic.digits(BigDecimal.valueOf(errors), 6, 0));
        }
        return 0;
    }
}
