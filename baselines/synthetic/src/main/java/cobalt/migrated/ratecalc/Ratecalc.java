package cobalt.migrated.ratecalc;

import cobalt.migrated.support.Pic;
import java.math.BigDecimal;

/**
 * RATECALC - tiered interest rate and monthly interest (called subprogram).
 *
 * <p>COBOL passes five parameters BY REFERENCE; in Java the inputs are
 * arguments and the three outputs come back as a {@link Result}.
 */
public final class Ratecalc {
    private Ratecalc() {}

    /** LK-ANNUAL-RATE (9V9(6)), LK-MONTHLY-INTEREST (S9(9)V99), LK-RESULT-CODE (99). */
    public record Result(BigDecimal annualRate, BigDecimal monthlyInterest, int resultCode) {}

    private static final BigDecimal TWELVE = BigDecimal.valueOf(12);

    /**
     * @param accountType LK-ACCT-TYPE: S savings, C checking, L loan
     * @param balance     LK-BALANCE, S9(9)V99
     */
    public static Result calculate(char accountType, BigDecimal balance) {
        BigDecimal rate;
        int resultCode = 0;
        switch (accountType) {
            case 'S' -> rate = savingsTier(balance);
            case 'C' -> rate = new BigDecimal("0.0005");
            case 'L' -> rate = new BigDecimal("0.0699");
            default -> {
                rate = BigDecimal.ZERO;
                resultCode = 8;
            }
        }
        if (balance.signum() < 0 && accountType != 'L') {
            rate = BigDecimal.ZERO;
        }
        rate = Pic.truncate(rate, 1, 6);
        // COMPUTE LK-MONTHLY-INTEREST ROUNDED = LK-BALANCE * LK-ANNUAL-RATE / 12
        BigDecimal interest = Pic.rounded(Pic.divide(balance.multiply(rate), TWELVE), 2);
        return new Result(rate, Pic.truncate(interest, 9, 2), resultCode);
    }

    private static BigDecimal savingsTier(BigDecimal balance) {
        if (balance.compareTo(BigDecimal.valueOf(1000)) < 0) {
            return new BigDecimal("0.0050");
        }
        if (balance.compareTo(BigDecimal.valueOf(50000)) < 0) {
            return new BigDecimal("0.0125");
        }
        return new BigDecimal("0.0215");
    }
}
