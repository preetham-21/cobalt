package cobalt.migrated.txnpost;

import cobalt.migrated.support.LineFiles;
import cobalt.migrated.support.Pic;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * TXNPOST - daily transaction posting (balance-line match/merge).
 *
 * <p>Merges the account master ACCTIN with transactions TXNIN (both sorted by
 * account id), writes the updated master ACCTNEW and rejected transactions to
 * TXNERR, and prints control totals.
 */
public final class Txnpost {
    /** Sorts after every real key, like HIGH-VALUES. */
    private static final String HIGH_VALUES = "ÿ".repeat(8);

    private LineFiles.Reader masters;
    private LineFiles.Reader txns;
    private String master;          // current ACCOUNT-RECORD (40 bytes)
    private String txn;             // current TRANSACTION-RECORD (40 bytes)
    private String masterKey;
    private String txnKey;
    private BigDecimal workBalance = BigDecimal.ZERO;  // S9(9)V99

    private long mastersRead;
    private long mastersWritten;
    private long txnsRead;
    private long applied;
    private long rejected;

    public static void main(String[] args) throws IOException {
        System.exit(new Txnpost().run());
    }

    int run() throws IOException {
        if (!Files.isRegularFile(Path.of("ACCTIN"))) {
            System.out.println("TXNPOST: OPEN ACCTIN FAILED, STATUS 35");
            return 16;
        }
        if (!Files.isRegularFile(Path.of("TXNIN"))) {
            System.out.println("TXNPOST: OPEN TXNIN FAILED, STATUS 35");
            return 16;
        }
        try (LineFiles.Reader m = new LineFiles.Reader(Path.of("ACCTIN"), 40);
             LineFiles.Reader t = new LineFiles.Reader(Path.of("TXNIN"), 40);
             LineFiles.Writer newMaster = new LineFiles.Writer(Path.of("ACCTNEW"));
             LineFiles.Writer errors = new LineFiles.Writer(Path.of("TXNERR"))) {
            masters = m;
            txns = t;
            readMaster();
            readTxn();
            while (!(masterKey.equals(HIGH_VALUES) && txnKey.equals(HIGH_VALUES))) {
                int cmp = txnKey.compareTo(masterKey);
                if (cmp < 0) {
                    reject(errors, "NO MATCHING ACCOUNT");
                    readTxn();
                } else if (cmp == 0) {
                    apply(errors);
                    readTxn();
                } else {
                    newMaster.write(master.substring(0, 9) + Pic.signedLeading(workBalance, 9, 2)
                            + master.substring(21));
                    mastersWritten++;
                    readMaster();
                }
            }
        }
        display("TXNPOST MASTERS READ      ", mastersRead);
        display("TXNPOST MASTERS WRITTEN   ", mastersWritten);
        display("TXNPOST TRANSACTIONS READ ", txnsRead);
        display("TXNPOST APPLIED           ", applied);
        display("TXNPOST REJECTED          ", rejected);
        return 0;
    }

    private void apply(LineFiles.Writer errors) throws IOException {
        char type = master.charAt(8);
        boolean closed = master.charAt(29) == 'C';
        boolean loan = type == 'L';
        String code = txn.substring(8, 10);
        BigDecimal amount = Pic.unsigned(txn.substring(10, 19), 2);
        String reason = null;
        if (closed) {
            reason = "ACCOUNT CLOSED";
        } else {
            switch (code) {
                case "DP" -> reason = post(loan ? workBalance.subtract(amount)
                                                : workBalance.add(amount));
                case "WD" -> {
                    if (loan) {
                        reason = "INVALID FOR LOAN";
                    } else if (amount.compareTo(workBalance) > 0) {
                        reason = "INSUFFICIENT FUNDS";
                    } else {
                        workBalance = Pic.truncate(workBalance.subtract(amount), 9, 2);
                    }
                }
                case "FE" -> reason = post(workBalance.subtract(amount));
                default -> reason = "INVALID TXN CODE";
            }
        }
        if (reason == null) {
            applied++;
        } else {
            reject(errors, reason);
        }
    }

    /** ADD/SUBTRACT ... ON SIZE ERROR: keep the old balance on overflow. */
    private String post(BigDecimal result) {
        if (Pic.sizeError(result, 9, 2)) {
            return "BALANCE OVERFLOW";
        }
        workBalance = result;
        return null;
    }

    private void reject(LineFiles.Writer errors, String reason) throws IOException {
        errors.write(txn + Pic.alpha(reason, 20));
        rejected++;
    }

    private void readMaster() {
        if (masters.hasNext()) {
            master = masters.next();
            mastersRead++;
            masterKey = master.substring(0, 8);
            workBalance = Pic.signedLeading(master.substring(9, 21), 2);
        } else {
            masterKey = HIGH_VALUES;
        }
    }

    private void readTxn() {
        if (txns.hasNext()) {
            txn = txns.next();
            txnsRead++;
            txnKey = txn.substring(0, 8);
        } else {
            txnKey = HIGH_VALUES;
        }
    }

    private static void display(String label, long value) {
        System.out.println(label + Pic.edit(BigDecimal.valueOf(value), "ZZZ,ZZ9"));
    }
}
